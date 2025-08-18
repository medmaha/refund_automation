from unittest.mock import MagicMock, Mock, patch

from src.config import DEFAULT_CARRIER_CODE
from src.models.order import (
    ShopifyOrder,
)
from src.models.tracking import (
    TrackingData,
    TrackingStatus,
    TrackingSubStatus,
)
from src.shopify.tracking import (
    fetch_tracking_details,
    generate_tracking_payload,
    get_order_by_tracking_id,
    register_orders_trackings,
)

# trunk-ignore(ruff/F403)
from src.tests.shopify.fixtures import *
from src.tests.uat.uat_constants import UATConstants

TEST_TRACKING_NUMBER = "123456"


def test_get_order_by_tracking_id(dummy_order, dummy_orders_with_invalid_returns):
    orders = [MagicMock(), Mock(), dummy_order, *dummy_orders_with_invalid_returns]
    result = get_order_by_tracking_id(UATConstants.TRACKING_NUMBER, orders)
    assert result == dummy_order


def test_get_order_by_tracking_id_not_found(dummy_order):
    orders = [MagicMock(), Mock(), dummy_order, MagicMock(), Mock()]
    result = get_order_by_tracking_id("NOT_MATCHING_NUMBER", orders)
    assert result is None


def test_generate_tracking_payload(dummy_order, dummy_orders_with_invalid_returns):
    payload = generate_tracking_payload(
        [dummy_order, *dummy_orders_with_invalid_returns]
    )
    assert payload == [
        # {"number": UATConstants.TRACKING_NUMBER, "carrier": DEFAULT_CARRIER_CODE}
        {"number": UATConstants.TRACKING_NUMBER}
    ]


@patch("src.shopify.tracking.requests.post")
def test_register_trackings_success(mock_post, dummy_order):
    mock_post.return_value.json.return_value = {
        "data": {"accepted": [{"number": "TRACK123"}], "rejected": []}
    }
    mock_post.return_value.status_code = 200

    register_orders_trackings([{"number": "TRACK123", "carrier": "DHL"}])

    mock_post.assert_called_once()


@patch("src.shopify.tracking.requests.post")
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
