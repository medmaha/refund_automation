import time

import requests

from src.config import (
    REQUEST_TIMEOUT,
    SHOPIFY_API_HEADERS,
    SHOPIFY_API_URL,
)
from src.logger import get_logger
from src.models.order import ShopifyOrder
from src.models.tracking import TrackingData
from src.shopify.graph_ql_queries import RETURN_ORDERS_QUERY
from src.shopify.tracking import (
    fetch_tracking_details,
    generate_tracking_payload,
    register_orders_trackings,
)
from src.utils.audit import audit_logger
from src.utils.slack import slack_notifier

logger = get_logger(__name__)

REQUEST_PAGINATION_SIZE = 12
MAX_SHOPIFY_ORDER_DATA = 10_000
DEFAULT_CARRIER_CODE = 7041  # DHL Paket
TRACKING_SEGMENT_SIZE = 40  # Maximum trackings per API call

<<<<<<< HEAD

def __get_order_by_tracking_id(tracking_number: str, orders: list[ShopifyOrder]):
    logger.debug(f"Searching for order with tracking number: {tracking_number}")
    for order in orders:
        # Skip orders without valid return shipments
        if not order.valid_return_shipment:
            continue

        for rf in order.valid_return_shipment.reverseFulfillmentOrders:
            for rd in rf.reverseDeliveries:
                if rd.deliverable.tracking.number == tracking_number:
                    logger.debug(f"Order found for tracking number: {tracking_number}")
                    return order
    logger.debug(f"No order found for tracking number: {tracking_number}")


def __generate_tracking_payload(orders: list[ShopifyOrder]):
    """Generate tracking payload from eligible orders."""

    logger.info(f"Generating tracking payload for {len(orders)} orders")
    payload = []

    if len(orders) < 1:
        return payload

    for order in orders:
        carrier_code = None
        tracking_number = None

        if not order.valid_return_shipment:
            continue

        for index, rfo in enumerate(order.valid_return_shipment.reverseFulfillmentOrders):

            if not len(rfo.reverseDeliveries) > 0:
                continue
            
            # FIXME: loop through or use current indexing approach
            deliverable = rfo.reverseDeliveries[index].deliverable

            if deliverable:
                carrier_code = deliverable.tracking.carrierName
                tracking_number = deliverable.tracking.number

        if carrier_code and not carrier_code.isdigit():
            logger.debug(
                f"Carrier code '{carrier_code}' is not digit, setting to {DEFAULT_CARRIER_CODE} (DHL Paket)"
            )
            carrier_code = DEFAULT_CARRIER_CODE

        if tracking_number:
            logger.debug(
                f"Adding tracking number: {tracking_number}, carrier: {carrier_code}"
            )
            payload.append({"number": tracking_number, "carrier": carrier_code})

    logger.info(f"Generated tracking payload with {len(payload)} entries")
    return payload


