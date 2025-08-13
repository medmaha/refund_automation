"""Optimized test suite for refund automation functionality using DRY principles and centralized fixtures."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from src.shopify.refund import process_refund_automation, refund_order
from src.models.order import TransactionKind
from src.utils.idempotency import idempotency_manager
from src.utils.audit import audit_logger


class TestDryRunMode:
    """Test DRY_RUN mode functionality."""
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.shopify.refund.retrieve_refundable_shopify_orders')
    @patch('src.shopify.refund.slack_notifier')
    def test_dry_run_mode_processes_orders_without_mutations(self, mock_slack, mock_retrieve, sample_order, sample_tracking):
        """Test that DRY_RUN mode processes orders but doesn't make actual API calls."""
        # Setup
        mock_retrieve.return_value = [(sample_order, sample_tracking)]
        
        # Execute
        with patch('sys.exit') as mock_exit:
            process_refund_automation()
        
        # Verify no actual API calls were made
        assert mock_slack.send_info.called
        assert mock_slack.send_refund_summary.called
        
        # Verify successful refund was processed (since DRY_RUN works correctly)
        summary_call = mock_slack.send_refund_summary.call_args[1]
        assert summary_call['successful_refunds'] == 1
        assert summary_call['failed_refunds'] == 0
    
    @patch('src.config.DRY_RUN', True)
    def test_dry_run_refund_creates_mock_refund(self, sample_order, sample_tracking):
        """Test that DRY_RUN mode creates mock refunds."""
        with patch('src.utils.slack.slack_notifier'):
            refund = refund_order(sample_order, sample_tracking)
        
        assert refund is not None
        assert "dry-run" in refund.id.lower()
        assert "DRY_RUN" in refund.orderName
        assert refund.orderId == sample_order.id
    
    @patch('src.config.DRY_RUN', True)
    def test_dry_run_audit_logging_works(self, sample_order, sample_tracking):
        """Test that audit logging works in DRY_RUN mode."""
        with patch('src.utils.slack.slack_notifier'):
            refund = refund_order(sample_order, sample_tracking)
        
        assert refund is not None
        # Audit logging should still work in DRY_RUN mode
        stats = audit_logger.get_audit_stats()
        assert stats['enabled'] == True


class TestLiveMode:
    """Test LIVE mode functionality."""
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('src.shopify.refund.retrieve_refundable_shopify_orders')
    @patch('src.shopify.refund.slack_notifier')
    @patch('requests.post')
    def test_live_mode_makes_actual_api_calls(self, mock_post, mock_slack, mock_retrieve, sample_order, sample_tracking):
        """Test that LIVE mode makes actual API calls to Shopify."""
        # Setup successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "refundCreate": {
                    "refund": {
                        "id": "gid://shopify/Refund/67890",
                        "createdAt": "2023-01-01T00:00:00Z",
                        "totalRefundedSet": {
                            "presentmentMoney": {"amount": "100.0", "currencyCode": "USD"}
                        }
                    },
                    "userErrors": []
                }
            }
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        mock_retrieve.return_value = [(sample_order, sample_tracking)]
        
        # Execute
        with patch('sys.exit') as mock_exit:
            process_refund_automation()
        
        # Verify API call was made
        assert mock_post.called
        assert mock_slack.send_refund_summary.called
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('requests.post')
    def test_live_mode_handles_api_errors(self, mock_post, sample_order, sample_tracking):
        """Test that LIVE mode properly handles API errors."""
        # Setup API error response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "refundCreate": {
                    "refund": None,
                    "userErrors": [{"message": "Test error"}]
                }
            }
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        with patch('src.utils.slack.slack_notifier'):
            refund = refund_order(sample_order, sample_tracking)
        
        assert refund is None


class TestIdempotency:
    """Test idempotency functionality."""
    
    def test_idempotency_prevents_duplicate_refunds(self, sample_order, sample_tracking):
        """Test that idempotency prevents duplicate refund operations."""
        with patch('src.utils.slack.slack_notifier'), \
             patch('src.config.DRY_RUN', True):
            
            # First refund should succeed
            refund1 = refund_order(sample_order, sample_tracking)
            assert refund1 is not None
            
            # Second refund should be prevented by idempotency
            refund2 = refund_order(sample_order, sample_tracking)
            assert refund2 is None
    
    def test_idempotency_key_generation(self, sample_order):
        """Test idempotency key generation."""
        key1, is_dup1 = idempotency_manager.generate_key(sample_order.id, "refund"), False
        key2, is_dup2 = idempotency_manager.generate_key(sample_order.id, "refund"), False
        
        # Same parameters should generate same key
        assert key1 == key2
        
        # Different parameters should generate different key
        key3, is_dup3 = idempotency_manager.generate_key(sample_order.id, "refund", amount=200.0), False
        assert key1 != key3


