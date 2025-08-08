import time

import requests

from src.config import (
    REQUEST_TIMEOUT,
    RETURN_TRACKING_STATUS,
    RETURN_TRACKING_SUB_STATUS,
    SHOPIFY_ACCESS_TOKEN,
    SHOPIFY_STORE_URL,
    TRACKING_API_KEY,
    TRACKING_BASE_URL,
)
from src.logger import get_logger
from src.models.order import ShopifyOrder
from src.models.tracking import TrackingData

from .graph_ql_queries import RETURN_ORDERS_QUERY

logger = get_logger(__name__)

REQUEST_PAGINATION_SIZE = 12
MAX_SHOPIFY_ORDER_DATA = 10_000


def __get_order_by_tracking_id(tracking_number: str, orders: list[ShopifyOrder]):
    logger.debug(f"Searching for order with tracking number: {tracking_number}")
    for order in orders:
        for rf in order.valid_return_shipment.reverseFulfillmentOrders:
            for rd in rf.reverseDeliveries:
                if rd.deliverable.tracking.number == tracking_number:
                    logger.debug(f"Order found for tracking number: {tracking_number}")
                    return order
    logger.debug(f"No order found for tracking number: {tracking_number}")


def __generate_tracking_payload(orders: list[ShopifyOrder]):
    
    logger.info(f"Generating tracking payload for {len(orders)} orders")
    payload = []

    if len(orders) < 1:
        return payload
    
    for order in orders:
        carrier_code = None
        tracking_number = None
        # Determine the return carrier
        for index, rf in enumerate(
            order.valid_return_shipment.reverseFulfillmentOrders
        ):

            has_reverse_deliveries = len(rf.reverseDeliveries) > 0

            if not has_reverse_deliveries:
                continue

            deliverable = rf.reverseDeliveries[index].deliverable

            if rf.reverseDeliveries and deliverable:
                # Get carrier code from number
                carrier_code = deliverable.tracking.carrierName
                tracking_number = deliverable.tracking.number

        if carrier_code and not carrier_code.isdigit():
            logger.debug(
                f"Carrier code '{carrier_code}' is not digit, setting to 7041 (DHL Paket)"
            )
            carrier_code = 7041  # DHL Paket

        if tracking_number:
            logger.debug(
                f"Adding tracking number: {tracking_number}, carrier: {carrier_code}"
            )
            payload.append({"number": tracking_number, "carrier": carrier_code})

    logger.info(f"Generated tracking payload with {len(payload)} entries")
    return payload