def __register_trackings(payload: list):
    """Register tracking numbers with the tracking API using retry logic and better error handling."""

    if len(payload) < 1:
        return

    url = f"{TRACKING_BASE_URL}/register"
    headers = {"Content-Type": "application/json", "17token": TRACKING_API_KEY}

    # Split payload into manageable segments
    payload_segments = [payload[i : i + TRACKING_SEGMENT_SIZE] for i in range(0, len(payload), TRACKING_SEGMENT_SIZE)]

    logger.info(
        f"Registering {len(payload)} trackings in {len(payload_segments)} segments"
    )
    
    total_registered = 0
    total_rejected = 0
    
    for segment_idx, segment_payload in enumerate(payload_segments, 1):
        try:
            logger.debug(f"Registering tracking segment {segment_idx}/{len(payload_segments)} with {len(segment_payload)} entries")
            
            # Use retry mechanism from utils.retry
            from src.utils.retry import exponential_backoff_retry
            
            @exponential_backoff_retry(
                exceptions=(requests.exceptions.RequestException, requests.exceptions.Timeout)
            )
            def _register_tracking_segment():
                response = requests.post(
                    url, headers=headers, json=segment_payload, timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()
                return response
            
            response = _register_tracking_segment()
            response_data = response.json()
            
            accepted_trackings = response_data.get("data", {}).get("accepted", [])
            rejected_trackings = response_data.get("data", {}).get("rejected", [])
            
            total_registered += len(accepted_trackings)

            # TODO: filter out rejected with reason (already registered) and add to accepted
            total_rejected += len(rejected_trackings)
            
            logger.info(f"Segment {segment_idx}: {len(accepted_trackings)} registered, {len(rejected_trackings)} rejected")
            
            # Log rejected trackings for troubleshooting
            if rejected_trackings:
                logger.warning(
                    f"Rejected trackings in segment {segment_idx}", 
                    extra={"rejected_count": len(rejected_trackings), "rejected_trackings": rejected_trackings}
                )
                
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Failed to register tracking segment {segment_idx}/{len(payload_segments)}: {e}",
                extra={
                    "segment_index": segment_idx,
                    "segment_size": len(segment_payload),
                    "error": str(e)
                }
            )
            slack_notifier.send_error(
                f"Failed to register tracking segment {segment_idx}", 
                details={"error": str(e), "segment_size": len(segment_payload)}
            )
            continue
            
        except Exception as e:
            logger.error(
                f"Unexpected error registering tracking segment {segment_idx}: {e}",
                extra={
                    "segment_index": segment_idx,
                    "segment_size": len(segment_payload),
                    "error": str(e)
                },
                exc_info=True
            )
            continue
    
    logger.info(f"Total tracking registration results: {total_registered} registered, {total_rejected} rejected")
    slack_notifier.send_info(
        "Tracking Registration Summary", 
        details={
            "registered": total_registered, 
            "rejected": total_rejected, 
            "total_segments": len(payload_segments)
        }
    )


