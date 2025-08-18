from unittest.mock import patch, Mock, MagicMock

from src.config import DEFAULT_CARRIER_CODE
from src.models.order import (
    ShopifyOrder,
    TransactionKind,
)
from src.models.tracking import (
    TrackingData,
    TrackingStatus,
    TrackingSubStatus,
)
from src.shopify.orders import __cleanup_shopify_orders as cleanup_shopify_orders
from src.shopify.orders import __fetch_tracking_details as fetch_tracking_details
from src.shopify.orders import __generate_tracking_payload as generate_tracking_payload
from src.shopify.orders import __get_order_by_tracking_id as get_order_by_tracking_id
from src.shopify.orders import __register_trackings as register_tracking
from src.shopify.orders import retrieve_refundable_shopify_orders
from src.tests.uat.uat_constants import UATConstants
from src.tests.uat.uat_fixtures import UATFixtureBuilder, create_delivered_tracking

import pytest

TEST_TRACKING_NUMBER = "123456"


@pytest.fixture
def dummy_order():
    return (
        UATFixtureBuilder()
        .with_line_item("gid://shopify/LineItem/BP1001", quantity=2, price=50.0)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .with_return_tracking(UATConstants.TRACKING_NUMBER)
        .with_return_line_item("gid://shopify/LineItem/BP1001", refundable_qty=2)
        .with_shipping(amount=10)
        .build()
    )


@pytest.fixture
def dummy_orders_with_invalid_returns():
    return [
        (
            UATFixtureBuilder()
            .with_line_item(f"gid://shopify/LineItem/BP100{i}", quantity=2, price=50.0)
            .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
            .with_shipping(amount=10)
            .with_no_tracking_no()
            .build()
        )
        for i in range(5)
    ]


@pytest.fixture
def dummy_tracking():
    return create_delivered_tracking()


def test_get_order_by_tracking_id(dummy_order, dummy_orders_with_invalid_returns):
    orders = [MagicMock(), Mock(), dummy_order, *dummy_orders_with_invalid_returns]
    result = get_order_by_tracking_id(UATConstants.TRACKING_NUMBER, orders)
    assert result == dummy_order


def test_get_order_by_tracking_id_not_found(dummy_order):
    orders = [MagicMock(), Mock(), dummy_order, MagicMock(), Mock()]
    result = get_order_by_tracking_id("NOT_MATCHING_NUMBER", orders)
    assert result is None


def test_cleanup_orders_success(dummy_order, dummy_orders_with_invalid_returns):
    orders = [dummy_order, *dummy_orders_with_invalid_returns]
    cleaned = cleanup_shopify_orders(orders.copy())

    assert dummy_order in cleaned
    assert len(cleaned) == 1


def test_cleanup_orders_failure(dummy_order, dummy_orders_with_invalid_returns):
    dummy_order.returns = []  # remove the return shipment

    orders = [dummy_order, *dummy_orders_with_invalid_returns]
    cleaned = cleanup_shopify_orders(orders.copy())

    assert dummy_order not in cleaned
    assert len(cleaned) == 0


def test_generate_tracking_payload(dummy_order, dummy_orders_with_invalid_returns):
    payload = generate_tracking_payload(
        [dummy_order, *dummy_orders_with_invalid_returns]
    )
    assert payload == [
        {"number": UATConstants.TRACKING_NUMBER, "carrier": DEFAULT_CARRIER_CODE}
    ]


@patch("src.shopify.refund.requests.post")
def test_register_trackings_success(mock_post, dummy_order):
    mock_post.return_value.json.return_value = {
        "data": {"accepted": [{"number": "TRACK123"}], "rejected": []}
    }
    mock_post.return_value.status_code = 200

    register_tracking([{"number": "TRACK123", "carrier": "DHL"}])

    mock_post.assert_called_once()


@patch("src.shopify.refund.requests.post")
def test_fetch_tracking_details_success(
    mock_post, dummy_order, dummy_orders_with_invalid_returns
):
    tracking_response = {
        "data": {
            "accepted": [
                {
                    "number": "TRACK123",
                    "carrier": DEFAULT_CARRIER_CODE,
                    "track_info": {
                        "latest_status": {
                            "status": TrackingStatus.DELIVERED.value,
                            "sub_status": TrackingSubStatus.DELIVERED_OTHER.value,
                        },
                    },
                },
                {  # This should match an order
                    "number": UATConstants.TRACKING_NUMBER,
                    "carrier": DEFAULT_CARRIER_CODE,
                    "track_info": {
                        "latest_status": {
                            "status": TrackingStatus.DELIVERED.value,
                            "sub_status": TrackingSubStatus.DELIVERED_OTHER.value,
                        },
                    },
                },
            ]
        }
    }

    mock_post.return_value.json.return_value = tracking_response
    mock_post.return_value.status_code = 200

    result = fetch_tracking_details(
        [
            {"number": "TRACK123", "carrier": "DHL"},
            {"number": UATConstants.TRACKING_NUMBER, "carrier": "DHL"},
        ],
        [dummy_order, *dummy_orders_with_invalid_returns],
    )
    assert len(result) == 1
    assert dummy_order == result[0][0]
    assert isinstance(result[0][0], ShopifyOrder)
    assert isinstance(result[0][1], TrackingData)


@patch("src.shopify.orders.__fetch_all_shopify_orders")
@patch("src.shopify.orders.__fetch_tracking_details")
@patch("src.shopify.orders.__register_trackings")
@patch("src.shopify.orders.slack_notifier")
def test_retrieve_refundable_orders(
    mock_slack,
    mock_register,
    mock_fetch_tracking,
    mock_fetch_orders,
    dummy_order,
    dummy_tracking,
):
    mock_fetch_orders.return_value = [dummy_order]
    mock_fetch_tracking.return_value = [(dummy_order, dummy_tracking)]
    mock_register.return_value = None

    with patch("src.shopify.orders.time.sleep") as mock_sleep:
        results = retrieve_refundable_shopify_orders()
        assert len(results) == 1
        assert results[0][0] == dummy_order
        mock_sleep.assert_called_with(5)
