from unittest.mock import patch

import pytest

from src.models.order import (
    ShopifyOrder,
)
from src.models.tracking import (
    LatestEvent,
    LatestStatus,
    TrackInfo,
    TrackingData,
    TrackingStatus,
    TrackingSubStatus,
)
from src.shopify.orders import __cleanup_shopify_orders as cleanup_shopify_orders
from src.shopify.orders import __fetch_tracking_details as fetch_tracking_details
from src.shopify.orders import __generate_tracking_payload as generate_tracking_payload
from src.shopify.orders import __get_order_by_tracking_id as get_order_by_tracking_id
from src.shopify.orders import (
    parse_graphql_order_data,
    retrieve_fulfilled_shopify_orders,
)

VALID_TRACKING_NUMBER = "123456"
VALID_TRACKING_CARRIER_NUMBER = "2130001"




@pytest.fixture
def shopify_order_without_tracking():
    dummy_order = parse_graphql_order_data(dummy_order_node_2)
    shopify_order = ShopifyOrder(**dummy_order)
    return shopify_order


@pytest.fixture
def shopify_order_with_tracking():
    dummy_order = parse_graphql_order_data(dummy_order_node_1)
    shopify_order = ShopifyOrder(**dummy_order)
    return shopify_order


# ----------------------
# __get_order_by_tracking_id
# ----------------------
def test_get_order_by_tracking_id_found(
    shopify_order_with_tracking, shopify_order_without_tracking
):
    found = get_order_by_tracking_id(
        VALID_TRACKING_NUMBER,
        [shopify_order_with_tracking, shopify_order_without_tracking],
    )
    assert found is shopify_order_with_tracking


def test_get_order_by_tracking_id_not_found(
    shopify_order_with_tracking, shopify_order_without_tracking
):
    found = get_order_by_tracking_id(
        "99999", [shopify_order_with_tracking, shopify_order_without_tracking]
    )
    assert found is None


# ----------------------
# __generate_tracking_payload
# ----------------------
def test_generate_tracking_payload_valid(
    shopify_order_with_tracking, shopify_order_without_tracking
):
    payload = generate_tracking_payload(
        [shopify_order_with_tracking, shopify_order_without_tracking]
    )
    assert payload == [
        {"number": VALID_TRACKING_NUMBER, "carrier": 7041}
    ]  # DHL Paket fallback


def test_generate_tracking_payload_empty():
    assert generate_tracking_payload([]) == []


# ----------------------
# __cleanup_shopify_orders
# ----------------------
def test_cleanup_shopify_orders_keeps_valid(shopify_order_with_tracking):
    orders = [shopify_order_with_tracking]
    cleaned = cleanup_shopify_orders(orders.copy())
    assert len(cleaned) == 1
    assert cleaned[0].id == shopify_order_with_tracking.id


def test_cleanup_shopify_orders_discards_invalid(shopify_order_with_tracking):
    # Remove tracking info so valid_return_shipment is None
    shopify_order_with_tracking.returns[0].reverseFulfillmentOrders[
        0
    ].reverseDeliveries[0].deliverable.tracking.number = None
    cleaned = cleanup_shopify_orders([shopify_order_with_tracking])
    assert len(cleaned) == 0


# ----------------------
# __fetch_tracking_details
# ----------------------
@patch("src.shopify.orders.requests.post")
def test_fetch_tracking_details_matching(
    mock_post, shopify_order_with_tracking, shopify_order_without_tracking
):
    latest_status = LatestStatus(
        status=TrackingStatus.DELIVERED,
        sub_status=TrackingSubStatus.DELIVERED_OTHER,
        sub_status_descr=None,
    )
    latest_event = LatestEvent(
        time_iso="2025-08-12T00:00:00Z",
        time_utc=None,
        description=None,
        location=None,
        stage=None,
        sub_status=None,
    )
    track_info = TrackInfo(
        latest_status=latest_status, latest_event=latest_event, milestone=[]
    )

    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "data": {
            "accepted": [
                {
                    "tag": "x",
                    "number": VALID_TRACKING_NUMBER,
                    "carrier": 7041,
                    "param": None,
                    "track_info": track_info.model_dump(),
                } 
            ]
        }
    }

    result = fetch_tracking_details(
        [
            {"number": VALID_TRACKING_NUMBER, "carrier": 7041},
            {"number": "27397", "carrier": 7041},
        ],
        [shopify_order_with_tracking, shopify_order_without_tracking],
    )
    assert len(result) == 1
    assert result[0][0] is shopify_order_with_tracking
    assert isinstance(result[0][1], TrackingData)


@patch("src.shopify.orders.requests.post")
def test_fetch_tracking_details_not_matching_status(
    mock_post, shopify_order_with_tracking, shopify_order_without_tracking
):
    latest_status = LatestStatus(
        status=TrackingStatus.NOTFOUND,
        sub_status=TrackingSubStatus.NOTFOUND_OTHER,
        sub_status_descr=None,
    )
    track_info = TrackInfo(latest_status=latest_status, latest_event=None, milestone=[])

    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "data": {
            "accepted": [
                {
                    "tag": "x",
                    "number": VALID_TRACKING_NUMBER,
                    "carrier": VALID_TRACKING_CARRIER_NUMBER,
                    "param": None,
                    "track_info": track_info.model_dump(),
                }
            ]
        }
    }

    result = fetch_tracking_details(
        [
            {"number": VALID_TRACKING_NUMBER, "carrier": VALID_TRACKING_CARRIER_NUMBER},
            {"number": "99999", "carrier": VALID_TRACKING_CARRIER_NUMBER},
        ],
        [shopify_order_with_tracking, shopify_order_without_tracking],
    )
    assert result == []