def __fetch_tracking_details(payload: list, orders: list[ShopifyOrder]):
    """
    Fetch tracking details for the given payload and match them with Shopify orders.
    """
    logger.info(f"Fetching tracking details for {len(payload)} payload entries")
    
    if not payload:
        logger.warning("Empty payload provided to fetch tracking details")
        return []
    
    url = f"{TRACKING_BASE_URL}/gettrackinfo"
    headers = {"Content-Type": "application/json", "17token": TRACKING_API_KEY}
    
    skipped_tracking_info: list = []

    try:
        # Use retry mechanism from utils.retry
        from src.utils.retry import exponential_backoff_retry
        
        @exponential_backoff_retry(
            exceptions=(requests.exceptions.RequestException, requests.exceptions.Timeout)
        )
        def _fetch_tracking_info():
            response = requests.post(
                url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response
        
        response = _fetch_tracking_info()
        response_data = response.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to fetch tracking details after retries: {e}",
            extra={
                "payload_size": len(payload),
                "error": str(e)
            }
        )
        slack_notifier.send_error(
            "Failed to fetch tracking details", 
            details={"error": str(e), "payload": str(payload), "payload_size": len(payload)}
        )
        return []
    
    except Exception as e:
        logger.error(
            f"Unexpected error fetching tracking details: {e}",
            extra={
                "payload_size": len(payload),
                "error": str(e)
            },
            exc_info=True
        )
        return []

    # List to hold tuples of (ShopifyOrder, TrackingData) for matched and valid trackings
    order_and_trackings: list[tuple[ShopifyOrder, TrackingData]] = []

    # Extract tracking data from the API response
    trackings: list = response_data.get("data", {}).get("accepted", [])
    logger.info(
        f"Received {len(trackings)} tracking entries from API"
    )
    
    if not trackings:
        logger.warning("No tracking data received from API")
        return []

    parsing_errors = 0
    processed_count = 0
    
    for tracking_data in trackings:
        processed_count += 1
        try:
            if not isinstance(tracking_data, dict):
                logger.warning(f"Invalid tracking data format: {type(tracking_data)}")
                parsing_errors += 1
                continue
                
            _tracking = TrackingData(**tracking_data)

            # Find the corresponding Shopify order by tracking number
            associated_order = __get_order_by_tracking_id(_tracking.number, orders)

            # Skip if either tracking-info or associated-order is missing
            if not (_tracking.track_info and associated_order):
                logger.debug(
                    f"Skipping tracking number: {_tracking.number} (missing tracking info)",
                    extra={
                        "has_track_info": _tracking.track_info is not None,
                        "has_associated_order": associated_order is not None
                    }
                )
                continue

            # Extract tracking status and sub-status with validation
            try:
                tracking_status = _tracking.track_info.latest_status.status
                tracking_sub_status = _tracking.track_info.latest_status.sub_status
            except AttributeError as e:
                logger.warning(
                    f"Invalid tracking status structure for {_tracking.number}: {e}",
                    extra={"tracking_number": _tracking.number}
                )
                continue

            # Only add to result if status and sub-status match the return criteria
            if (
                tracking_status == TrackingStatus.DELIVERED
                and tracking_sub_status == TrackingSubStatus.DELIVERED_OTHER
            ):
                logger.info(
                    f"Tracking number {_tracking.number} matches return criteria",
                    extra={
                        "tracking_number": _tracking.number,
                        "status": tracking_status.value if hasattr(tracking_status, 'value') else str(tracking_status),
                        "sub_status": tracking_sub_status.value if hasattr(tracking_sub_status, 'value') else str(tracking_sub_status)
                    }
                )
                order_and_trackings.append((associated_order, _tracking))
            else:
                logger.debug(
                    f"Tracking number {_tracking.number} does not match return criteria",
                    extra={
                        "tracking_number": _tracking.number,
                        "status": tracking_status.value if hasattr(tracking_status, 'value') else str(tracking_status),
                        "sub_status": tracking_sub_status.value if hasattr(tracking_sub_status, 'value') else str(tracking_sub_status)
                    }
                )

        except ValueError as e:
            # Pydantic validation error
            parsing_errors += 1
            logger.error(
                f"Validation error for tracking data: {e}",
                extra={
                    "tracking_number": tracking_data.get('number', 'unknown'),
                    "error": str(e)
                }
            )
            
        except Exception as e:
            # Any other parsing error
            parsing_errors += 1
            logger.error(
                f"Parsing error for tracking data: {e}",
                extra={
                    "tracking_number": tracking_data.get('number', 'unknown'),
                    "error": str(e),
                    "tracking_data_keys": list(tracking_data.keys()) if isinstance(tracking_data, dict) else None
                },
                exc_info=True
            )
    
    # Log summary statistics
    logger.info(
        f"Tracking details processing complete: {len(order_and_trackings)} matched, {parsing_errors} errors out of {processed_count} total",
        extra={
            "matched_orders": len(order_and_trackings),
            "parsing_errors": parsing_errors,
            "processed_count": processed_count,
            "success_rate": f"{((processed_count - parsing_errors) / processed_count * 100):.1f}%" if processed_count > 0 else "0%"
        }
    )
    
    if parsing_errors > 0:
        slack_notifier.send_warning(
            f"Tracking parsing completed with {parsing_errors} errors",
            details={
                "matched": len(order_and_trackings),
                "errors": parsing_errors,
                "total": processed_count
            }
        )
    
    return order_and_trackings


def __cleanup_shopify_orders(orders: list[ShopifyOrder]):
    """Clean up Shopify orders by filtering out orders with zero amount and those that do not require shipping."""

    logger.info(f"Cleaning up {len(orders)} Shopify orders")

    cleaned_orders = []

=======
ELIGIBLE_ORDERS_QUERY = (
    "(return_status:IN_PROGRESS) AND "
    "(fulfillment_status:FULFILLED OR fulfillment_status:PARTIAL) AND "
    "(financial_status:PAID OR financial_status:PARTIALLY_PAID OR financial_status:PARTIALLY_REFUNDED) "
)


def __cleanup_shopify_orders(orders: list[ShopifyOrder]):
    logger.info(f"Cleaning up {len(orders)} Shopify orders")

>>>>>>> stage
    # Remove and get the last order from the list
    cleaned_orders = []

    try:
        order = orders.pop()
        while True:
            # Only keep orders that have at least one valid shipment
            if order.get_valid_return_shipment():
                cleaned_orders.append(order)
                logger.debug(
                    f"Order {getattr(order, 'id', None)} added to cleaned orders"
                )
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

<<<<<<< HEAD
        try:
            # Pop the next order from the list
            order = orders.pop()
        except IndexError:
            break
        except Exception as e:
            logger.error(f"Unhandled error while cleaning orders", extra={"error": str(e)})

    logger.info(f"Cleaned orders count: {len(cleaned_orders)}")
    return cleaned_orders
