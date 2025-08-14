"""Optimized test suite for refund functionality using DRY principles and centralized fixtures."""

import pytest
from unittest.mock import patch, Mock
from datetime import datetime

from src.shopify.refund import process_refund_automation, refund_order
from src.tests.fixtures import *

@pytest.fixture(autouse=True)
def mock_slack_and_idempotency():
    """Mocks slack_notifier and idempotency_manager for all tests in this module."""
    with patch('src.shopify.refund.slack_notifier') as mock_slack, \
         patch('src.shopify.refund.idempotency_manager') as mock_idempotency_manager:
        mock_idempotency_manager.check_operation_idempotency.return_value = ("test_key", False)
        yield mock_slack, mock_idempotency_manager

class TestRefundModes:
    """Test refund functionality in both DRY_RUN and LIVE modes."""
    
    def test_dry_run_creates_mock_refund(self, mock_slack_and_idempotency, sample_order, sample_tracking, assert_helpers):
        """Test that DRY_RUN mode creates mock refunds."""
        mock_slack, mock_idempotency_manager = mock_slack_and_idempotency
        refund = refund_order(sample_order, sample_tracking)

        assert_helpers.assert_refund_created(refund, sample_order, is_dry_run=True)
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'DRY_RUN')
    @patch('requests.post')
    def test_dry_run_no_api_calls(self, mock_post, mock_slack_and_idempotency, sample_order, sample_tracking):
        """Test that DRY_RUN mode doesn't make API calls to Shopify."""
        mock_slack, mock_idempotency_manager = mock_slack_and_idempotency

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
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('requests.post')
    def test_live_mode_makes_api_calls(self, mock_post, mock_slack_and_idempotency, sample_order, sample_tracking, 
                                      successful_api_response, assert_helpers):
        """Test that LIVE mode makes actual API calls."""
        mock_slack, mock_idempotency_manager = mock_slack_and_idempotency
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
    @patch('requests.post')
    def test_live_mode_handles_api_errors(self, mock_post, mock_slack_and_idempotency, sample_order, sample_tracking, error_api_response):
        """Test that LIVE mode handles API errors."""
        mock_slack, mock_idempotency_manager = mock_slack_and_idempotency
        # Mock API error response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = error_api_response
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        refund = refund_order(sample_order, sample_tracking)
        
        assert refund is None  # Should fail due to user errors

class TestErrorHandling:
    """Test error handling functionality."""
    
    @patch('src.config.DRY_RUN', True)
    def test_no_valid_transactions(self, mock_slack_and_idempotency, order_without_valid_transactions, sample_tracking):
        """Test handling of orders with no valid transactions."""
        mock_slack, mock_idempotency_manager = mock_slack_and_idempotency
        refund = refund_order(order_without_valid_transactions, sample_tracking)
        
        assert refund is None
    
    @patch('src.shopify.refund.EXECUTION_MODE', 'LIVE')
    @patch('requests.post')
    def test_network_error_handling(self, mock_post, mock_slack_and_idempotency, sample_order, sample_tracking, mock_helpers):
        """Test handling of network errors."""
        mock_slack, mock_idempotency_manager = mock_slack_and_idempotency
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
