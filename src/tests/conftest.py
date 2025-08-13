"""
Centralized pytest fixtures and test configuration for the refund automation test suite.

This module provides shared fixtures, constants, and utilities following the DRY principle
to reduce code duplication across test files.
"""

import os
import json
import uuid
import pytest
import shutil
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from typing import Dict, Any, List, Tuple

from src.models.order import (
    LineItem,
    MoneyBag,
    MoneyBagSet,
    OrderTransaction,
    ShopifyOrder,
    TransactionKind,
    RefundCreateResponse
)


# ============================================================================
# TEST CONSTANTS
# ============================================================================

class TestConstants:
    """Centralized test constants to ensure consistency across test suites."""
    
    # Test data constants
    DEFAULT_AMOUNT = 100.0
    DEFAULT_CURRENCY = "USD"
    DEFAULT_TRACKING_NUMBER = "TEST123456789"
    DEFAULT_TRACKING_EVENT = "delivered"
    DEFAULT_GATEWAY = "shopify_payments"
    
    # Shopify API constants
    SHOPIFY_REFUND_ID_PREFIX = "gid://shopify/Refund/"
    SHOPIFY_ORDER_ID_PREFIX = "gid://shopify/Order/"
    SHOPIFY_TRANSACTION_ID_PREFIX = "gid://shopify/Transaction/"
    SHOPIFY_LINE_ITEM_ID_PREFIX = "gid://shopify/LineItem/"
    
    # Test file patterns
    IDEMPOTENCY_CACHE_FILE = ".idempotency_cache.json"
    AUDIT_LOGS_DIR = ".audit_logs"
    AUDIT_LOG_PREFIX = "audit_"
    
    # Slack test constants
    SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
    SLACK_CHANNEL = "#test-channel"
    
    # API response constants
    SUCCESS_RESPONSE_STATUS = 200
    ERROR_RESPONSE_STATUS = 400
    
    # Time constants
    DEFAULT_CREATED_AT = "2023-01-01T00:00:00Z"


