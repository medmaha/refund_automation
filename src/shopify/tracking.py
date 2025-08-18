import requests

from src.config import (
    DEFAULT_CARRIER_CODE,
    REQUEST_TIMEOUT,
    TRACKING_API_KEY,
    TRACKING_BASE_URL,
)
from src.logger import get_logger
from src.models.order import ShopifyOrder
from src.models.tracking import TrackingData, TrackingStatus, TrackingSubStatus
from src.utils.slack import slack_notifier

TRACKING_SEGMENT_SIZE = 40  # Maximum trackings per API call

logger = get_logger(__name__)


def get_order_by_tracking_id(tracking_number: str, orders: list[ShopifyOrder]):
    """
    Optimized function to find an order by tracking number.
    This version returns None if no matching order is found, which is good practice.
    """

    for order in orders:
        if order.tracking_number == tracking_number:
            return order

    logger.debug(f"No order found for tracking number: {tracking_number}")
    return None


def generate_tracking_payload(orders: list[ShopifyOrder]):
    """Generate tracking payload from eligible orders."""

    logger.info(f"Generating tracking {len(orders)} orders")
    payload = []

    if len(orders) < 1:
        return payload

    try:
        for order in orders:
            carrier_code = None
            for _return in order.returns:
                if _return.status == "OPEN":
                    for rfo in _return.reverseFulfillmentOrders:
                        for rd in rfo.reverseDeliveries:
                            # Only if we have the tracking number
                            if rd.deliverable.tracking.number:
                                carrier_code = rd.deliverable.tracking.carrierName
                                tracking_number = rd.deliverable.tracking.number

                                if carrier_code and not carrier_code.isdigit():
                                    carrier_code = DEFAULT_CARRIER_CODE

                                logger.debug(
                                    f"Adding tracking number: {tracking_number}, carrier: {carrier_code}"
                                )

                                payload.append({"number": tracking_number})
                                # payload.append({"number": tracking_number, "carrier": carrier_code})
    except Exception as e:
        logger.error(f"Failed to generate tracking payload -> error [{e}]")
        return payload

    logger.info(f"Generated tracking payload with {len(payload)} entries")
    return payload


