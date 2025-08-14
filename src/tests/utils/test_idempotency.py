from datetime import datetime, timedelta
import json
from unittest.mock import patch, mock_open
from src.utils.idempotency import idempotency_manager, IdempotencyManager
from src.tests.fixtures import *


class TestIdempotency:

    @patch("src.utils.idempotency.load_cache_data")
    def test_instantiation(self, mock_load_cache_data):

        # Instantiating this manager should called "_load_cache_data_method"
        manager = IdempotencyManager()

        assert manager.ttl_hours is not None
        assert manager.cache_file is not None
        assert isinstance(manager._cache, dict)

        mock_load_cache_data.assert_not_called()

        manager.initialize()
        mock_load_cache_data.assert_called_once()

    def test_idempotency_key_generation(self, sample_order):

        # Should regenerate same key for the same inputs
        key1 = idempotency_manager.generate_key(sample_order.id, TestConstants.DEFAULT_OPERATION, amount=TestConstants.DEFAULT_AMOUNT)
        key2 = idempotency_manager.generate_key(sample_order.id, TestConstants.DEFAULT_OPERATION, amount=TestConstants.DEFAULT_AMOUNT)
        
        # Same parameters should generate same key
        assert key1 == key2
        
        # Different parameters should generate different key
        key3 = idempotency_manager.generate_key(sample_order.id, TestConstants.DEFAULT_OPERATION, amount=200.0)
        assert key1 != key3

    @patch("src.utils.idempotency.save_cache_data")
    def test_idempotency_is_duplicate_operation(self, mock_save_cache, sample_order):

        # Generate idempotency-key for the order
        key = idempotency_manager.generate_key(sample_order.id, TestConstants.DEFAULT_OPERATION, amount=TestConstants.DEFAULT_AMOUNT)
        
        # Mark an operation as completed to store/cache the key
        idempotency_manager.mark_operation_completed(key, sample_order.id, "refund", result={"test": True})
        
        # Assert the that this key can no longer be operational
        assert idempotency_manager.is_duplicate_operation(key)
   
    @patch("src.utils.idempotency.idempotency_manager._save_cache")
    def test_idempotency_mark_operation_completed(self, mock_save_cache, sample_order):

        # Generate idempotency-key for the order
        key = idempotency_manager.generate_key(sample_order.id, TestConstants.DEFAULT_OPERATION, amount=TestConstants.DEFAULT_AMOUNT)

        # Make a operation as complete to idempotent this ops
        idempotency_manager.mark_operation_completed(key, order_id=sample_order.id, operation=TestConstants.DEFAULT_OPERATION, result={"Test": True})

        # Assert the key was added to cache
        assert key in idempotency_manager._cache

        # Assert that save was called after the cache mutation
        mock_save_cache.assert_called_once()

    def test_get_stats(self):

        # Create a fresh manager to avoid interference
        manager = IdempotencyManager()
        manager._cache.clear()
        
        # Add some test entries
        manager._cache["key1"] = {"dry_run": True, "operation": "refund"}
        manager._cache["key2"] = {"dry_run": False, "operation": "refund"}
        manager._cache["key3"] = {"dry_run": True, "operation": "cancel"}

        stats = manager.get_stats()
        
        assert stats["total_entries"] == 3
        assert stats["dry_run_entries"] == 2
        assert stats["live_entries"] == 1

        assert "cache_file" in stats
        assert stats["ttl_hours"] == manager.ttl_hours

    @patch("src.utils.idempotency.idempotency_manager._save_cache")
    def test_invalidate_key(self, mock_save_cache):

        keep_key = "keep_key"
        test_key = "test_key_to_invalidate"
        
        idempotency_manager._cache[test_key] = {"operation": "refund", "timestamp": "2025-01-01T00:00:00"}
        idempotency_manager._cache[keep_key] = {"operation": "refund", "timestamp": "2025-01-01T00:00:00"}
        
        # Invalidate one key
        idempotency_manager.invalidate_key(test_key)
        
        # Verify key was removed
        assert test_key not in idempotency_manager._cache

        # Verify keep_key wasn't touched
        assert keep_key in idempotency_manager._cache
        
        # Verify save was called
        mock_save_cache.assert_called_once()
        
        # Reset mock for next test
        mock_save_cache.reset_mock()
        
        # Try to invalidate non-existent key (should do nothing)
        idempotency_manager.invalidate_key("non_existent_key")
        
        # Save should not be called for non-existent key
        mock_save_cache.assert_not_called()

    def test_cleanup_expired_entries(self):
        
        # Create test entries with different ages
        now = datetime.now()
        old_time = (now - timedelta(hours=25)).isoformat()  # Expired
        recent_time = (now - timedelta(hours=1)).isoformat()  # Not expired
        invalid_time = "invalid-timestamp"  # Invalid format
        
        idempotency_manager._cache = {
            "expired_key": {"timestamp": old_time, "operation": "refund"},
            "valid_key": {"timestamp": recent_time, "operation": "refund"},
            "invalid_key": {"timestamp": invalid_time, "operation": "refund"},
            "missing_timestamp": {"operation": "refund"}  # Missing timestamp
        }
        
        idempotency_manager._cleanup_expired_entries()
        
        # Only the valid key should remain
        assert "valid_key" in idempotency_manager._cache
        assert "expired_key" not in idempotency_manager._cache
        assert "invalid_key" not in idempotency_manager._cache
        assert "missing_timestamp" not in idempotency_manager._cache
        
        assert len(idempotency_manager._cache) == 1

    @patch('builtins.open', new_callable=mock_open)
    def test_save_cache(self, mock_file):
        """Test successful cache saving to file."""

        test_cache = {"test_key": {"operation": "refund", "timestamp": "2025-01-01T00:00:00"}}
        manager = IdempotencyManager()
        manager._cache = test_cache
        
        manager._save_cache()
        
        # Verify file was opened for writing - look for write calls specifically
        write_calls = [call for call in mock_file.call_args_list if len(call[0]) > 1 and call[0][1] == 'w']
        assert len(write_calls) >= 1
        
        # Verify JSON was written
        handle = mock_file()
        written_data = ''.join(call.args[0] for call in handle.write.call_args_list)
        
        # Parse the written JSON to verify it's correct
        parsed_data = json.loads(written_data)
        assert parsed_data == test_cache

    @patch('builtins.open', new_callable=mock_open, read_data='{"test_key": {"timestamp": "2025-08-14T12:00:00", "operation": "refund"}}')
    def test_load_cache(self, mock_file):

        from src.utils.idempotency import load_cache_data

        # Create a manager and manually test the load function
        manager = IdempotencyManager()
        manager.cache_file = "test_cache.json"  # Set test cache file
        manager.ttl_hours = 24
        
        # Call load_cache_data directly
        load_cache_data(manager)
            
        # Verify file was opened for reading
        mock_file.assert_called_with("test_cache.json", "r")
        
        # Verify cache was loaded
        assert "test_key" in manager._cache
        assert manager._cache["test_key"]["operation"] == "refund"
        assert manager._cache["test_key"]["timestamp"] == "2025-08-14T12:00:00"