=======
    except Exception as e:
        logger.error("Unhandled error while cleaning orders", extra={"error": str(e)})
        return cleaned_orders
>>>>>>> stage


def __fetch_shopify_orders(variables: dict):
    # Making the GraphQL request to Shopify
    try:
        response = requests.post(
            SHOPIFY_API_URL,
            headers=SHOPIFY_API_HEADERS,
            timeout=REQUEST_TIMEOUT,
            json={"query": RETURN_ORDERS_QUERY, "variables": variables},
        )
        response.raise_for_status()
        audit_logger.log_api_interaction(
            order_id="",
            endpoint=SHOPIFY_API_URL,
            request_type="graphql",
            status_code=response.status_code,
            request_id=f"fetch_shopify_orders_{time.time()}",
            response_time_ms=response.elapsed.total_seconds() * 1000,
            error="",
        )
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error("Unhandled error while fetching orders", extra={"error": str(e)})
        audit_logger.log_api_interaction(
            order_id="",
            endpoint=SHOPIFY_API_URL,
            request_type="graphql",
            status_code=400,
            request_id="fetch_shopify_orders",
            response_time_ms=0,
            error=str(e),
        )
        raise e


def __fetch_all_shopify_orders():
    """Fetch all shopify orders using pagination."""

<<<<<<< HEAD

def __fetch_all_shopify_orders():
    """Fetch all shopify orders using pagination."""
    logger.info("Starting retrieval of Shopify orders")
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    endpoint = f"https://{SHOPIFY_STORE_URL}.myshopify.com/admin/api/2025-07/graphql.json"
    
    cursor = None
    has_next_page = True
    orders: list[ShopifyOrder] = []
    
=======
    logger.info(
        f"Fetching all refundable Shopify orders: max({MAX_SHOPIFY_ORDER_DATA})"
    )

    cursor = None
    has_next_page = True
    orders: list[ShopifyOrder] = []

>>>>>>> stage
    # GraphQL variables for order filtering
    variables = {
        "first": REQUEST_PAGINATION_SIZE,
        "query": ELIGIBLE_ORDERS_QUERY,
    }
<<<<<<< HEAD
    
=======

>>>>>>> stage
    # Loop through paginated results
    while has_next_page:
        # Prevent infinite loops and memory issues
        if len(orders) >= MAX_SHOPIFY_ORDER_DATA:
            logger.warning(
                f"Reached maximum order limit ({MAX_SHOPIFY_ORDER_DATA}), stopping pagination"
<<<<<<< HEAD
            )
            break
        
        # Set pagination cursor
        variables["after"] = cursor
        
        logger.debug(f"Requesting orders page with cursor: {cursor}")
        
        try:
            data = __fetch_shopify_orders(endpoint=endpoint, headers=headers, variables=variables)
            
            # Extract and validate response data
            orders_data = data.get("data", {}).get("orders", {})
            edges = orders_data.get("edges", [])
            errors = data.get("errors")
            
            if errors:
                logger.error(f"Shopify API errors: {errors}")
                slack_notifier.send_error("Shopify API errors", details={"errors": errors})
            
            logger.info(f"Fetched {len(edges)} orders from Shopify")
            
            # Process each order
            for edge in edges:
                try:
                    node = parse_graphql_order_data(edge["node"])
                    orders.append(ShopifyOrder(**node))
                except Exception as e:
                    logger.error(
                        f"Error parsing order data: {e}",
                        extra={"order_id": edge.get("node", {}).get("id", "unknown")}
                    )
                    continue
            
            # Update pagination info
            page_info = orders_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor", None)
            
        except Exception as e:
            logger.error(
                f"Failed to fetch Shopify orders: {e}",
                extra={"variables":variables},
                exc_info=True
            )
            slack_notifier.send_error(
                "Failed to fetch Shopify orders", 
                details={"error": str(e), **variables}
            )
            break
    
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
    cleaned_orders = __cleanup_shopify_orders(orders.copy())  # Use copy to avoid modifying original
    
    if not cleaned_orders:
        logger.info("No eligible orders remain after cleanup")
        slack_notifier.send_info("No eligible orders found after filtering")
        return []
    
    logger.info(f"Cleaned orders: {len(cleaned_orders)} eligible out of {len(orders)} total")
    slack_notifier.send_info(
        f"Order filtering complete", 
        details={"eligible": len(cleaned_orders), "total": len(orders)}
    )
    
    # Generate tracking payload
    payload = __generate_tracking_payload(cleaned_orders)
    
    if not payload:
        logger.warning("No tracking payload generated")
        return []
    
    # Register trackings with the API
    __register_trackings(payload)
    
    # Wait for tracking registration to sync
    sync_delay = 5  # seconds
    logger.info(f"Waiting {sync_delay} seconds for tracking registration to sync")
    time.sleep(sync_delay)
    
    # Fetch and match tracking details with orders
    trackings = __fetch_tracking_details(payload, cleaned_orders)
    
    logger.info(
        f"Tracking processing complete: {len(trackings)} matched trackings",
        extra={
            "total_orders": len(orders),
            "eligible_orders": len(cleaned_orders),
            "matched_trackings": len(trackings)
        }
    )
    
    return trackings


