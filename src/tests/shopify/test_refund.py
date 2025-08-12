import pytest
from unittest.mock import MagicMock, patch

from src.models.order import (
    LineItem,
    MoneyBag,
    MoneyBagSet,
    OrderTransaction,
    ShopifyOrder,
    TransactionKind,
)
from src.shopify.refund import process_refund_automation, refund_order


def _create_order(transaction_kinds=None):
    """Helper to create minimal order for testing."""
    if transaction_kinds is None:
        transaction_kinds = [TransactionKind.SALE]
    
    money_set = MoneyBagSet(presentmentMoney=MoneyBag(amount=100.0))
    transactions = [
        OrderTransaction(id=f"tx_{i}", gateway="test", kind=kind, amountSet=money_set)
        for i, kind in enumerate(transaction_kinds)
    ]
    
    return ShopifyOrder(
        id="test_order", name="#TEST", tags=[], 
        lineItems=[LineItem(id="li1", quantity=1, refundableQuantity=1)],
        totalPriceSet=money_set, transactions=transactions, returns=[]
    )


def _mock_refund_response(refund_id="test_refund", user_errors=None):
    """Helper to create minimal mock refund response."""
    return {
        "data": {
            "refundCreate": {
                "userErrors": user_errors or [],
                "refund": {
                    "id": refund_id,
                    "createdAt": "2025-01-01T00:00:00Z",
                    "totalRefundedSet": {"presentmentMoney": {"amount": 100.0}}
                } if not user_errors else None
            }
        }
    }


@patch("src.shopify.refund.requests.post")
def test_refund_order_success(mock_post):
    """Test successful refund creation."""
    order = _create_order()
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = _mock_refund_response("refund_123")
    
    refund = refund_order(order)
    assert refund is not None
    assert refund.orderId == order.id


@patch("src.shopify.refund.requests.post")
def test_refund_with_multiple_transactions(mock_post):
    """Test refund with multiple valid transaction types."""
    order = _create_order([TransactionKind.SALE, TransactionKind.SUGGESTED_REFUND])
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = _mock_refund_response()
    
    refund = refund_order(order)
    assert refund is not None


@pytest.mark.parametrize("DRY_RUN", [True, False])
@patch("src.shopify.refund.requests.post")
def test_refund_order_with_user_errors(mock_post, DRY_RUN, monkeypatch):
    """Test refund failure due to Shopify user errors."""

    execution_mode = "DRY_RUN" if DRY_RUN else "LIVE"
    monkeypatch.setattr("src.shopify.refund.EXECUTION_MODE", execution_mode)
    
    order = _create_order()
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = _mock_refund_response(
        user_errors=[{"message": "Refund not allowed"}]
    )
    
    refund = refund_order(order)

    if not DRY_RUN:
        assert refund is None


def test_refund_order_no_sale_transaction():
    """Test refund fails when order has no valid transactions."""
    order = _create_order([TransactionKind.REFUND])  # Invalid transaction type
    result = refund_order(order)
    assert result is None


@patch("src.shopify.refund.retrieve_fulfilled_shopify_orders")
@patch("src.shopify.refund.refund_order")
def test_process_refund_automation_with_orders(mock_refund_order, mock_retrieve):
    """Test automation processes orders with valid tracking."""
    order = _create_order()
    tracking = MagicMock()
    tracking.track_info.latest_event = MagicMock()  # Has latest event
    
    mock_retrieve.return_value = [(order, tracking)]
    mock_refund_order.return_value = MagicMock(id="refund_123")
    
    process_refund_automation()
    mock_refund_order.assert_called_once_with(order)


@patch("src.shopify.refund.retrieve_fulfilled_shopify_orders", return_value=[])
@patch("src.shopify.refund.sys.exit")
def test_process_refund_automation_no_orders(mock_exit, mock_retrieve):
    """Test automation exits when no orders are found."""
    process_refund_automation()
    mock_exit.assert_called_once_with(0)


@patch("src.shopify.refund.retrieve_fulfilled_shopify_orders")
def test_process_refund_automation_missing_latest_event(mock_retrieve):
    """Test automation skips orders without latest tracking event."""
    order = _create_order()
    tracking = MagicMock()
    tracking.track_info.latest_event = None  # No latest event
    
    mock_retrieve.return_value = [(order, tracking)]
    # Should not raise exception, just skip processing
    process_refund_automation()