def register_orders_trackings(payload: list[dict]):
    """Register tracking numbers with the tracking API using retry logic and better error handling."""

    # idempotent_payloads = []

    # TODO: Check the idempotency of the registering items
    # To reduce unnecessary network bandwidth

    if not payload:
        return

    url = f"{TRACKING_BASE_URL}/register"
    headers = {"content-type": "application/json", "17token": TRACKING_API_KEY}

    # Split payload into manageable segments
    payload_segments = [
        payload[i : i + TRACKING_SEGMENT_SIZE]
        for i in range(0, len(payload), TRACKING_SEGMENT_SIZE)
    ]

    logger.info(
        f"Registering {len(payload)} trackings in {len(payload_segments)} segments"
    )

    total_registered = 0
    total_rejected = 0

    for segment_idx, segment_payload in enumerate(payload_segments, 1):
        try:
            logger.debug(
                f"Registering tracking segment {segment_idx}/{len(payload_segments)} with {len(segment_payload)} entries"
            )

            # Use retry mechanism from utils.retry
            from src.utils.retry import exponential_backoff_retry

            @exponential_backoff_retry(
                exceptions=(
                    requests.exceptions.RequestException,
                    requests.exceptions.Timeout,
                )
            )
            def _register_tracking_segment(segment_payload: list[dict]):
                response = requests.post(
                    url,
                    headers=headers,
                    json=segment_payload,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                return response

            response = _register_tracking_segment(segment_payload)
            response_data = response.json()

            accepted_trackings = response_data.get("data", {}).get("accepted", [])
            rejected_trackings = response_data.get("data", {}).get("rejected", [])

            total_registered += len(accepted_trackings)

            # Later: filter out rejected with reason (already registered) and add to accepted
            total_rejected += len(rejected_trackings)

            logger.info(
                f"Segment {segment_idx}: {len(accepted_trackings)} registered, {len(rejected_trackings)} rejected"
            )

            # Log rejected trackings for troubleshooting
            if rejected_trackings:
                logger.warning(
                    f"Rejected trackings in segment {segment_idx}",
                    extra={
                        "rejected_count": len(rejected_trackings),
                        "rejected_trackings": rejected_trackings,
                    },
                )

        except requests.exceptions.RequestException as e:
            logger.error(
                f"Failed to register tracking segment {segment_idx}/{len(payload_segments)}: {e}",
                extra={
                    "segment_index": segment_idx,
                    "segment_size": len(segment_payload),
                    "error": str(e),
                },
            )
            slack_notifier.send_error(
                f"Failed to register tracking segment {segment_idx}",
                details={"error": str(e), "segment_size": len(segment_payload)},
            )
            continue

        except Exception as e:
            logger.error(
                f"Unexpected error registering tracking segment {segment_idx}: {e}",
                extra={
                    "segment_index": segment_idx,
                    "segment_size": len(segment_payload),
                    "error": str(e),
                },
                exc_info=True,
            )
            continue

    logger.info(
        f"Total tracking registration results: {total_registered} registered, {total_rejected} rejected"
    )


def fetch_tracking_details(payload: list, orders: list[ShopifyOrder]):
    """
    Fetch tracking details for the given payload and match them with Shopify orders.
    """

    logger.info(f"Fetching tracking details for {len(payload)} payload entries")

    if not payload:
        logger.warning("Empty payload provided to fetch tracking details")
        return []

    url = f"{TRACKING_BASE_URL}/gettrackinfo"
    headers = {"content-type": "application/json", "17token": TRACKING_API_KEY}

    try:
        # Use retry mechanism from utils.retry
        from src.utils.retry import exponential_backoff_retry

        @exponential_backoff_retry(
            exceptions=(
                requests.exceptions.RequestException,
                requests.exceptions.Timeout,
            )
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
            extra={"payload_size": len(payload), "error": str(e)},
        )
        slack_notifier.send_error(
            "Failed to fetch tracking details",
            details={
                "error": str(e),
                "payload": str(payload),
                "payload_size": len(payload),
            },
        )
        return []

    except Exception as e:
        logger.error(
            f"Unexpected error fetching tracking details: {e}",
            extra={"payload_size": len(payload), "error": str(e)},
            exc_info=True,
        )
        return []

    # Extract tracking data from the API response
    trackings: list = response_data.get("data", {}).get("accepted", [])

    if not trackings:
        logger.warning("No tracking data received from API")
        return []

    logger.info(f"Received {len(trackings)} tracking entries from API")

    # List to hold tuples of (ShopifyOrder, TrackingData) for matched and valid trackings
    order_and_trackings = []

    parsing_errors = 0
    processed_count = 0

    matched_tracking_numbers: list[tuple[str, str]] = []
    unmatched_tracking_numbers: list[tuple[str, str]] = []

    for tracking_data in trackings:
        processed_count += 1
        try:
            if not isinstance(tracking_data, dict):
                logger.warning(f"Invalid tracking data format: {type(tracking_data)}")
                parsing_errors += 1
                continue

            _tracking = TrackingData(**tracking_data)

            # Find the corresponding Shopify order by tracking number
            corresponding_order = get_order_by_tracking_id(_tracking.number, orders)

            # Skip if either tracking-info or corresponding-order is missing
            if not (_tracking.track_info and corresponding_order):
                unmatched_tracking_numbers.append((_tracking.number, "N/A"))
                logger.debug(
                    f"Skipping tracking number: {_tracking.number} (missing tracking info)",
                    extra={
                        "has_track_info": _tracking.track_info is not None,
                        "has_associated_order": corresponding_order is not None,
                    },
                )
                continue

            # Extract tracking status and sub-status with validation
            try:
                tracking_status = _tracking.track_info.latest_status.status.value
                tracking_sub_status = (
                    _tracking.track_info.latest_status.sub_status.value
                )
            except AttributeError as e:
                logger.warning(
                    f"Invalid tracking status structure for {_tracking.number}: {e}",
                    extra={"tracking_number": _tracking.number},
                )
                continue

            # Only add to result if
            # status and sub-status match the return criteria
            if (
                tracking_status == TrackingStatus.DELIVERED.value
                and tracking_sub_status == TrackingSubStatus.DELIVERED_OTHER.value
            ):
                order_and_trackings.append((corresponding_order, _tracking))
                matched_tracking_numbers.append(
                    (_tracking.number, corresponding_order.name)
                )
            else:
                unmatched_tracking_numbers.append(
                    (_tracking.number, corresponding_order.name)
                )
                logger.debug(
                    f"Tracking number {_tracking.number} does not match return criteria",
                    extra={
                        "tracking_number": _tracking.number,
                        "status": (
                            tracking_status.value
                            if hasattr(tracking_status, "value")
                            else str(tracking_status)
                        ),
                        "sub_status": (
                            tracking_sub_status.value
                            if hasattr(tracking_sub_status, "value")
                            else str(tracking_sub_status)
                        ),
                    },
                )

        except ValueError as e:
            # Pydantic validation error
            parsing_errors += 1
            logger.error(
                f"Validation error for tracking data: {e}",
                extra={
                    "tracking_number": tracking_data.get("number", "unknown"),
                    "error": str(e),
                },
            )

        except Exception as e:
            # Any other parsing error
            parsing_errors += 1
            logger.error(
                f"Parsing error for tracking data: {e}",
                extra={
                    "tracking_number": tracking_data.get("number", "unknown"),
                    "error": str(e),
                    "tracking_data_keys": (
                        list(tracking_data.keys())
                        if isinstance(tracking_data, dict)
                        else None
                    ),
                },
                exc_info=True,
            )

    if matched_tracking_numbers:
        logger.info(
            "These tracking-numbers matches return criteria",
            extra={
                "status": TrackingStatus.DELIVERED.value,
                "sub_status": TrackingSubStatus.DELIVERED_OTHER.value,
                "payload": matched_tracking_numbers,
            },
        )
        slack_payload = {
            f"{item[1]}": f"Tracking({item[0]})" for item in matched_tracking_numbers
        }
        slack_payload.update(
            {
                "status": TrackingStatus.DELIVERED.value,
                "sub_status": TrackingSubStatus.DELIVERED_OTHER.value,
            }
        )
        slack_notifier.send_info(
            "These tracking-numbers matches return criteria", details=slack_payload
        )

    if unmatched_tracking_numbers:
        logger.info(
            f"These tracking-numbers {_tracking.number} fails return criteria",
            extra={"payload": matched_tracking_numbers},
        )
        slack_payload = {
            f"{item[1]}": f"Tracking({item[0]})" for item in unmatched_tracking_numbers
        }
        slack_notifier.send_warning(
            "These tracking-numbers fails return criteria", details=slack_payload
        )

    # Log summary statistics
    logger.info(
        f"Tracking details processing complete: {len(order_and_trackings)} matched, {parsing_errors} errors out of {processed_count} total",
        extra={
            "matched_orders": len(order_and_trackings),
            "parsing_errors": parsing_errors,
            "processed_count": processed_count,
            "success_rate": (
                f"{((processed_count - parsing_errors) / processed_count * 100):.1f}%"
                if processed_count > 0
                else "0%"
            ),
        },
    )

    if parsing_errors > 0:
        slack_notifier.send_warning(
            f"Tracking parsing completed with {parsing_errors} errors",
            details={
                "matched": len(order_and_trackings),
                "errors": parsing_errors,
                "total": processed_count,
            },
        )

    return order_and_trackings