class TestFixtures:
    """Helper class for creating test fixtures with consistent data."""
    
    @staticmethod
    def create_money_set(amount: float = TestConstants.DEFAULT_AMOUNT, 
                        currency: str = TestConstants.DEFAULT_CURRENCY) -> MoneyBagSet:
        """Create a MoneyBagSet for testing."""
        return MoneyBagSet(
            presentmentMoney=MoneyBag(amount=amount, currencyCode=currency),
            shopMoney=MoneyBag(amount=amount, currencyCode=currency)
        )
    
    @staticmethod
    def create_api_error_response(error_message: str = "Test API error") -> Dict[str, Any]:
        """Create a mock API error response."""
        return {
            "data": {
                "refundCreate": {
                    "userErrors": [{"message": error_message}],
                    "refund": None
                }
            }
        }
    
    @staticmethod
    def create_tracking_payload(tracking_numbers: List[str] = None) -> List[Dict[str, Any]]:
        """Create a tracking payload for API testing."""
        if tracking_numbers is None:
            tracking_numbers = [TestConstants.DEFAULT_TRACKING_NUMBER]
        
        return [
            {"number": num, "carrier": 7041}  # DHL Paket
            for num in tracking_numbers
        ]
    
    @staticmethod
    def create_tracking_api_response(tracking_numbers: List[str] = None, 
                                   status: str = "delivered") -> Dict[str, Any]:
        """Create a mock tracking API response."""
        if tracking_numbers is None:
            tracking_numbers = [TestConstants.DEFAULT_TRACKING_NUMBER]
        
        accepted_data = []
        for num in tracking_numbers:
            accepted_data.append({
                "number": num,
                "carrier": 7041,
                "track_info": {
                    "latest_status": {
                        "status": status,
                        "sub_status": "delivered_other" if status == "delivered" else "other"
                    },
                    "latest_event": TestConstants.DEFAULT_TRACKING_EVENT
                }
            })
        
        return {
            "data": {
                "accepted": accepted_data,
                "rejected": []
            }
        }
    
    @staticmethod
    def create_transaction(transaction_id: str = None,
                          kind: TransactionKind = TransactionKind.SALE,
                          amount: float = TestConstants.DEFAULT_AMOUNT,
                          currency: str = TestConstants.DEFAULT_CURRENCY) -> OrderTransaction:
        """Create an OrderTransaction for testing."""
        if transaction_id is None:
            transaction_id = f"{TestConstants.SHOPIFY_TRANSACTION_ID_PREFIX}1"
        
        return OrderTransaction(
            id=transaction_id,
            gateway=TestConstants.DEFAULT_GATEWAY,
            kind=kind,
            amountSet=TestFixtures.create_money_set(amount, currency)
        )
    
    @staticmethod
    def create_line_item(item_id: str = None,
                        quantity: int = 1,
                        refundable_quantity: int = 1) -> LineItem:
        """Create a LineItem for testing."""
        if item_id is None:
            item_id = f"{TestConstants.SHOPIFY_LINE_ITEM_ID_PREFIX}1"
        
        return LineItem(
            id=item_id,
            quantity=quantity,
            refundableQuantity=refundable_quantity
        )
    
    @staticmethod
    def create_order(order_id: str = None,
                    order_name: str = None,
                    transaction_kinds: List[TransactionKind] = None,
                    amount: float = TestConstants.DEFAULT_AMOUNT,
                    currency: str = TestConstants.DEFAULT_CURRENCY) -> ShopifyOrder:
        """Create a ShopifyOrder for testing with unique ID."""
        # Generate unique identifiers
        unique_id = str(uuid.uuid4())[:8]
        
        if order_id is None:
            order_id = f"{TestConstants.SHOPIFY_ORDER_ID_PREFIX}{unique_id}"
        
        if order_name is None:
            order_name = f"TEST-{unique_id}"
        
        if transaction_kinds is None:
            transaction_kinds = [TransactionKind.SALE]
        
        # Create components
        money_set = TestFixtures.create_money_set(amount, currency)
        line_item = TestFixtures.create_line_item()
        
        transactions = [
            TestFixtures.create_transaction(
                f"{TestConstants.SHOPIFY_TRANSACTION_ID_PREFIX}{i}",
                kind,
                amount,
                currency
            )
            for i, kind in enumerate(transaction_kinds)
        ]
        
        return ShopifyOrder(
            id=order_id,
            name=order_name,
            tags=[],
            lineItems=[line_item],
            totalPriceSet=money_set,
            transactions=transactions,
            returns=[]
        )
    
    @staticmethod
    def create_tracking(tracking_number: str = TestConstants.DEFAULT_TRACKING_NUMBER,
                       latest_event: str = TestConstants.DEFAULT_TRACKING_EVENT) -> Mock:
        """Create a mock tracking object for testing."""
        tracking = Mock()
        tracking.number = tracking_number
        tracking.track_info.latest_event = latest_event
        return tracking
    
    @staticmethod
    def create_shopify_response(refund_id: str = None,
                              user_errors: List[Dict[str, Any]] = None,
                              amount: float = TestConstants.DEFAULT_AMOUNT,
                              currency: str = TestConstants.DEFAULT_CURRENCY) -> Dict[str, Any]:
        """Create a mock Shopify API response for testing."""
        if refund_id is None:
            refund_id = f"{TestConstants.SHOPIFY_REFUND_ID_PREFIX}12345"
        
        return {
            "data": {
                "refundCreate": {
                    "userErrors": user_errors or [],
                    "refund": {
                        "id": refund_id,
                        "createdAt": TestConstants.DEFAULT_CREATED_AT,
                        "totalRefundedSet": {
                            "presentmentMoney": {
                                "amount": str(amount),
                                "currencyCode": currency
                            }
                        }
                    } if not user_errors else None
                }
            }
        }


# ============================================================================
# PYTEST FIXTURES
# ============================================================================

@pytest.fixture
def test_constants():
    """Provide access to test constants."""
    return TestConstants


@pytest.fixture
def test_fixtures():
    """Provide access to test fixture helpers."""
    return TestFixtures


@pytest.fixture
def sample_order():
    """Create a unique sample order for each test to avoid idempotency conflicts."""
    return TestFixtures.create_order()


@pytest.fixture
def sample_tracking():
    """Create a sample tracking object."""
    return TestFixtures.create_tracking()


@pytest.fixture
def sample_order_with_tracking():
    """Create a tuple of sample order and tracking for convenience."""
    return TestFixtures.create_order(), TestFixtures.create_tracking()


@pytest.fixture
def multiple_orders():
    """Create multiple unique orders for testing batch operations."""
    return [TestFixtures.create_order() for _ in range(3)]


