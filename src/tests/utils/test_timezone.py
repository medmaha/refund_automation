from datetime import datetime
from src.tests.fixtures import *


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

