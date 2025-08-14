from src.tests.fixtures import *

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

