import time

import requests

from src.config import (
    REQUEST_TIMEOUT,
    SHOPIFY_ACCESS_TOKEN,
    SHOPIFY_STORE_URL,
)
from src.logger import get_logger
from src.models.order import ShopifyOrder
from src.shopify.graph_ql_queries import RETURN_ORDERS_QUERY
from src.shopify.tracking import (
    fetch_tracking_details,
    generate_tracking_payload,
    register_orders_trackings,
)
from src.utils.slack import slack_notifier

logger = get_logger(__name__)

REQUEST_PAGINATION_SIZE = 12
MAX_SHOPIFY_ORDER_DATA = 10_000

TRACKING_SEGMENT_SIZE = 40  # Maximum trackings per API call

ELIGIBLE_ORDERS_QUERY = """
name:1016
financial_status:PAID OR
financial_status:PARTIALLY_PAID OR
financial_status:PARTIALLY_REFUNDED AND
(return_status:RETURNED OR return_status:IN_PROGRESS) AND
(fulfillment_status:FULFILLED OR fulfillment_status:PARTIALLY_FULFILLED)
"""

def __cleanup_shopify_orders(orders: list[ShopifyOrder]):
    logger.info(f"Cleaning up {len(orders)} Shopify orders")

    cleaned_orders = []
    # Remove and get the last order from the list
    order = orders.pop()

    while True:
        # Only keep orders that have at least one valid shipment
        if order.valid_return_shipment:
            cleaned_orders.append(order)
            logger.debug(f"Order {getattr(order, 'id', None)} added to cleaned orders")
        try:
            # Pop the next order from the list
            order = orders.pop()
        except IndexError:
            # Break out of the while loop if we run out of orders
            break
        except Exception as e:
            logger.error(
                "Unhandled error while cleaning orders", extra={"error": str(e)}
            )

    logger.info(f"Cleaned orders count: {len(cleaned_orders)}")
    return cleaned_orders


