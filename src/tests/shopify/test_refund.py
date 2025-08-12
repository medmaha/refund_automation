from unittest.mock import MagicMock, patch

import pytest

from src.models.order import (
    LineItem,
    MoneyBag,
    MoneyBagSet,
    OrderTransaction,
    RefundCreateResponse,
    ShopifyOrder,
    TransactionKind,
)
from src.shopify.refund import process_refund_automation, refund_order


@pytest.fixture
def sample_order():
    amount = 100
    currencyCode = "USD"
    return ShopifyOrder(
        id="order_1",
        name="#1001",
        tags=[],
        lineItems=[LineItem(id="li1", quantity=1, refundableQuantity=1)],
        totalPriceSet=MoneyBagSet(
            presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
            shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
        ),
        transactions=[
            OrderTransaction(
                id="txn1",
                gateway="manual",
                kind=TransactionKind.SALE,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                    shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                ),
            )
        ],
        returns=[],
    )


@pytest.fixture
def sample_order_with_multiple_transactions():
    amount = 200
    currencyCode = "EUR"

    return ShopifyOrder(
        id="order_1",
        name="#1001",
        tags=[],
        lineItems=[LineItem(id="li1", quantity=1, refundableQuantity=1)],
        totalPriceSet=MoneyBagSet(
            presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
            shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
        ),
        transactions=[
            OrderTransaction(
                id="txn1",
                gateway="manual",
                kind=TransactionKind.SALE,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                    shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                ),
            ),
            OrderTransaction(
                id="txn2",
                gateway="manual",
                kind=TransactionKind.VOID,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                    shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                ),
            ),
            OrderTransaction(
                id="txn3",
                gateway="manual",
                kind=TransactionKind.CAPTURE,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                    shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                ),
            ),
            OrderTransaction(
                id="txn4",
                gateway="manual",
                kind=TransactionKind.SUGGESTED_REFUND,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                    shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                ),
            ),
            OrderTransaction(
                id="txn5",
                gateway="manual",
                kind=TransactionKind.CHANGE,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                    shopMoney=MoneyBag(amount=amount, currencyCode=currencyCode),
                ),
            ),
        ],
        returns=[],
    )


@pytest.fixture
def sample_tracking():
    class DummyTracking:
        def __init__(self):
            self.track_info = MagicMock()
            self.track_info.latest_event = MagicMock()

    return DummyTracking()


# -------------------------
# refund_order() Tests
# -------------------------
@patch("src.shopify.refund.requests.post")
def test_refund_order_success(mock_post, sample_order):
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    refund_id = "refund_1"
    mock_resp.json.return_value = {
        "data": {
            "refundCreate": {
                "userErrors": [],
                "refund": {
                    "id": refund_id,
                    "createdAt": "2025-08-12T00:00:00Z",
                    "totalRefundedSet": {
                        "presentmentMoney": {"amount": 100, "currencyCode": "USD"},
                        "shopMoney": {"amount": 100, "currencyCode": "USD"},
                    },
                },
            }
        }
    }
    mock_post.return_value = mock_resp

    refund = refund_order(sample_order)
    assert refund is not None

    assert isinstance(refund, RefundCreateResponse)
    assert refund.id == refund_id
    assert refund.orderId == sample_order.id
    assert refund.orderName == sample_order.name
    assert (
        refund.totalRefundedSet.model_dump() == sample_order.totalPriceSet.model_dump()
    )


@patch("src.shopify.refund.requests.post")
def test_refund_with_multiple_transactions(
    mock_post, sample_order_with_multiple_transactions
):
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    refund_id = "refund_1"

    mock_resp.json.return_value = {
        "data": {
            "refundCreate": {
                "userErrors": [],
                "refund": {
                    "id": refund_id,
                    "createdAt": "2025-08-12T00:00:00Z",
                    "totalRefundedSet": sample_order_with_multiple_transactions.totalPriceSet.model_dump(),
                },
            }
        }
    }
    mock_post.return_value = mock_resp

    refund = refund_order(sample_order_with_multiple_transactions)
    assert refund is not None
    assert (
        refund.totalRefundedSet.model_dump()
        == sample_order_with_multiple_transactions.totalPriceSet.model_dump()
    )


@patch("src.shopify.refund.requests.post")
def test_refund_order_with_user_errors(mock_post, sample_order):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "refundCreate": {
                "userErrors": [{"message": "Refund not allowed"}],
                "refund": None,
            }
        }
    }
    mock_post.return_value = mock_resp

    refund = refund_order(sample_order)
    assert refund is None


def test_refund_order_no_sale_transaction(sample_order):
    sample_order.transactions[0].kind = TransactionKind.REFUND
    result = refund_order(sample_order)
    assert result is None


# -------------------------
# process_refund_automation() Tests
# -------------------------
@pytest.mark.parametrize(
    "order",
    [
        sample_order._fixture_function(),
        sample_order_with_multiple_transactions._fixture_function(),
    ],
)
@patch("src.shopify.refund.retrieve_fulfilled_shopify_orders")
@patch("src.shopify.refund.refund_order")
def test_process_refund_automation_with_orders(
    mock_refund_order, mock_retrieve, sample_tracking, order: ShopifyOrder
):
    mock_retrieve.return_value = [(order, sample_tracking)]
    mock_refund_order.return_value = RefundCreateResponse(
        id="refund_1",
        orderId=order.id,
        orderName=order.name,
        createdAt="2025-08-12T00:00:00Z",
        totalRefundedSet=order.totalPriceSet.model_dump(),
    )

    process_refund_automation()
    mock_refund_order.assert_called_once()


@patch("src.shopify.refund.retrieve_fulfilled_shopify_orders", return_value=[])
@patch("src.shopify.refund.sys.exit")
def test_process_refund_automation_no_orders(mock_exit, mock_retrieve):
    mock_retrieve.return_value = []
    process_refund_automation()
    mock_exit.assert_called_once_with(0)


@patch("src.shopify.refund.retrieve_fulfilled_shopify_orders")
def test_process_refund_automation_missing_latest_event(
    mock_retrieve, sample_order, sample_tracking
):
    sample_tracking.track_info.latest_event = None
    mock_retrieve.return_value = [(sample_order, sample_tracking)]
    # Should skip refund and not raise
    process_refund_automation()