def retrieve_refundable_shopify_orders():
    """
    Retrieve all matching Shopify orders and merge them with their 17track tracking information.
    
    Returns:
        List of tuples containing (ShopifyOrder, TrackingData) for eligible orders
    """
    logger.info("Starting retrieval of fulfilled Shopify orders")
    
    try:
        # Step 1: Fetch all relevant Shopify orders
        orders = __fetch_all_shopify_orders()
        
        if not orders:
            logger.info("No orders fetched from Shopify")
            return []
        
        # Step 2: Process orders for tracking information
        trackings = __process_orders_for_tracking(orders)
        
        logger.info(
            f"Order retrieval and tracking processing complete: {len(trackings)} final results"
        )
        
        return trackings
        
=======
            )
            break

        # Set pagination cursor
        variables["after"] = cursor

        logger.debug(f"Requesting orders page with cursor: {cursor}")

        try:
            data = __fetch_shopify_orders(variables=variables)

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
                # Break out of the while loop and return early
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

    empty_entries = ([], [])

    if not orders:
        logger.info("No orders to process")
        return empty_entries

    logger.info(f"Processing {len(orders)} orders for tracking")
    slack_notifier.send_info(f"Processing {len(orders)} orders for tracking")

    # Clean up orders to remove ineligible ones
    cleaned_orders = __cleanup_shopify_orders(
        orders
    )  # Use copy to avoid modifying original

    if not cleaned_orders:
        logger.info("No eligible orders remain after cleanup")
        slack_notifier.send_info("No eligible orders found after filtering")
        return empty_entries

    logger.info(
        f"Cleaned orders: {len(cleaned_orders)} eligible out of {len(orders)} total"
    )
    slack_notifier.send_info(
        "Order filtering complete",
        details={"eligible": len(cleaned_orders), "total": len(orders)},
    )

    if not cleaned_orders:
        return empty_entries

    # Generate tracking payload
    payload = generate_tracking_payload(cleaned_orders)

    if not payload:
        logger.warning("No tracking payload generated")
        return empty_entries

    # Register trackings with the API
    register_orders_trackings(payload)

    # Fetch and match tracking details with orders
    trackings = fetch_tracking_details(payload)

    logger.info(
        f"Tracking processing complete: {len(trackings)} matched trackings",
        extra={
            "total_orders": len(orders),
            "eligible_orders": len(cleaned_orders),
            "matched_trackings": len(trackings),
        },
    )

    return cleaned_orders, trackings


def retrieve_refundable_shopify_orders() -> tuple[
    list[ShopifyOrder], list[TrackingData]
]:
    """
    Retrieve all matching Shopify orders and merge them with their 17track tracking information.

    Returns:
        List of tuples containing (ShopifyOrder, TrackingData) for eligible orders
    """
    try:
        # Step 1: Fetch all relevant Shopify orders
        orders = __fetch_all_shopify_orders()

        if not orders:
            return ([], [])

        # Step 2: Process orders for tracking information
        cleaned_orders, trackings = __process_orders_for_tracking(orders)

        return (cleaned_orders, trackings)

>>>>>>> stage
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
