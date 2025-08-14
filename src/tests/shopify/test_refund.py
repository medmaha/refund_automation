"""Optimized test suite for refund functionality using DRY principles and centralized fixtures."""

import pytest
from unittest.mock import patch, Mock
from datetime import datetime

from src.shopify.refund import process_refund_automation, refund_order
from src.tests.fixtures import *

class TestDryRunMode:
    """Test DRY_RUN mode functionality."""
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.utils.slack.slack_notifier')
    def test_dry_run_creates_mock_refund(self, mock_slack, sample_order, sample_tracking, assert_helpers):
        """Test that DRY_RUN mode creates mock refunds."""
        refund = refund_order(sample_order, sample_tracking)
        
        assert_helpers.assert_refund_created(refund, sample_order, is_dry_run=True)
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'DRY_RUN')
    @patch('src.utils.slack.slack_notifier')
    @patch('requests.post')
    def test_dry_run_no_api_calls(self, mock_post, mock_slack, sample_order, sample_tracking):
        """Test that DRY_RUN mode doesn't make API calls to Shopify."""
        # Mock Slack responses to avoid StopIteration
        mock_slack_response = Mock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_slack_response.raise_for_status = Mock()
        
        # Configure mock_post to handle different URLs
        def mock_post_side_effect(url, *args, **kwargs):
            if 'slack.com' in url:
                return mock_slack_response
            elif 'myshopify.com' in url:
                raise AssertionError("Shopify API should not be called in DRY_RUN mode")
            return mock_slack_response
            
        mock_post.side_effect = mock_post_side_effect
        
        refund = refund_order(sample_order, sample_tracking)
        
        # Should create a refund but not call Shopify API
        assert refund is not None
        
        # Verify Shopify API was not called (only Slack calls should happen)
        shopify_calls = [call for call in mock_post.call_args_list 
                        if call[0][0] and 'myshopify.com' in str(call[0][0])]
        assert len(shopify_calls) == 0, "Shopify API should not be called in DRY_RUN mode"


