import json
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from src.config import AUDIT_LOG_DIR, AUDIT_LOG_ENABLED, DRY_RUN
from src.logger import get_logger
from src.utils.timezone import get_current_time_iso8601

logger = get_logger(__name__)


class AuditEventType(str, Enum):
    """Types of audit events."""

    REFUND_INITIATED = "refund_initiated"
    REFUND_COMPLETED = "refund_completed"
    REFUND_FAILED = "refund_failed"
    ORDER_MATCHED = "order_matched"
    ORDER_UNMATCHED = "order_unmatched"
    ORDER_SKIPPED = "order_skipped"
    DUPLICATE_DETECTED = "duplicate_detected"
    API_REQUEST = "api_request"
    API_RESPONSE = "api_response"
    ERROR_ESCALATED = "error_escalated"


class AuditLogger:
    """Handles audit logging for refund automation operations."""

    def __init__(self, log_dir: str = AUDIT_LOG_DIR):
        self.log_dir = log_dir
        self.enabled = AUDIT_LOG_ENABLED

        if self.enabled:
            os.makedirs(self.log_dir, exist_ok=True)

    def _get_log_filename(self) -> str:
        """Generate audit log filename based on current date."""
        today = datetime.now().strftime("%Y-%m-%d")
        active_filename = f"audit_{today}.json"

        if DRY_RUN:
            active_filename = "dry_run." + active_filename

        return os.path.join(self.log_dir, active_filename)

    def _write_audit_entry(self, entry: Dict[str, Any]):
        """Write audit entry to log file."""
        if not self.enabled:
            return

        log_file = self._get_log_filename()

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                json.dump(entry, f, separators=(",", ":"))
                f.write("\n")
        except Exception as e:
            logger.error(f"Failed to write audit entry: {e}", extra={"entry": entry})

    def log_decision(
        self,
        event_type: AuditEventType,
        order_id: str,
        order_name: str,
        decision_branch: str,
        amounts: Optional[Dict[str, Any]] = None,
        currency: str = "USD",
        references: Optional[Dict[str, str]] = None,
        api_status: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
        """
        Log a decision event with comprehensive audit information.

        Args:
            event_type: Type of audit event
            order_id: Shopify order ID
            order_name: Shopify order name
            decision_branch: The branch taken (matched/unmatched/skipped/etc.)
            amounts: Dictionary of relevant amounts
            currency: Currency code
            references: External references (tracking numbers, etc.)
            api_status: API response status
            idempotency_key: Idempotency key for the operation
            additional_data: Any additional context data
        """
        entry = {
            "timestamp": get_current_time_iso8601(),
            "event_type": event_type.value,
            "order_id": order_id,
            "order_name": order_name,
            "decision_branch": decision_branch,
            "currency": currency,
            "mode": "DRY_RUN" if DRY_RUN else "LIVE",
            "idempotency_key": idempotency_key,
        }

        if amounts:
            entry["amounts"] = amounts

        if references:
            entry["references"] = references

        if api_status:
            entry["api_status"] = api_status

        if additional_data:
            entry.update(additional_data)

        self._write_audit_entry(entry)

    def log_api_interaction(
        self,
        request_type: str,
        endpoint: str,
        order_id: str,
        request_id: Optional[str] = None,
        status_code: Optional[int] = None,
        response_time_ms: Optional[float] = None,
        error: Optional[str] = None,
    ):
        """
        Log API interactions for debugging and monitoring.

        Args:
            request_type: Type of request (POST, GET, etc.)
            endpoint: API endpoint
            order_id: Related order ID
            request_id: Unique request identifier
            status_code: HTTP status code
            response_time_ms: Response time in milliseconds
            error: Error message if applicable
        """
        entry = {
            "timestamp": get_current_time_iso8601(),
            "event_type": AuditEventType.API_REQUEST.value,
            "request_type": request_type,
            "endpoint": endpoint,
            "order_id": order_id,
            "request_id": request_id,
            "mode": "DRY_RUN" if DRY_RUN else "LIVE",
        }

        if status_code:
            entry["status_code"] = status_code

        if response_time_ms:
            entry["response_time_ms"] = response_time_ms

        if error:
            entry["error"] = error

        self._write_audit_entry(entry)

    def log_refund_decision(
        self,
        order_id: str,
        order_name: str,
        refund_amount: float,
        currency: str,
        decision: str,
        tracking_number: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        refund_id: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """
        Log a refund decision with all relevant details.

        Args:
            order_id: Shopify order ID
            order_name: Shopify order name
            refund_amount: Amount to be refunded
            currency: Currency code
            decision: Decision made (processed/skipped/failed)
            tracking_number: Associated tracking number
            idempotency_key: Idempotency key
            refund_id: Shopify refund ID if successful
            error: Error message if failed
        """
        event_type_map = {
            "processed": AuditEventType.REFUND_COMPLETED,
            "failed": AuditEventType.REFUND_FAILED,
            "skipped": AuditEventType.ORDER_SKIPPED,
        }

        event_type = event_type_map.get(decision, AuditEventType.REFUND_INITIATED)

        amounts = {"refund_amount": refund_amount}
        references = {"tracking_number": tracking_number} if tracking_number else {}

        additional_data = {}
        if refund_id:
            additional_data["refund_id"] = refund_id
        if error:
            additional_data["error"] = error

        self.log_decision(
            event_type=event_type,
            order_id=order_id,
            order_name=order_name,
            decision_branch=decision,
            amounts=amounts,
            currency=currency,
            references=references,
            idempotency_key=idempotency_key,
            additional_data=additional_data,
        )

    def log_duplicate_operation(
        self,
        order_id: str,
        order_name: str,
        idempotency_key: str,
        original_timestamp: str,
    ):
        """Log when a duplicate operation is detected."""
        self.log_decision(
            event_type=AuditEventType.DUPLICATE_DETECTED,
            order_id=order_id,
            order_name=order_name,
            decision_branch="duplicate_skipped",
            idempotency_key=idempotency_key,
            additional_data={
                "original_timestamp": original_timestamp,
                "reason": "Operation already completed",
            },
        )

    def get_audit_stats(self) -> Dict[str, Any]:
        """Get statistics about audit logging."""
        if not self.enabled:
            return {"enabled": False}

        stats = {
            "enabled": True,
            "log_dir": self.log_dir,
            "current_log_file": self._get_log_filename(),
        }

        # Try to get file size
        try:
            log_file = self._get_log_filename()
            if os.path.exists(log_file):
                stats["log_file_size_bytes"] = os.path.getsize(log_file)

                # Count lines (entries)
                with open(log_file, "r") as f:
                    stats["total_entries"] = sum(1 for _ in f)
            else:
                stats["log_file_size_bytes"] = 0
                stats["total_entries"] = 0
        except Exception as e:
            stats["error"] = str(e)

        return stats


# Global instance
audit_logger = AuditLogger()


def log_refund_audit(
    order_id: str,
    order_name: str,
    refund_amount: float,
    currency: str,
    decision: str,
    tracking_number: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    refund_id: Optional[str] = None,
    error: Optional[str] = None,
):
    """Convenience function for logging refund decisions."""
    audit_logger.log_refund_decision(
        order_id=order_id,
        order_name=order_name,
        refund_amount=refund_amount,
        currency=currency,
        decision=decision,
        tracking_number=tracking_number,
        idempotency_key=idempotency_key,
        refund_id=refund_id,
        error=error,
    )
