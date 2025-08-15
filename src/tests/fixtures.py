# ============================================================================
# PYTEST FIXTURES
# ============================================================================

import os
import shutil
from unittest.mock import Mock
import pytest
from src.models.order import TransactionKind
from src.tests.conftest import TestConstants, TestFixtures

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