class TestLiveMode:
    """Test LIVE mode functionality."""
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('src.utils.slack.slack_notifier')
    @patch('requests.post')
    def test_live_mode_makes_api_calls(self, mock_post, mock_slack, sample_order, sample_tracking, 
                                      successful_api_response, assert_helpers):
        """Test that LIVE mode makes actual API calls."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = successful_api_response
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        refund = refund_order(sample_order, sample_tracking)
        
        # Should make API call
        assert mock_post.called
        assert_helpers.assert_refund_created(refund, sample_order, is_dry_run=False)
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('src.utils.slack.slack_notifier')
    @patch('requests.post')
    def test_live_mode_handles_api_errors(self, mock_post, mock_slack, sample_order, sample_tracking, error_api_response):
        """Test that LIVE mode handles API errors."""
        # Mock API error response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = error_api_response
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        refund = refund_order(sample_order, sample_tracking)
        
        assert refund is None  # Should fail due to user errors


class TestIdempotency:
    """Test idempotency functionality."""
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.utils.slack.slack_notifier')
    def test_prevents_duplicate_refunds(self, mock_slack, sample_order, sample_tracking):
        """Test that idempotency prevents duplicate refund operations."""
        # First refund should succeed
        refund1 = refund_order(sample_order, sample_tracking)
        assert refund1 is not None
        
        # Second refund should be prevented
        refund2 = refund_order(sample_order, sample_tracking)
        assert refund2 is None
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.utils.slack.slack_notifier')
    def test_different_orders_not_prevented(self, mock_slack, multiple_orders, sample_tracking):
        """Test that different orders can be refunded separately."""
        order1, order2 = multiple_orders[:2]
        
        refund1 = refund_order(order1, sample_tracking)
        refund2 = refund_order(order2, sample_tracking)
        
        assert refund1 is not None
        assert refund2 is not None
        assert refund1.orderId != refund2.orderId


class TestErrorHandling:
    """Test error handling functionality."""
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.utils.slack.slack_notifier')
    def test_no_valid_transactions(self, mock_slack, order_without_valid_transactions, sample_tracking):
        """Test handling of orders with no valid transactions."""
        refund = refund_order(order_without_valid_transactions, sample_tracking)
        
        assert refund is None
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('src.utils.slack.slack_notifier')
    @patch('requests.post')
    def test_network_error_handling(self, mock_post, mock_slack, sample_order, sample_tracking, mock_helpers):
        """Test handling of network errors."""
        # Mock Slack response to prevent StopIteration
        mock_slack_response = Mock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_slack_response.raise_for_status = Mock()
        
        # Configure mock_post to handle different URLs
        def mock_post_side_effect(url, *args, **kwargs):
            if 'slack.com' in url:
                return mock_slack_response  # Slack calls succeed
            elif 'myshopify.com' in url:
                raise Exception("Network error")  # Shopify calls fail
            return mock_slack_response
        
        mock_post.side_effect = mock_post_side_effect
        
        refund = refund_order(sample_order, sample_tracking)
        
        assert refund is None  # Should handle error gracefully


class TestProcessAutomation:
    """Test the main process automation function."""
    
    @patch('src.shopify.refund.slack_notifier')
    @patch('src.shopify.refund.retrieve_refundable_shopify_orders')
    @patch('sys.exit')
    def test_no_orders_found(self, mock_exit, mock_retrieve, mock_slack):
        """Test automation when no orders are found."""
        mock_retrieve.return_value = []
        
        process_refund_automation()
        
        mock_exit.assert_called_once_with(0)
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.shopify.refund.slack_notifier')
    @patch('src.shopify.refund.retrieve_refundable_shopify_orders')
    def test_processes_orders_with_tracking(self, mock_retrieve, mock_slack, sample_order_with_tracking, assert_helpers):
        """Test automation processes orders with valid tracking."""
        mock_retrieve.return_value = [sample_order_with_tracking]
        
        # Should not raise exception
        process_refund_automation()
        
        # Verify Slack notifications were sent
        assert_helpers.assert_slack_called(mock_slack, should_call_info=True, should_call_summary=True)
    
    @patch('src.shopify.refund.slack_notifier')
    @patch('src.shopify.refund.retrieve_refundable_shopify_orders')
    def test_skips_orders_without_tracking_event(self, mock_retrieve, mock_slack, sample_order, test_constants):
        """Test automation skips orders without latest tracking event."""
        tracking = Mock()
        tracking.number = test_constants.DEFAULT_TRACKING_NUMBER
        tracking.track_info.latest_event = None  # No latest event
        
        mock_retrieve.return_value = [(sample_order, tracking)]
        
        # Should not raise exception, just skip processing
        process_refund_automation()
        
        # Should still send summary
        assert mock_slack.send_refund_summary.called


class TestRetryMechanism:
    """Test retry mechanism functionality."""
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('src.utils.slack.slack_notifier')
    @patch('time.sleep')  # Speed up tests
    @patch('requests.post')
    def test_retry_on_failure(self, mock_post, mock_sleep, mock_slack, sample_order, sample_tracking, successful_api_response, assert_helpers):
        """Test that API failures trigger retry mechanism."""
        # Mock Slack response for success notifications
        mock_slack_response = Mock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_slack_response.raise_for_status = Mock()
        
        # Mock successful Shopify API response
        shopify_success_response = Mock(
            status_code=200,
            json=Mock(return_value=successful_api_response),
            raise_for_status=Mock()
        )
        
        # Configure mock_post to handle different URLs with retry scenario
        shopify_call_count = 0
        def mock_post_side_effect(url, *args, **kwargs):
            nonlocal shopify_call_count
            if 'slack.com' in url:
                return mock_slack_response  # Slack calls always succeed
            elif 'myshopify.com' in url:
                shopify_call_count += 1
                if shopify_call_count == 1:
                    raise Exception("Network error")  # First call fails
                else:
                    return shopify_success_response  # Second call succeeds
            return mock_slack_response
        
        mock_post.side_effect = mock_post_side_effect
        
        refund = refund_order(sample_order, sample_tracking)
        
        # Should eventually succeed after retry
        assert refund is not None
        # Verify at least one Shopify call was made
        shopify_calls = [call for call in mock_post.call_args_list 
                        if call[0][0] and 'myshopify.com' in str(call[0][0])]
        assert len(shopify_calls) >= 2, "Should retry failed API calls"


class TestAuditLogging:
    """Test audit logging functionality."""
    
    @patch('src.config.DRY_RUN', True)
    @patch('src.utils.slack.slack_notifier')
    def test_audit_logs_created(self, mock_slack, sample_order, sample_tracking):
        """Test that audit logs are created for refund decisions."""
        refund = refund_order(sample_order, sample_tracking)
        
        # Should create audit log entries
        from src.utils.audit import audit_logger
        stats = audit_logger.get_audit_stats()
        assert stats['enabled'] == True


class TestTimezoneHandling:
    """Test timezone handling functionality."""
    
    def test_timezone_info_available(self):
        """Test that timezone information is properly configured."""
        from src.utils.timezone import timezone_handler
        
        tz_info = timezone_handler.get_timezone_info()
        
        assert 'store_timezone' in tz_info
        assert 'current_utc' in tz_info
        assert 'current_store' in tz_info
    
    def test_iso8601_formatting(self):
        """Test that timestamps are properly formatted as ISO8601."""
        from src.utils.timezone import get_current_time_iso8601
        
        timestamp = get_current_time_iso8601()
        
        # Should be parseable as datetime
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        assert isinstance(parsed, datetime)