@pytest.fixture
def order_with_multiple_transactions():
    """Create an order with multiple transaction types."""
    return TestFixtures.create_order(
        transaction_kinds=[TransactionKind.SALE, TransactionKind.CAPTURE]
    )


@pytest.fixture
def order_without_valid_transactions():
    """Create an order with no valid transactions for refunding."""
    return TestFixtures.create_order(
        transaction_kinds=[TransactionKind.REFUND, TransactionKind.VOID]
    )


@pytest.fixture
def successful_api_response():
    """Create a successful Shopify API response."""
    return TestFixtures.create_shopify_response()


@pytest.fixture
def error_api_response():
    """Create an error Shopify API response."""
    return TestFixtures.create_shopify_response(
        user_errors=[{"message": "Test error"}]
    )


@pytest.fixture
def mock_requests_response():
    """Create a mock requests response."""
    mock_response = Mock()
    mock_response.status_code = TestConstants.SUCCESS_RESPONSE_STATUS
    mock_response.json.return_value = TestFixtures.create_shopify_response()
    mock_response.raise_for_status = Mock()
    return mock_response


@pytest.fixture(autouse=True)
def cleanup_test_files():
    """
    Automatically clean up test files before and after each test.
    This fixture runs automatically for every test.
    """
    # Clean up before test
    _cleanup_files()
    
    yield
    
    # Clean up after test
    _cleanup_files()


def _cleanup_files():
    """Helper function to clean up test files."""
    # Clean up idempotency cache
    if os.path.exists(TestConstants.IDEMPOTENCY_CACHE_FILE):
        os.remove(TestConstants.IDEMPOTENCY_CACHE_FILE)
    
    # Clean up audit logs
    if os.path.exists(TestConstants.AUDIT_LOGS_DIR):
        shutil.rmtree(TestConstants.AUDIT_LOGS_DIR, ignore_errors=True)


# ============================================================================
# TEST UTILITIES
# ============================================================================

class MockHelpers:
    """Helper functions for creating common mocks."""
    
    @staticmethod
    def mock_slack_notifier():
        """Create a mock Slack notifier with common methods."""
        mock = MagicMock()
        mock.send_info = MagicMock()
        mock.send_warning = MagicMock()
        mock.send_error = MagicMock()
        mock.send_success = MagicMock()
        mock.send_refund_summary = MagicMock()
        return mock
    
    @staticmethod
    def mock_successful_requests_post():
        """Create a mock requests.post that returns success."""
        mock = MagicMock()
        mock.return_value.status_code = TestConstants.SUCCESS_RESPONSE_STATUS
        mock.return_value.json.return_value = TestFixtures.create_shopify_response()
        mock.return_value.raise_for_status = Mock()
        return mock
    
    @staticmethod
    def mock_failing_requests_post(exception_message: str = "Network error"):
        """Create a mock requests.post that raises an exception."""
        mock = MagicMock()
        mock.side_effect = Exception(exception_message)
        return mock


class AssertionHelpers:
    """Helper functions for common test assertions."""
    
    @staticmethod
    def assert_refund_created(refund: RefundCreateResponse, 
                            expected_order: ShopifyOrder,
                            is_dry_run: bool = False):
        """Assert that a refund was created correctly."""
        assert refund is not None
        assert refund.orderId == expected_order.id
        
        if is_dry_run:
            assert "dry-run" in refund.id.lower()
            assert "DRY_RUN" in refund.orderName
        else:
            assert "dry-run" not in refund.id.lower()
            assert "DRY_RUN" not in refund.orderName
    
    @staticmethod
    def assert_slack_called(mock_slack: Mock, 
                          should_call_info: bool = True,
                          should_call_summary: bool = True):
        """Assert that Slack methods were called as expected."""
        if should_call_info:
            assert mock_slack.send_info.called, "Expected send_info to be called"
        
        if should_call_summary:
            assert mock_slack.send_refund_summary.called, "Expected send_refund_summary to be called"
    
    @staticmethod
    def assert_api_called(mock_post: Mock, expected_calls: int = 1):
        """Assert that API was called the expected number of times."""
        assert mock_post.call_count == expected_calls, f"Expected {expected_calls} API calls, got {mock_post.call_count}"


# Provide helper classes as fixtures for easy access in tests
@pytest.fixture
def mock_helpers():
    """Provide access to mock helper functions."""
    return MockHelpers


@pytest.fixture
def assert_helpers():
    """Provide access to assertion helper functions."""
    return AssertionHelpers
