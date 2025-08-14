import pytz
from datetime import datetime, timezone
from typing import Optional
from src.config import SHOPIFY_TIMEZONE
from src.logger import get_logger

logger = get_logger(__name__)


class TimezoneHandler:
    """Handles timezone operations for the refund automation system."""
    
    def __init__(self, store_timezone: str = SHOPIFY_TIMEZONE):
        self.store_timezone_str = store_timezone
        try:
            self.store_timezone = pytz.timezone(store_timezone)
        except pytz.UnknownTimeZoneError:
            logger.warning(f"Unknown timezone '{store_timezone}', defaulting to UTC")
            self.store_timezone = pytz.UTC
            self.store_timezone_str = "UTC"
    
    def get_current_time_utc(self) -> datetime:
        """Get current time in UTC."""
        return datetime.now(timezone.utc)
    
    def get_current_time_store(self) -> datetime:
        """Get current time in store timezone."""
        return datetime.now(self.store_timezone)
    
    def to_store_timezone(self, dt: datetime) -> datetime:
        """Convert datetime to store timezone."""
        if dt.tzinfo is None:
            # Assume UTC if no timezone info
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(self.store_timezone)
    
    def to_utc(self, dt: datetime) -> datetime:
        """Convert datetime to UTC."""
        if dt.tzinfo is None:
            # Assume store timezone if no timezone info
            dt = self.store_timezone.localize(dt)
        return dt.astimezone(timezone.utc)
    
    def format_iso8601_with_tz(self, dt: datetime, use_store_tz: bool = True) -> str:
        """
        Format datetime as ISO8601 string with timezone.
        
        Args:
            dt: DateTime to format
            use_store_tz: If True, convert to store timezone first
        
        Returns:
            ISO8601 formatted string with timezone
        """
        if use_store_tz:
            dt = self.to_store_timezone(dt)
        
        return dt.isoformat()
    
    def parse_shopify_datetime(self, dt_str: str) -> datetime:
        """
        Parse Shopify datetime string.
        Shopify typically returns UTC datetimes in ISO format.
        """
        try:
            # Try parsing with timezone info
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            return dt
        except ValueError:
            try:
                # Try parsing without timezone (assume UTC)
                dt = datetime.fromisoformat(dt_str)
                return pytz.UTC.localize(dt)
            except ValueError as e:
                logger.error(f"Failed to parse datetime string: {dt_str}", extra={"error": str(e)})
                raise
    
    def compare_times_in_store_tz(self, dt1: datetime, dt2: datetime) -> int:
        """
        Compare two datetimes in store timezone.
        
        Returns:
            -1 if dt1 < dt2
            0 if dt1 == dt2  
            1 if dt1 > dt2
        """
        dt1_store = self.to_store_timezone(dt1)
        dt2_store = self.to_store_timezone(dt2)
        
        if dt1_store < dt2_store:
            return -1
        elif dt1_store > dt2_store:
            return 1
        else:
            return 0
    
    def get_timezone_info(self) -> dict:
        """Get timezone information for logging."""
        current_utc = self.get_current_time_utc()
        current_store = self.get_current_time_store()
        
        return {
            "store_timezone": self.store_timezone_str,
            "current_utc": self.format_iso8601_with_tz(current_utc, use_store_tz=False),
            "current_store": self.format_iso8601_with_tz(current_store, use_store_tz=True),
            "utc_offset": current_store.strftime("%z")
        }


# Global instance
timezone_handler = TimezoneHandler()


def get_current_time_iso8601() -> str:
    """Get current time as ISO8601 string in store timezone."""
    return timezone_handler.format_iso8601_with_tz(timezone_handler.get_current_time_store())


def get_current_time_utc_iso8601() -> str:
    """Get current time as ISO8601 string in UTC."""
    return timezone_handler.format_iso8601_with_tz(timezone_handler.get_current_time_utc(), use_store_tz=False)


def format_datetime_for_log(dt: datetime) -> str:
    """Format datetime for logging with timezone info."""
    return timezone_handler.format_iso8601_with_tz(dt)