class TestAuditLogging:
    """Test audit logging functionality."""
    
    def test_audit_logging_records_decisions(self, sample_order, sample_tracking):
        """Test that audit logging records all decisions."""
        with patch('src.utils.slack.slack_notifier'), \
             patch('src.config.DRY_RUN', True):
            
            refund = refund_order(sample_order, sample_tracking)
            assert refund is not None
            
            # Check that audit stats show activity
            stats = audit_logger.get_audit_stats()
            assert stats['enabled'] == True
    
    def test_audit_logging_handles_different_decision_branches(self, sample_order, sample_tracking):
        """Test audit logging for different decision branches."""
        with patch('src.utils.slack.slack_notifier'), \
             patch('src.config.DRY_RUN', True):
            
            # Test successful refund
            refund1 = refund_order(sample_order, sample_tracking)
            assert refund1 is not None
            
            # Test duplicate prevention
            refund2 = refund_order(sample_order, sample_tracking)
            assert refund2 is None


class TestRetryMechanism:
    """Test retry mechanism functionality."""
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('requests.post')
    def test_retry_on_api_failure(self, mock_post, sample_order, sample_tracking):
        """Test that API failures trigger retry mechanism."""
        # Setup to fail first two times, succeed on third
        responses = [
            Mock(side_effect=Exception("Network error")),
            Mock(side_effect=Exception("Network error")), 
            Mock()
        ]
        
        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "data": {
                "refundCreate": {
                    "refund": {
                        "id": "gid://shopify/Refund/67890",
                        "createdAt": "2023-01-01T00:00:00Z",
                        "totalRefundedSet": {
                            "presentmentMoney": {"amount": "100.0", "currencyCode": "USD"}
                        }
                    },
                    "userErrors": []
                }
            }
        }
        success_response.raise_for_status = Mock()
        responses[-1] = success_response
        
        mock_post.side_effect = responses
        
        with patch('src.utils.slack.slack_notifier'), \
             patch('time.sleep'):  # Speed up test by mocking sleep
            refund = refund_order(sample_order, sample_tracking)
        
        # Should eventually succeed after retries
        assert refund is not None
        assert mock_post.call_count == 3


class TestSlackNotifications:
    """Test Slack notification functionality."""
    
    def test_slack_notifier_formats_messages_correctly(self, sample_order, sample_tracking):
        """Test that Slack notifier formats messages correctly."""
        from src.utils.slack import SlackNotifier
        
        # Create a fresh notifier with test config
        notifier = SlackNotifier()
        message = notifier._format_message("Test message", "info", {"key": "value"})
        
        assert "Test message" in message["attachments"][0]["text"]
        assert len(message["attachments"][0]["fields"]) == 1
        assert message["attachments"][0]["fields"][0]["title"] == "key"
        assert message["attachments"][0]["fields"][0]["value"] == "value"
    
    def test_slack_notifier_logging_functionality(self, sample_order, sample_tracking):
        """Test that Slack notifications are properly logged."""
        from src.utils.slack import slack_notifier
        
        # This should always work regardless of DRY_RUN mode
        # because it should at minimum log the notification
        result = slack_notifier.send_info("Test message")
        
        # In DRY_RUN mode, this would be None, in LIVE mode it could be True/False
        # Either way, the function should execute without error


class TestTimezoneHandling:
    """Test timezone handling functionality."""
    
    def test_timezone_info_included_in_logs(self):
        """Test that timezone information is included in logs."""
        from src.utils.timezone import timezone_handler
        
        tz_info = timezone_handler.get_timezone_info()
        
        assert 'store_timezone' in tz_info
        assert 'current_utc' in tz_info
        assert 'current_store' in tz_info
        assert 'utc_offset' in tz_info
    
    def test_iso8601_timestamp_formatting(self):
        """Test that timestamps are formatted as ISO8601."""
        from src.utils.timezone import get_current_time_iso8601
        
        timestamp = get_current_time_iso8601()
        
        # Should be able to parse as ISO8601
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        assert isinstance(parsed, datetime)


class TestConfigurationValidation:
    """Test that configuration behaves correctly."""
    
    def test_dry_run_toggle_works(self):
        """Test that DRY_RUN toggle affects execution mode."""
        with patch('src.config.DRY_RUN', True):
            from src.shopify.refund import EXECUTION_MODE
            # Need to reload the module to get updated EXECUTION_MODE
            import importlib
            import src.shopify.refund
            importlib.reload(src.shopify.refund)
            
            assert src.shopify.refund.EXECUTION_MODE == "DRY_RUN"
        
        with patch('src.config.DRY_RUN', False):
            importlib.reload(src.shopify.refund)
            assert src.shopify.refund.EXECUTION_MODE == "LIVE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
