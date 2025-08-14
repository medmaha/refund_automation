"""Optimized test suite for refund automation functionality using DRY principles and centralized fixtures."""

from unittest.mock import Mock, patch

from src.shopify.refund import  refund_order
from src.tests.fixtures import *

class TestRetryMechanism:
    """Test retry mechanism functionality."""
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('requests.post')
    def test_retry_on_api_failure(self, mock_post, sample_order, sample_tracking):
        """Test that API failures trigger retry mechanism."""
        # Mock Slack response for notifications
        mock_slack_response = Mock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_slack_response.raise_for_status = Mock()
        
        # Mock successful Shopify API response
        shopify_success_response = Mock()
        shopify_success_response.status_code = 200
        shopify_success_response.json.return_value = {
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
        shopify_success_response.raise_for_status = Mock()
        
        # Configure mock_post to handle different URLs with retry scenario
        shopify_call_count = 0
        def mock_post_side_effect(url, *args, **kwargs):
            nonlocal shopify_call_count
            if 'slack.com' in url:
                return mock_slack_response  # Slack calls always succeed
            elif 'myshopify.com' in url:
                shopify_call_count += 1
                if shopify_call_count <= 2:
                    raise Exception("Network error")  # First two calls fail
                else:
                    return shopify_success_response  # Third call succeeds
            return mock_slack_response
        
        mock_post.side_effect = mock_post_side_effect
        
        with patch('src.utils.slack.slack_notifier'), \
             patch('time.sleep'):  # Speed up test by mocking sleep
            refund = refund_order(sample_order, sample_tracking)
        
        # Should eventually succeed after retries
        assert refund is not None
        # Verify retry behavior
        shopify_calls = [call for call in mock_post.call_args_list 
                        if call[0][0] and 'myshopify.com' in str(call[0][0])]
        assert len(shopify_calls) >= 3, "Should retry failed API calls"

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
            