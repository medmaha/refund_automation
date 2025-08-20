import hashlib
import json
import os
from typing import Any, Dict, Optional

from src.config import DRY_RUN, IDEMPOTENCY_SAVE_ENABLED
from src.logger import get_logger
from src.utils.timezone import get_current_time_iso8601

logger = get_logger(__name__)

# Create the .logs dir if it does not exist
CACHE_DIR = ".cache"
os.makedirs(CACHE_DIR, exist_ok=True)


class IdempotencyManager:
    """Manages idempotency keys to prevent duplicate operations."""

    def __init__(self, ttl_hours: int = 24):
        self.ttl_hours = ttl_hours
        self._cache: Dict[str, Dict[str, Any]] = {}

        filename = "idempotency.json"
        if DRY_RUN:
            filename = "dry_run." + filename

        self.cache_file = str(os.path.join(CACHE_DIR, filename))

    def initialize(self):
        self._load_cache()
        self._cleanup_expired_entries()

    def _load_cache(self):
        """Load idempotency cache from file."""
        load_cache_data(self)

    def _save_cache(self):
        """Save idempotency cache to file."""
        if IDEMPOTENCY_SAVE_ENABLED:
            save_cache_data(self)
            return

        logger.debug(f"Idempotency saving disabled, cache file: {self.cache_file}")

    def _cleanup_expired_entries(self):
        """Remove expired entries from cache."""
        from datetime import datetime, timedelta

        from src.utils.timezone import timezone_handler

        current_time = timezone_handler.get_current_time_store()
        expired_keys = []

        for key, entry in self._cache.items():
            try:
                entry_time = timezone_handler.to_store_timezone(
                    datetime.fromisoformat(entry["timestamp"])
                )
                expired_time = entry_time + timedelta(hours=self.ttl_hours)

                time_comparison = timezone_handler.compare_times_in_store_tz(
                    current_time, expired_time
                )
                if time_comparison == 1:
                    expired_keys.append(key)

            except (KeyError, ValueError) as e:
                logger.warning(f"Invalid cache entry for key {key}: {e}")
                expired_keys.append(key)
            except Exception as e:
                logger.warning(
                    "Error cleaning cleanup_expired_entries", extra={"error": str(e)}
                )

        if not expired_keys:
            return

        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired idempotency entries")

        # TODO: clarify this decision
        # For now, we just mutate the cache file
        self._save_cache()

    def generate_key(self, order_id: str, operation: str = "refund", **kwargs) -> str:
        """
        Generate idempotency key for an operation.

        Args:
            order_id: Shopify order ID
            operation: Type of operation (refund, etc.)
            **kwargs: Additional parameters to include in key generation

        Returns:
            Hex-encoded idempotency key
        """
        # Create a consistent data structure for hashing
        key_data = {
            "order_id": order_id,
            "operation": operation,
            "params": sorted(kwargs.items()) if kwargs else [],
        }

        # Convert to JSON and hash
        json_str = json.dumps(key_data, sort_keys=True, separators=(",", ":"))
        hash_obj = hashlib.sha256(json_str.encode("utf-8"))
        key = hash_obj.hexdigest()[:16]  # Use first 16 chars for readability

        logger.debug(f"Generated idempotency key: {key} for order: {order_id}")
        return key

    def is_duplicate_operation(self, idempotency_key: str) -> bool:
        """
        Check if an operation has already been performed.

        Args:
            idempotency_key: The idempotency key to check

        Returns:
            True if operation was already performed, False otherwise
        """
        idempotency_key = idempotency_key
        if idempotency_key in self._cache:
            entry = self._cache[idempotency_key]
            logger.info(
                f"Duplicate operation detected for key: {idempotency_key}",
                extra={
                    "idempotency_key": idempotency_key,
                    "original_timestamp": entry.get("timestamp"),
                    "order_id": entry.get("order_id"),
                    "operation": entry.get("operation"),
                },
            )
            return True
        return False

    def mark_operation_completed(
        self, idempotency_key: str, order_id: str, operation: str, result: Any = None
    ):
        """
        Mark an operation as completed.

        Args:
            idempotency_key: The idempotency key
            order_id: Shopify order ID
            operation: Type of operation performed
            result: Optional result of the operation
        """
        entry = {
            "timestamp": get_current_time_iso8601(),
            "ttl_hours": self.ttl_hours,
            "order_id": order_id,
            "operation": operation,
            "dry_run": DRY_RUN,
            "result": result,
        }

        self._cache[idempotency_key] = entry
        self._save_cache()

        logger.info(
            f"Marked operation as completed for key: {idempotency_key}",
            extra={
                "idempotency_key": idempotency_key,
                "order_id": order_id,
                "operation": operation,
                "dry_run": DRY_RUN,
            },
        )

    def get_operation_result(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Get the result of a previously completed operation.

        Args:
            idempotency_key: The idempotency key

        Returns:
            Operation details if found, None otherwise
        """
        return self._cache.get(idempotency_key)

    def invalidate_key(self, idempotency_key: str):
        """
        Invalidate an idempotency key (remove from cache).
        Use with caution - this allows re-running operations.
        """
        if idempotency_key in self._cache:
            del self._cache[idempotency_key]
            self._save_cache()
            logger.warning(f"Invalidated idempotency key: {idempotency_key}")

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the idempotency cache."""
        total_entries = len(self._cache)
        dry_run_entries = sum(
            1 for entry in self._cache.values() if entry.get("dry_run", False)
        )
        live_entries = total_entries - dry_run_entries

        return {
            "total_entries": total_entries,
            "dry_run_entries": dry_run_entries,
            "live_entries": live_entries,
            "cache_file": self.cache_file,
            "ttl_hours": self.ttl_hours,
        }

    def check_operation_idempotency(
        self, order_id: str, operation: str = "refund", **kwargs
    ) -> tuple[str, bool]:
        """
        Check if an operation is idempotent (already performed).

        Returns:
            Tuple of (idempotency_key, is_duplicate)
        """
        key = idempotency_manager.generate_key(order_id, operation, **kwargs)
        is_duplicate = idempotency_manager.get_operation_result(key)
        return key, is_duplicate


def load_cache_data(instance: "IdempotencyManager"):
    """Load idempotency cache from file."""

    try:
        if os.path.exists(instance.cache_file):
            with open(instance.cache_file, "r") as f:
                instance._cache = json.load(f)
        else:
            instance._cache = {}
    except json.decoder.JSONDecodeError as e:
        logger.warning(f"Failed to load idempotency cache: {e}")
        instance._cache = {}

    except Exception as e:
        logger.error(f"Failed to load idempotency cache: {e}")
        instance._cache = {}


def save_cache_data(instance: "IdempotencyManager"):
    """Save idempotency cache to file."""
    try:
        with open(instance.cache_file, "w") as f:
            json.dump(instance._cache, f, indent=2)
        logger.debug(f"Saved idempotency cache with {len(instance._cache)} entries")
    except Exception as e:
        logger.error(f"Failed to save idempotency cache: {e}")


# Global instance
idempotency_manager = IdempotencyManager()
idempotency_manager.initialize()