# ----------------------
# retrieve_fulfilled_shopify_orders (E2E patched)
# ----------------------
@patch("src.shopify.orders.__fetch_tracking_details")
@patch("src.shopify.orders.__register_trackings")
@patch("src.shopify.orders.__generate_tracking_payload")
@patch("src.shopify.orders.__cleanup_shopify_orders")
@patch("src.shopify.orders.requests.post")
def test_retrieve_fulfilled_shopify_orders_success(
    mock_graphql_req,
    mock_cleanup,
    mock_generate_payload,
    mock_register_trackings,
    mock_fetch_trackings,
    shopify_order_with_tracking,
):

    tracking_data = {
        "number": VALID_TRACKING_NUMBER,
        "carrier": VALID_TRACKING_CARRIER_NUMBER,
        "tag": "tag1",
    }
    tracking = TrackingData(**tracking_data, param=None, track_info=None)

    mock_cleanup.return_value = [shopify_order_with_tracking]
    mock_generate_payload.return_value = {
        "number": VALID_TRACKING_NUMBER,
        "carrier": VALID_TRACKING_CARRIER_NUMBER,
    }
    mock_fetch_trackings.return_value = [(shopify_order_with_tracking, tracking)]

    mock_graphql_req.return_value.status_code = 200
    mock_graphql_req.return_value.json.return_value = {
        "data": get_graphql_query_response()
    }
    result = retrieve_fulfilled_shopify_orders()

    assert result == [(shopify_order_with_tracking, tracking)]


valid_dummy_return_id = "gid://shopify/Order/1002"


def get_graphql_query_response():
    shopify_graphql = {
        "orders": {
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": False,
                "startCursor": "cursor_start_1",
                "endCursor": "cursor_end_1",
            },
            "edges": [
                {"cursor": "cursor_1", "node": dummy_order_node_1},
                {"cursor": "cursor_2", "node": dummy_order_node_2},
            ],
        }
    }

    return shopify_graphql


dummy_order_node_2 = {
    "id": "gid://shopify/Order/1002",
    "name": "#1002",
    "tags": ["Standard"],
    "transactions": [
        {
            "id": "gid://shopify/Transaction/tx3",
            "kind": "SALE",
            "gateway": "paypal",
            "amountSet": {"presentmentMoney": {"amount": "200.00"}},
        }
    ],
    "totalPriceSet": {
        "shopMoney": {"amount": "200.00", "currencyCode": "USD"},
        "presentmentMoney": {
            "amount": "200.00",
            "currencyCode": "USD",
        },
    },
    "lineItems": {
        "nodes": [
            {
                "id": "gid://shopify/LineItem/li3",
                "quantity": 1,
                "refundableQuantity": 0,
            }
        ]
    },
    "fulfillments": [
        {
            "id": "gid://shopify/Fulfillment/f2",
            "name": "Fulfillment 2",
            "totalQuantity": 1,
            "displayStatus": "FULFILLED",
            "requiresShipping": True,
            "trackingInfo": [
                {
                    "number": "TRACK456",
                    "company": "FedEx",
                    "url": "https://fedex.com/track?num=TRACK456",
                }
            ],
        }
    ],
    "returns": {"nodes": []},
}

dummy_order_node_1 = {
    "id": "gid://shopify/Order/1001",
    "name": "#1001",
    "tags": ["VIP", "Return Pending"],
    "transactions": [
        {
            "id": "gid://shopify/Transaction/tx1",
            "kind": "SALE",
            "gateway": "manual",
            "amountSet": {"presentmentMoney": {"amount": "120.00"}},
        },
        {
            "id": "gid://shopify/Transaction/tx2",
            "kind": "REFUND",
            "gateway": "manual",
            "amountSet": {"presentmentMoney": {"amount": "50.00"}},
        },
    ],
    "totalPriceSet": {
        "shopMoney": {"amount": "120.00", "currencyCode": "USD"},
        "presentmentMoney": {
            "amount": "120.00",
            "currencyCode": "USD",
        },
    },
    "lineItems": {
        "nodes": [
            {
                "id": "gid://shopify/LineItem/li1",
                "quantity": 2,
                "refundableQuantity": 1,
            },
            {
                "id": "gid://shopify/LineItem/li2",
                "quantity": 1,
                "refundableQuantity": 1,
            },
        ]
    },
    "fulfillments": [
        {
            "id": "gid://shopify/Fulfillment/f1",
            "name": "Fulfillment 1",
            "totalQuantity": 3,
            "displayStatus": "FULFILLED",
            "requiresShipping": True,
            "trackingInfo": [
                {
                    "number": "TRACK123",
                    "company": "DHL",
                    "url": "https://dhl.com/track?num=TRACK123",
                }
            ],
        }
    ],
    "returns": {
        "nodes": [
            {
                "id": "gid://shopify/Return/r1",
                "name": "Return 1",
                "reverseFulfillmentOrders": {
                    "nodes": [
                        {
                            "reverseDeliveries": {
                                "nodes": [
                                    {
                                        "deliverable": {
                                            "tracking": {
                                                "carrierName": "DHL",
                                                "number": VALID_TRACKING_NUMBER,
                                                "url": "https://dhl.com/track?num=RETURN123",
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            }
        ]
    },
}
