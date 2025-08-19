import json
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from src.config import (
    AUTOMATION_ID,
    DRY_RUN,
    SLACK_CHANNEL,
    SLACK_ENABLED,
    SLACK_WEBHOOK_URL,
)
from src.logger import get_logger
from src.utils.retry import exponential_backoff_retry

logger = get_logger(__name__)


class SlackNotifier:
    """Handles Slack notifications for refund automation events."""

    def __init__(self):
        self.webhook_url = SLACK_WEBHOOK_URL
        self.channel = SLACK_CHANNEL
        self.enabled = SLACK_ENABLED and self.webhook_url
        self.automation_id = AUTOMATION_ID
        self.notify_slack_disabled = False

    def _format_message(
        self, message: str, level: str, details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Format message for Slack."""
        mode_indicator = "(DRY-RUN)" if DRY_RUN else "(LIVE)"
        timestamp = datetime.now().isoformat()

        # Color coding based on level
        colors = {
            "info": "#36a64f",  # Green
            "warning": "#ff9500",  # Orange
            "error": "#ff0000",  # Red
            "success": "#36a64f",  # Green
        }

        attachment = {
            "color": colors.get(level, "#808080"),
            "title": f"Refund Automation Alert ({self.automation_id}) {mode_indicator}",
            "text": message,
            "timestamp": timestamp,
            "fields": [],
        }

        if details:
            for key, value in details.items():
                attachment["fields"].append(
                    {"title": key, "value": str(value), "short": len(str(value)) < 30}
                )

        return {
            "channel": self.channel,
            "username": "Refund Automation Bot",
            "icon_emoji": ":robot_face:",
            "attachments": [attachment],
        }

    def __notify_slack_disabled(self):
        if self.notify_slack_disabled:
            return

        self.notify_slack_disabled = True
        logger.debug("Slack notifications disabled, skipping")

    @exponential_backoff_retry(exceptions=(requests.exceptions.RequestException,))
    def _send_to_slack(self, payload: Dict[str, Any]) -> bool:
        """Send payload to Slack webhook."""
        if not self.enabled:
            self.__notify_slack_disabled()
            return False

        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        try:
            response = requests.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Slack notification: {e}")
            raise

    def send_info(self, message: str, details: Optional[Dict[str, Any]] = None):
        """Send info level notification."""
        if self.enabled or DRY_RUN:  # Always log in DRY_RUN mode
            payload = self._format_message(message, "info", details)
            return self._send_to_slack(payload)

    def send_warning(self, message: str, details: Optional[Dict[str, Any]] = None):
        """Send warning level notification."""
        if self.enabled or DRY_RUN:
            payload = self._format_message(message, "warning", details)
            return self._send_to_slack(payload)

    def send_error(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ):
        """Send error level notification with request ID for escalation."""
        error_details = details.copy() if details else {}
        if request_id:
            error_details["Request ID"] = request_id

        if self.enabled or DRY_RUN:
            payload = self._format_message(message, "error", error_details)
            return self._send_to_slack(payload)

    def send_success(self, message: str, details: Optional[Dict[str, Any]] = None):
        """Send success level notification."""
        if self.enabled or DRY_RUN:
            payload = self._format_message(message, "success", details)
            return self._send_to_slack(payload)

    def send_refund_summary(
        self,
        successful_refunds: int,
        failed_refunds: int,
        total_amount: float,
        currency: str,
    ):
        """Send a summary of refund processing."""
        message = f"Refund processing completed: {successful_refunds} successful, {failed_refunds} failed"
        details = {
            "Successful Refunds": successful_refunds,
            "Failed Refunds": failed_refunds,
            "Total Refunded": (
                f"{total_amount:.2f} {currency}" if successful_refunds > 0 else "0"
            ),
            "Mode": "DRY-RUN" if DRY_RUN else "LIVE",
        }

        level = "success" if failed_refunds == 0 else "warning"
        if self.enabled or DRY_RUN:
            payload = self._format_message(message, level, details)
            return self._send_to_slack(payload)


# Global instance
slack_notifier = SlackNotifier()
