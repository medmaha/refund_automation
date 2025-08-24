"""
Timing validation utilities for refund automation.

Handles delivery timing validation with configurable delay periods and exact hour checking
to meet UAT requirements for 5-day delay and precise timing validations.
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from src.logger import get_logger
from src.models.tracking import TrackingData, TrackingStatus, TrackingSubStatus
from src.utils.timezone import timezone_handler

logger = get_logger(__name__)


class TimingValidationResult(str, Enum):
    """Results of timing validation."""

    ELIGIBLE = "eligible"
    TOO_EARLY = "too_early"
    NO_DELIVERY_TIME = "no_delivery_time"
    INVALID_DELIVERY_TIME = "invalid_delivery_time"


class DeliveryTimingValidator:
    """Validates delivery timing for refund eligibility."""

    def __init__(self, required_delay_hours: int = 120):  # 5 days = 120 hours
        self.required_delay_hours = required_delay_hours
        self.logger = logger

    def validate_delivery_timing(
        self, tracking_data, current_time: Optional[datetime] = None
    ) -> Tuple[TimingValidationResult, dict]:
        """
        Validate if enough time has passed since delivery for refund eligibility.

        Args:
            tracking_data: Tracking data with delivery information
            current_time: Current time to compare against (defaults to now)

        Returns:
            Tuple of (validation_result, details_dict)
        """
        if current_time is None:
            current_time = timezone_handler.get_current_time_store()

        # Extract delivery time from tracking data
        try:
            delivery_time = self._extract_delivery_time(tracking_data)
        except ValueError as value:
            delivery_time = str(value)
        except Exception:
            delivery_time = None

        if delivery_time is None:
            return TimingValidationResult.NO_DELIVERY_TIME, {
                "reason": "No delivery time found in tracking data",
                "tracking_number": getattr(tracking_data, "number", "Unknown"),
                "current_time": timezone_handler.format_iso8601_with_tz(current_time),
            }

        # Validate delivery time format
        if not isinstance(delivery_time, datetime):
            return TimingValidationResult.INVALID_DELIVERY_TIME, {
                "reason": "Invalid delivery time format",
                "delivery_time": str(delivery_time),
                "tracking_number": getattr(tracking_data, "number", "Unknown"),
            }

        # Convert to store timezone for consistent comparison
        delivery_time_store = timezone_handler.to_store_timezone(delivery_time)
        current_time_store = timezone_handler.to_store_timezone(current_time)

        # Calculate time difference in hours
        time_diff = current_time_store - delivery_time_store
        hours_since_delivery = time_diff.total_seconds() / 3600

        # Check if required delay has passed
        is_eligible = hours_since_delivery >= self.required_delay_hours

        hours_since_delivery = abs(hours_since_delivery)

        details = {
            "delivery_time": timezone_handler.format_iso8601_with_tz(
                delivery_time_store
            ),
            "current_time": timezone_handler.format_iso8601_with_tz(current_time_store),
            "hours_since_delivery": round(hours_since_delivery, 2),
            "required_delay_hours": self.required_delay_hours,
            "tracking_number": getattr(tracking_data, "number", "Unknown"),
            "time_remaining_hours": max(
                0, self.required_delay_hours - hours_since_delivery
            ),
        }

        if is_eligible:
            self.logger.info(
                f"Timing validation passed: {(hours_since_delivery):.2f}h >= {self.required_delay_hours}h",
                extra={
                    "tracking_number": details["tracking_number"],
                    "hours_since_delivery": hours_since_delivery,
                    "required_delay_hours": self.required_delay_hours,
                },
            )
            return TimingValidationResult.ELIGIBLE, details
        else:
            self.logger.info(
                f"Timing validation failed: {hours_since_delivery:.2f}h < {self.required_delay_hours}h",
                extra={
                    "tracking_number": details["tracking_number"],
                    "hours_since_delivery": hours_since_delivery,
                    "required_delay_hours": self.required_delay_hours,
                    "time_remaining_hours": details["time_remaining_hours"],
                },
            )
            return TimingValidationResult.TOO_EARLY, details

    def _extract_delivery_time(self, tracking_data: TrackingData) -> Optional[datetime]:
        """Extract delivery time from tracking data."""
        try:
            delivered_at = None
            if tracking_data.track_info.latest_event:
                if (
                    tracking_data.track_info.latest_status.status
                    == TrackingStatus.Delivered
                    and tracking_data.track_info.latest_status.sub_status
                    == TrackingSubStatus.Delivered_Other
                ):
                    delivered_at = tracking_data.track_info.latest_event.time_utc
                    return timezone_handler.parse_shopify_datetime(delivered_at)
            return None
        except Exception as e:
            self.logger.error(
                f"Error extracting delivery time from tracking data: {e}",
                extra={
                    "tracking_number": getattr(tracking_data, "number", "Unknown"),
                    "error": str(e),
                },
            )
            if delivered_at:
                raise ValueError(delivered_at) from e
            raise

    def get_earliest_eligible_time(self, delivery_time: datetime) -> datetime:
        """Calculate the earliest time when a refund becomes eligible."""
        delivery_time_store = timezone_handler.to_store_timezone(delivery_time)
        earliest_eligible = delivery_time_store + timedelta(
            hours=self.required_delay_hours
        )
        return earliest_eligible

    def format_time_remaining(self, time_remaining_hours: float) -> str:
        """Format remaining time in human-readable format."""
        if time_remaining_hours <= 0:
            return "Eligible now"

        days = int(time_remaining_hours // 24)
        hours = int(time_remaining_hours % 24)
        minutes = int((time_remaining_hours % 1) * 60)

        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0 and days == 0:  # Only show minutes if less than a day
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

        if not parts:
            return "Less than 1 minute"

        return ", ".join(parts)


class TimingTestHelper:
    """Helper class for creating test scenarios with specific timing."""

    @staticmethod
    def create_delivery_time_scenario(hours_ago: float) -> datetime:
        """Create a delivery time scenario for testing."""
        current_time = timezone_handler.get_current_time_store()
        delivery_time = current_time - timedelta(hours=hours_ago)
        return delivery_time

    @staticmethod
    def create_exact_timing_scenario(exact_hours: float) -> datetime:
        """Create exact timing scenario (e.g., exactly 120 hours ago)."""
        return TimingTestHelper.create_delivery_time_scenario(exact_hours)

    @staticmethod
    def create_edge_case_scenarios():
        """Create various edge case timing scenarios for testing."""
        scenarios = {
            # B-Time1: Too early (3 days = 72 hours)
            "too_early_3_days": {"hours": 72},
            # B-Time2: Same day but less than 120 hours (5 hours on same day)
            "same_day_too_early": {"hours": 5},
            # B-Time3: Just eligible (5 days + 1 hour = 121 hours)
            "just_eligible": {"hours": 121},
            # Exactly on the edge (120 hours exactly)
            "exact_boundary": {"hours": 120},
            # Well past eligible (7 days)
            "well_past_eligible": {"days": 7},
            # Edge case: just under (119.9 hours)
            "just_under_boundary": {"hours": 119, "minutes": 57},
            # Edge case: just over (120.1 hours)
            "just_over_boundary": {"hours": 120, "minutes": 5},
        }

        return scenarios


# Global instance
delivery_timing_validator = DeliveryTimingValidator()


def validate_refund_timing(
    tracking_data, current_time: Optional[datetime] = None
) -> Tuple[bool, dict]:
    """
    Convenient function to validate refund timing.

    Returns:
        Tuple of (is_eligible, details_dict)
    """
    result, details = delivery_timing_validator.validate_delivery_timing(
        tracking_data, current_time
    )
    is_eligible = result == TimingValidationResult.ELIGIBLE

    return is_eligible, details


def get_timing_validation_message(result: TimingValidationResult, details: dict) -> str:
    """Generate human-readable timing validation message."""
    tracking_number = details.get("tracking_number", "Unknown")

    if result == TimingValidationResult.ELIGIBLE:
        hours_since = details.get("hours_since_delivery", 0)
        return f"Tracking {tracking_number}: Eligible for refund ({hours_since:.1f}h since delivery)"

    elif result == TimingValidationResult.TOO_EARLY:
        time_remaining = details.get("time_remaining_hours", 0)
        formatted_remaining = delivery_timing_validator.format_time_remaining(
            time_remaining
        )
        return f"Tracking {tracking_number}: Too early for refund. Wait {formatted_remaining}"

    elif result == TimingValidationResult.NO_DELIVERY_TIME:
        return f"Tracking {tracking_number}: No delivery time found in tracking data"

    elif result == TimingValidationResult.INVALID_DELIVERY_TIME:
        return f"Tracking {tracking_number}: Invalid delivery time format"

    else:
        return f"Tracking {tracking_number}: Unknown timing validation result"