def __fetch_shopify_orders(endpoint: str, headers: dict, variables: dict):
    # Making the GraphQL request to Shopify

    response = requests.post(
        endpoint,
        headers=headers,
        json={"query": RETURN_ORDERS_QUERY, "variables": variables},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    return response.json()


def __fetch_all_shopify_orders():
    """Fetch all shopify orders using pagination."""

    logger.info(
        f"Fetching all refundable Shopify orders: max({MAX_SHOPIFY_ORDER_DATA})"
    )

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    endpoint = (
        f"https://{SHOPIFY_STORE_URL}.myshopify.com/admin/api/2025-07/graphql.json"
    )

    cursor = None
    has_next_page = True
    orders: list[ShopifyOrder] = []

    # GraphQL variables for order filtering
    variables = {
        "first": REQUEST_PAGINATION_SIZE,
        "query": ELIGIBLE_ORDERS_QUERY,
    }

    # Loop through paginated results
    while has_next_page:
        # Prevent infinite loops and memory issues
        if len(orders) >= MAX_SHOPIFY_ORDER_DATA:
            logger.warning(
                f"Reached maximum order limit ({MAX_SHOPIFY_ORDER_DATA}), stopping pagination"
            )
            break

        # Set pagination cursor
        variables["after"] = cursor

        logger.debug(f"Requesting orders page with cursor: {cursor}")

        try:
            data = __fetch_shopify_orders(
                endpoint=endpoint, headers=headers, variables=variables
            )

            errors = data.get("errors")

            if errors:
                logger.error(f"Shopify API errors: {errors}")
                slack_notifier.send_error(
                    "Shopify API errors",
                    details={
                        "successfully_fetched": f"[{len(orders)}] Orders",
                        "errors": errors,
                        "api_requests_vars": variables,
                    },
                )

                # Break out of this loop for early return
                break

            else:
                # Extract and validate response data
                orders_data = data.get("data", {}).get("orders", {})
                edges = orders_data.get("edges", [])

                logger.info(f"Fetched {len(edges)} orders from Shopify")

                # Process each order
                for edge in edges:
                    try:
                        node = parse_graphql_order_data(edge["node"])
                        orders.append(ShopifyOrder(**node))
                    except Exception as e:
                        logger.error(
                            f"Error parsing order data: {e}",
                            extra={
                                "order_id": edge.get("node", {}).get("id", "unknown")
                            },
                        )
                        continue

            # Update pagination info
            page_info = orders_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor", None)

        except Exception as e:
            logger.error(
                f"Failed to fetch Shopify orders: {e}",
                extra={"variables": variables},
                exc_info=True,
            )
            slack_notifier.send_error(
                "Failed to fetch Shopify orders", details={"error": str(e), **variables}
            )
            break

    if orders:
        logger.info(f"Successfully fetched {len(orders)} total orders")
    return orders


def __process_orders_for_tracking(orders: list[ShopifyOrder]):
    """Process orders to generate and register tracking information."""
    if not orders:
        logger.info("No orders to process")
        return []

    logger.info(f"Processing {len(orders)} orders for tracking")
    slack_notifier.send_info(f"Processing {len(orders)} orders for tracking")

    # Clean up orders to remove ineligible ones
    cleaned_orders = __cleanup_shopify_orders(
        orders.copy()
    )  # Use copy to avoid modifying original

    if not cleaned_orders:
        logger.info("No eligible orders remain after cleanup")
        slack_notifier.send_info("No eligible orders found after filtering")
        return []

    logger.info(
        f"Cleaned orders: {len(cleaned_orders)} eligible out of {len(orders)} total"
    )
    slack_notifier.send_info(
        "Order filtering complete",
        details={"eligible": len(cleaned_orders), "total": len(orders)},
    )

    if not cleaned_orders:
        return []

    # Generate tracking payload
    payload = generate_tracking_payload(cleaned_orders)

    if not payload:
        logger.warning("No tracking payload generated")
        return []

    # Register trackings with the API
    register_orders_trackings(payload)

    # Wait for tracking registration to sync
    sync_delay = 5  # seconds
    logger.info(f"Waiting {sync_delay} seconds for tracking registration to sync")
    time.sleep(sync_delay)

    # Fetch and match tracking details with orders
    trackings = fetch_tracking_details(payload, cleaned_orders)

    logger.info(
        f"Tracking processing complete: {len(trackings)} matched trackings",
        extra={
            "total_orders": len(orders),
            "eligible_orders": len(cleaned_orders),
            "matched_trackings": len(trackings),
        },
    )

    return trackings


def retrieve_refundable_shopify_orders():
    """
    Retrieve all matching Shopify orders and merge them with their 17track tracking information.

    Returns:
        List of tuples containing (ShopifyOrder, TrackingData) for eligible orders
    """
    try:
        # Step 1: Fetch all relevant Shopify orders
        orders = __fetch_all_shopify_orders()

        if not orders:
            return []

        # Step 2: Process orders for tracking information
        trackings = __process_orders_for_tracking(orders)

        return trackings

    except Exception as e:
        error_msg = f"Failed to retrieve fulfilled Shopify orders: {e}"
        logger.error(error_msg, exc_info=True)
        slack_notifier.send_error(error_msg, details={"error": str(e)})
        raise


def parse_graphql_order_data(node: dict):
    # Handle returns data - check if it's already structured or needs extraction
    _return = node.get("returns", {})
    if isinstance(_return, dict) and "nodes" in _return:
        returns_nodes = _return["nodes"]
    elif isinstance(_return, list):
        returns_nodes = _return
    else:
        returns_nodes = []

    # Handle lineItems data - check if it's already structured or needs extraction
    line_items_data = node.get("lineItems", {})
    if isinstance(line_items_data, dict) and "nodes" in line_items_data:
        line_items = line_items_data["nodes"]
    elif isinstance(line_items_data, list):
        line_items = line_items_data
    else:
        line_items = []

    discount_applications = node.get("discountApplications", {}).get("edges", [])
    node["discountApplications"] = discount_applications

    for index, discount_app in enumerate(node["discountApplications"]):
        node["discountApplications"][index] = discount_app.get("node")

    order_refunds = node.get("refunds", [])
    if isinstance(order_refunds, dict) and "nodes" in order_refunds:
        order_refunds = order_refunds["nodes"]
    elif isinstance(order_refunds, list):
        order_refunds = order_refunds
    else:
        order_refunds = []

    # Flatten nested refund data for easier processing
    for refund in order_refunds:
        return_line_items = refund.get("refundLineItems", {})

        if isinstance(return_line_items, dict) and "nodes" in return_line_items:
            refund["refundLineItems"] = return_line_items["nodes"]
        elif isinstance(return_line_items, list):
            refund["refundLineItems"] = return_line_items
        else:
            refund["refundLineItems"] = []

        refund["refundShippingLines"] = refund.get("refundShippingLines", {}).get(
            "edges", []
        )

    # Flatten nested return data for easier processing
    for _return in returns_nodes:
        return_line_items = _return.get("returnLineItems", {})

        if isinstance(return_line_items, dict) and "nodes" in return_line_items:
            _return["returnLineItems"] = return_line_items["nodes"]
        elif isinstance(return_line_items, list):
            _return["returnLineItems"] = return_line_items
        else:
            _return["returnLineItems"] = []

        # Handle reverseFulfillmentOrders
        reverse_fulfillments_data = _return.get("reverseFulfillmentOrders", {})
        if (
            isinstance(reverse_fulfillments_data, dict)
            and "nodes" in reverse_fulfillments_data
        ):
            reverse_fulfillments_orders_nodes = reverse_fulfillments_data["nodes"]
        elif isinstance(reverse_fulfillments_data, list):
            reverse_fulfillments_orders_nodes = reverse_fulfillments_data
        else:
            reverse_fulfillments_orders_nodes = []

        _return["reverseFulfillmentOrders"] = reverse_fulfillments_orders_nodes

        for r_fulfillment in reverse_fulfillments_orders_nodes:
            # Handle reverseDeliveries
            reverse_deliveries_data = r_fulfillment.get("reverseDeliveries", {})
            if (
                isinstance(reverse_deliveries_data, dict)
                and "nodes" in reverse_deliveries_data
            ):
                reverse_delivery_nodes = reverse_deliveries_data["nodes"]
            elif isinstance(reverse_deliveries_data, list):
                reverse_delivery_nodes = reverse_deliveries_data
            else:
                reverse_delivery_nodes = []

            r_fulfillment["reverseDeliveries"] = reverse_delivery_nodes

    # Add the processed data to the node
    node["lineItems"] = line_items
    node["returns"] = returns_nodes
    node["refunds"] = order_refunds
    return node