def __register_trackings(payload: list):

    if len(payload) < 1:
        return

    url = f"{TRACKING_BASE_URL}/register"
    headers = {"Content-Type": "application/json", "17token": TRACKING_API_KEY}

    payload_segments = [payload[i : i + 40] for i in range(0, len(payload), 40)]

    logger.info(
        f"Registering {len(payload)} trackings in {len(payload_segments)} segments"
    )
    for payload in payload_segments:
        logger.debug(f"Registering tracking segment with {len(payload)} entries")
        response = requests.post(
            url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        accepted_trackings = response.json().get("data", {}).get("accepted", [])
        rejected_trackings = response.json().get("data", {}).get("rejected", [])
        logger.info(f"[Registered Trackings]: {len(accepted_trackings)}")
        logger.info(f"[UnRegistered Trackings]: {len(rejected_trackings)}")


def __fetch_tracking_details(payload: list, orders: list[ShopifyOrder]):
    """
    Fetch tracking details for the given payload and match them with Shopify orders.
    """
    logger.info(f"Fetching tracking details for {len(payload)} payload entries")
    url = f"{TRACKING_BASE_URL}/gettrackinfo"
    headers = {"Content-Type": "application/json", "17token": TRACKING_API_KEY}
    response = requests.post(
        url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()

    # List to hold tuples of (ShopifyOrder, TrackingData) for matched and valid trackings
    order_and_trackings: list[tuple[ShopifyOrder, TrackingData]] = []

    # Extract tracking data from the API response
    trackings: list = response.json().get("data", {}).get("accepted")
    logger.info(
        f"Received {len(trackings) if trackings else 0} tracking entries from API"
    )

    for tracking_data in trackings:
        try:
            _tracking = TrackingData(**tracking_data)

            # Find the corresponding Shopify order by tracking number
            associated_order = __get_order_by_tracking_id(_tracking.number, orders)

            # Skip if either tracking-info or associated-order is missing
            if not (_tracking.track_info and associated_order):
                logger.debug(
                    f"Skipping tracking number: {_tracking.number} (missing info or order)"
                )
                continue

            # Extract tracking status and sub-status
            tracking_status = _tracking.track_info.latest_status.status
            tracking_sub_status = _tracking.track_info.latest_status.sub_status

            # Only add to result if status and sub-status match the return criteria
            if (
                tracking_status == RETURN_TRACKING_STATUS
                and tracking_sub_status == RETURN_TRACKING_SUB_STATUS
            ):
                logger.info(
                    f"Tracking number {_tracking.number} matches return criteria"
                )
                order_and_trackings.append((associated_order, _tracking))
            else:
                logger.debug(
                    f"Tracking number {_tracking.number} does not match return criteria"
                )

        except Exception as e:
            # Log any errors encountered during parsing
            logger.error(f"[Parsing Error]: {tracking_data.get('number')} -- {e}")

    logger.info(f"Matched {len(order_and_trackings)} orders with valid tracking data")
    return order_and_trackings


def __cleanup_shopify_orders(orders: list[ShopifyOrder]):
    """Clean up Shopify orders by filtering out orders with zero amount and those that do not require shipping."""
    logger.info(f"Cleaning up {len(orders)} Shopify orders")
    # Initialize a list to hold cleaned orders
    cleaned_orders = []
    # Remove and get the last order from the list
    order = orders.pop()

    while True:

        # Only keep orders that have a valid return shipment
        if order.valid_return_shipment:
            cleaned_orders.append(order)
            logger.debug(f"Order {getattr(order, 'id', None)} added to cleaned orders")

        try:
            # Pop the next order from the list
            order = orders.pop()
        except IndexError:
            break

    logger.info(f"Cleaned orders count: {len(cleaned_orders)}")
    return cleaned_orders


def retrieve_fulfilled_shopify_orders():
    """
    Retrieve all matching shopify orders and merge them with their 17track tracking information

    """
    logger.info("Starting retrieval of fulfilled Shopify orders")
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

    # graph-ql variables
    variables = {
        "first": REQUEST_PAGINATION_SIZE,
        "query": "return_status:IN_PROGRESS, financial_status:PAID",
    }

    # Loop through paginated Shopify orders until all are retrieved or the maximum is reached
    while has_next_page:

        # Stop fetching if we've reached the maximum allowed number of orders
        if len(orders) >= MAX_SHOPIFY_ORDER_DATA:
            logger.warning("Reached maximum allowed number of Shopify orders")
            has_next_page = False
            break

        # Set the cursor for pagination
        variables["after"] = cursor

        logger.debug(f"Requesting Shopify orders page with cursor: {cursor}")
        # Making the GraphQL request to Shopify
        response = requests.post(
            endpoint,
            headers=headers,
            json={"query": RETURN_ORDERS_QUERY, "variables": variables},
            timeout=REQUEST_TIMEOUT,
        )

        # Raise an error if the request failed
        response.raise_for_status()

        data = response.json()

        # Extract orders data from the response (graphQL)
        orders_data = data.get("data", {}).get("orders", {})
        edges = orders_data.get("edges", [])
        errors = data.get("errors")

        # Log any errors returned by Shopify
        if errors:
            logger.error(f"[Error]: {errors}")

        logger.info(f"Fetched {len(edges)} orders from Shopify")
        for edge in edges:
            node = edge["node"]
            returns_nodes = node["returns"]["nodes"]

            line_items = node["lineItems"]["nodes"]
            node["lineItems"] = line_items

            # Flatten nested return data for easier processing
            for return_data in returns_nodes:

                reverse_fulfillments_orders_nodes = return_data[
                    "reverseFulfillmentOrders"
                ]["nodes"]

                return_data["reverseFulfillmentOrders"] = (
                    reverse_fulfillments_orders_nodes
                )

                for r_fulfillment in reverse_fulfillments_orders_nodes:
                    reverse_delivery_nodes = r_fulfillment["reverseDeliveries"]["nodes"]
                    r_fulfillment["reverseDeliveries"] = reverse_delivery_nodes

            # Add the processed order to the list
            node["returns"] = returns_nodes
            orders.append(ShopifyOrder(**node))

        # Update pagination info for the next loop iteration
        page_info = orders_data.get("pageInfo", {})
        has_next_page = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor", None)

        trackings = []

        # If no orders were found, return an empty list
        if not len(orders):
            logger.info("No orders found, returning empty tracking list")
            return trackings

        # Clean up orders to remove those not eligible for processing (refunding)
        cleaned_orders = __cleanup_shopify_orders(orders)

        # If no cleaned orders remain, return an empty list
        if not len(cleaned_orders):
            logger.info(
                "No cleaned orders remain after filtering, returning empty tracking list"
            )
            return trackings

        # Generate the payload for tracking registration
        payload = __generate_tracking_payload(cleaned_orders)

        # Register all trackings with the tracking API
        __register_trackings(payload)

        # Wait for a short period to allow tracking registration to sync
        logger.info("Waiting 5 seconds for tracking registration to sync")
        time.sleep(5)

        # Fetch tracking details and match them with Shopify orders
        trackings = __fetch_tracking_details(payload, cleaned_orders)
        logger.info(f"Returning {len(trackings)} matched trackings")
        return trackings


__all__ = ("retrieve_fulfilled_shopify_orders",)
