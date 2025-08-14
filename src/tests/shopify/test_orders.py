import pytest
from unittest.mock import patch

from src.models.order import (
    DeliverableTracking,
    Deliverable,
    LineItem,
    MoneyBag,
    MoneyBagSet,
    OrderTransaction,
    ReturnFulfillments,
    ReverseDeliveries,
    ReverseFulfillmentOrder,
    ShopifyOrder,
    TransactionKind,
)
from src.models.tracking import (
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
from src.shopify.orders import retrieve_refundable_shopify_orders

TEST_TRACKING_NUMBER = "123456"


@pytest.fixture(autouse=True)
def mock_slack_notifier():
    
    with patch('src.shopify.orders.slack_notifier') as mock_slack:
        yield mock_slack




def _create_order_with_tracking(tracking_number=None, carrier_name=None):
    """Helper to create order with optional tracking info."""
    money_set = MoneyBagSet(presentmentMoney=MoneyBag(amount=100.0))
    
    # Create tracking if provided
    returns = []
    if tracking_number or carrier_name:
        tracking = DeliverableTracking(number=tracking_number, carrierName=carrier_name)
        deliverable = Deliverable(tracking=tracking)
        reverse_delivery = ReverseDeliveries(deliverable=deliverable)
        rfo = ReverseFulfillmentOrder(reverseDeliveries=[reverse_delivery])
        return_fulfillment = ReturnFulfillments(
            id="return_1", name="Return 1", reverseFulfillmentOrders=[rfo]
        )
        returns = [return_fulfillment]
    
    return ShopifyOrder(
        id="order_test", name="#TEST", tags=[], 
        lineItems=[LineItem(id="li1", quantity=1, refundableQuantity=1)],
        totalPriceSet=money_set,
        transactions=[OrderTransaction(id="tx1", gateway="test", kind=TransactionKind.SALE, amountSet=money_set)],
        returns=returns
    )


def _create_tracking_data(tracking_number, status=TrackingStatus.DELIVERED, sub_status=TrackingSubStatus.DELIVERED_OTHER):
    """Helper to create minimal tracking data."""
    latest_status = LatestStatus(status=status, sub_status=sub_status, sub_status_descr=None)
    track_info = TrackInfo(latest_status=latest_status, latest_event=None, milestone=[])
    return TrackingData(tag="test", number=tracking_number, carrier=7041, param=None, track_info=track_info)


# ----------------------
# __get_order_by_tracking_id
# ----------------------
def test_get_order_by_tracking_id_found():
    order_with_tracking = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    order_without_tracking = _create_order_with_tracking()
    
    found = get_order_by_tracking_id(
        TEST_TRACKING_NUMBER, [order_with_tracking, order_without_tracking]
    )
    assert found is order_with_tracking


def test_get_order_by_tracking_id_not_found():
    order_with_tracking = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    order_without_tracking = _create_order_with_tracking()
    
    found = get_order_by_tracking_id(
        "nonexistent", [order_with_tracking, order_without_tracking]
    )
    assert found is None


# ----------------------
# __generate_tracking_payload
# ----------------------
def test_generate_tracking_payload_valid():
    order_with_tracking = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    order_without_tracking = _create_order_with_tracking()
    
    payload = generate_tracking_payload([order_with_tracking, order_without_tracking])
    assert payload == [{"number": TEST_TRACKING_NUMBER, "carrier": 7041}]  # DHL Paket fallback


def test_generate_tracking_payload_empty():
    assert generate_tracking_payload([]) == []


# ----------------------
# __cleanup_shopify_orders
# ----------------------
def test_cleanup_shopify_orders_keeps_valid():
    order = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    cleaned = cleanup_shopify_orders([order])
    assert len(cleaned) == 1
    assert cleaned[0].id == order.id


def test_cleanup_shopify_orders_discards_invalid():
    order = _create_order_with_tracking()  # No tracking - invalid
    cleaned = cleanup_shopify_orders([order])
    assert len(cleaned) == 0


# ----------------------
# __fetch_tracking_details
# ----------------------
@patch("src.shopify.orders.requests.post")
def test_fetch_tracking_details_matching(mock_post, mock_slack_notifier):
    order = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    tracking_data = _create_tracking_data(TEST_TRACKING_NUMBER)
    
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "data": {"accepted": [tracking_data.model_dump()]}
    }

    result = fetch_tracking_details(
        [{"number": TEST_TRACKING_NUMBER, "carrier": 7041}], [order]
    )
    assert len(result) == 1
    assert result[0][0] is order
    assert isinstance(result[0][1], TrackingData)


@patch("src.shopify.orders.requests.post")
def test_fetch_tracking_details_not_matching_status(mock_post, mock_slack_notifier):
    order = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    tracking_data = _create_tracking_data(TEST_TRACKING_NUMBER, TrackingStatus.NOTFOUND, TrackingSubStatus.NOTFOUND_OTHER)
    
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "data": {"accepted": [tracking_data.model_dump()]}
    }

    result = fetch_tracking_details(
        [{"number": TEST_TRACKING_NUMBER, "carrier": 7041}], [order]
    )
    assert result == []


# ----------------------
# retrieve_refundable_shopify_orders (E2E patched)
# ----------------------
@patch("src.shopify.orders.__fetch_tracking_details")
@patch("src.shopify.orders.__fetch_shopify_orders")
@patch("src.shopify.orders.__register_trackings")
def test_retrieve_refundable_shopify_orders_success_e2e(
    _,
    mock_fetch_shopify_orders,
    mock_fetch_tracking_details,
    mock_slack_notifier,
):
    order = _create_order_with_tracking(TEST_TRACKING_NUMBER, "DHL")
    tracking = _create_tracking_data(TEST_TRACKING_NUMBER)

    mock_fetch_tracking_details.return_value = [(order, tracking)]

    mock_fetch_shopify_orders.return_value = {
        "data": get_graphql_query_response()
    }
    
    result = retrieve_refundable_shopify_orders()
    assert result == [(order, tracking)]


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
                    "number": "4238482203",
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
                                                "number": "3937299393",
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
                    "number": "10i30i020209",
                    "company": "FedEx",
                    "url": "https://fedex.com/track?num=TRACK456",
                }
            ],
        }
    ],
    "returns": {"nodes": []},
}
