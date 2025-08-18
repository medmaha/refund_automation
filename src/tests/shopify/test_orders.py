from unittest.mock import patch

from src.shopify.orders import __cleanup_shopify_orders as cleanup_shopify_orders
from src.shopify.orders import (
    retrieve_refundable_shopify_orders,
)

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
):
    mock_fetch_orders.return_value = [dummy_order]
    mock_fetch_tracking.return_value = [(dummy_order, dummy_tracking)]
    mock_register.return_value = None

    with patch("src.shopify.orders.time.sleep") as mock_sleep:
        results = retrieve_refundable_shopify_orders()
        assert len(results) == 1
        assert results[0][0] == dummy_order
        mock_sleep.assert_called_with(5)
