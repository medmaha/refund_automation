from unittest.mock import patch

from src.shopify.orders import __cleanup_shopify_orders as cleanup_shopify_orders
<<<<<<< HEAD
from src.shopify.orders import __fetch_tracking_details as fetch_tracking_details
from src.shopify.orders import __generate_tracking_payload as generate_tracking_payload
from src.shopify.orders import __get_order_by_tracking_id as get_order_by_tracking_id
from src.shopify.orders import retrieve_refundable_shopify_orders
=======
from src.shopify.orders import (
    retrieve_refundable_shopify_orders,
)
>>>>>>> stage

# trunk-ignore(ruff/F403)
from src.tests.shopify.fixtures import *


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


<<<<<<< HEAD
# ----------------------
# __fetch_tracking_details
# ----------------------
@patch("src.shopify.orders.requests.post")
def test_fetch_tracking_details_matching(mock_post):
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
def test_fetch_tracking_details_not_matching_status(mock_post):
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
=======
@patch("src.shopify.orders.__fetch_all_shopify_orders")
@patch("src.shopify.orders.fetch_tracking_details")
@patch("src.shopify.orders.register_orders_trackings")
@patch("src.shopify.orders.slack_notifier")
def test_retrieve_refundable_orders(
    mock_slack,
    mock_register,
    mock_fetch_tracking,
    mock_fetch_orders,
    dummy_order,
    dummy_tracking,
>>>>>>> stage
):
    mock_fetch_orders.return_value = [dummy_order]
    mock_fetch_tracking.return_value = [(dummy_order, dummy_tracking)]
    mock_register.return_value = None

<<<<<<< HEAD
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
=======
    with patch("src.shopify.orders.time.sleep") as mock_sleep:
        results = retrieve_refundable_shopify_orders()
        assert len(results) == 1
        assert results[0][0] == dummy_order
        mock_sleep.assert_called_with(5)
>>>>>>> stage
