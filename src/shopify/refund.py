import json
import sys
import time
import uuid
from typing import Optional

import requests

from src.config import DRY_RUN, REQUEST_TIMEOUT, SHOPIFY_ACCESS_TOKEN, SHOPIFY_STORE_URL
from src.logger import get_logger
from src.models.order import RefundCreateResponse, ReturnFulfillments, ShopifyOrder
from src.models.tracking import TrackingData
from src.shopify.graph_ql_queries import REFUND_CREATE_MUTATION
from src.shopify.orders import retrieve_refundable_shopify_orders
from src.shopify.refund_calculator import refund_calculator
from src.shopify.refund_validator import validate_order_before_refund
from src.utils.audit import audit_logger, log_refund_audit
from src.utils.dry_run import create_dry_run_refund
from src.utils.idempotency import idempotency_manager
from src.utils.retry import exponential_backoff_retry
from src.utils.slack import slack_notifier
from src.utils.timezone import get_current_time_iso8601, timezone_handler

logger = get_logger(__name__)

endpoint = f"https://{SHOPIFY_STORE_URL}.myshopify.com/admin/api/2025-07/graphql.json"
headers = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json",
}

EXECUTION_MODE = "LIVE" if not DRY_RUN else "DRY_RUN"


def process_refund_automation():
    """Process fulfilled Shopify orders and handle refunds if eligible."""

    # Log timezone information
    tz_info = timezone_handler.get_timezone_info()
    logger.info(
        f"Starting refund automation in {EXECUTION_MODE} mode",
        extra={"mode": EXECUTION_MODE, "timezone_info": tz_info},
    )

    # Send startup notification
    slack_notifier.send_info(
        "Refund automation starting",
        details={"timezone:": f"\t{tz_info['store_timezone']}"},
    )

    try:
        trackings = retrieve_refundable_shopify_orders()
    except Exception as e:
        error_msg = f"Failed to retrieve Shopify orders: {e}"
        logger.error(error_msg, extra={"error": str(e)})
        slack_notifier.send_error(error_msg, details={"error": str(e)})
        return sys.exit(1)

    if not trackings:
        logger.warning(
            "No eligible tracking data found", extra={"trackings": len(trackings)}
        )
        slack_notifier.send_warning("No eligible orders found for refund processing")
        return sys.exit(0)

    # Initializing counters for summary
    successful_refunds = 0
    failed_refunds = 0
    skipped_refunds = 0
    total_refunded_amount = 0.0
    currency = "USD"
    refunded_orders = {}

    logger.info(f"Processing {len(trackings)} orders for potential refunds")

    refunded_returns: list[ReturnFulfillments] = []

    for idx, order_and_tracking in enumerate(trackings):
        # Extract order and tracking first
        order, tracking = order_and_tracking

        logger.info(
            f"Processing order {idx + 1}/{len(trackings)} - {order.name}",
            extra={
                "progress": f"{idx + 1}/{len(trackings)}",
                "order_id": order.id,
                "order_name": order.name,
            },
        )
        # Process refund with comprehensive error handling
        try:
            refund = refund_order(order, tracking)
            if refund:
                logger.info(
                    f"Successfully refunded Order({order.name})",
                    extra={
                        "order_id": order.id,
                        "refund_id": refund.id,
                        "order_name": order.name,
                    },
                )
                refunded_orders[refund.id] = refund.model_dump_json(indent=2)
                successful_refunds += 1

                # Add to total amount
                if hasattr(refund.totalRefundedSet, "presentmentMoney"):
                    total_refunded_amount += (
                        refund.totalRefundedSet.presentmentMoney.amount
                    )
                    currency = (
                        refund.totalRefundedSet.presentmentMoney.currencyCode
                        or currency
                    )

                # Capture the returns to close them later
                refunded_returns.extend(order.returns)

            else:
                logger.warning(
                    "Refund not processed",
                    extra={"order_id": order.id, "order_name": order.name},
                )
                failed_refunds += 1

        except Exception as e:
            logger.error(
                f"Unexpected error processing order {order.name}: {e}",
                extra={"order_id": order.id, "order_name": order.name, "error": str(e)},
            )
            failed_refunds += 1

            # Send error notification
            slack_notifier.send_error(
                f"Failed to process refund for order {order.name}",
                details={"order_id": order.id, "error": str(e)},
            )

    total_refunded_count = successful_refunds
    if not total_refunded_count:
        logger.warning(
            "No refund processed",
            extra={
                "trackings": len(trackings),
                "orders": len(order_and_tracking),
                "successful_refunds": successful_refunds,
                "failed_refunds": failed_refunds,
                "skipped_refunds": skipped_refunds,
            },
        )
        slack_notifier.send_warning(
            "No refund processed",
            details={
                "orders": len(order_and_tracking),
                "successful_refunds": successful_refunds,
                "failed_refunds": failed_refunds,
                "skipped_refunds": skipped_refunds,
            },
        )
        return sys.exit(0)

    # Log final summary
    summary_msg = "Refund processing completed"
    logger.info(
        summary_msg,
        extra={
            "successful_refunds": successful_refunds,
            "failed_refunds": failed_refunds,
            "skipped_refunds": skipped_refunds,
            "total_refunded_amount": f"{total_refunded_amount:.2f}",
            "currency": currency,
            "mode": EXECUTION_MODE,
        },
    )

    # Send summary Slack notification
    slack_notifier.send_refund_summary(
        successful_refunds=successful_refunds,
        failed_refunds=failed_refunds,
        total_amount=total_refunded_amount,
        currency=currency,
    )

    if refunded_orders:
        logger.debug(
            "Detailed refund results",
            extra={"refunded_orders": list(refunded_orders.keys())},
        )


def refund_order(
    order: ShopifyOrder, tracking=TrackingData
) -> Optional[RefundCreateResponse]:
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())[:8]

    # Extract basic order information
    order_amount = order.totalPriceSet.presentmentMoney.amount
    currency = order.totalPriceSet.presentmentMoney.currencyCode or "USD"
    tracking_number = tracking.number if tracking else None

    logger.info(
        f"Initiating refund for order {order.name} (${order_amount} {currency}) - mode: {EXECUTION_MODE}",
        extra={
            "order_id": order.id,
            "order_name": order.name,
            "order_amount": order_amount,
            "currency": currency,
            "mode": EXECUTION_MODE,
            "request_id": request_id,
            "tracking_number": tracking_number,
            "timestamp": get_current_time_iso8601(),
        },
    )

    try:
        # Validate the order and the tracking information before performing any mutations
        is_valid_refund = validate_order_before_refund(order, tracking, slack_notifier)
        if not is_valid_refund:
            return None

        idempotency_key, is_duplicated = (
            idempotency_manager.check_operation_idempotency(
                order.id,
                operation="refund",
                tracking_no=tracking.number,
                refund_id=order.valid_return_shipment.id,
            )
        )

        if is_duplicated:
            cached_results = idempotency_manager.get_operation_result(idempotency_key)
            logger.warning(
                f"Idempotency: Skipping Order: {order.id}-{order.name}",
                extra={"idempotency_key": idempotency_key, "order_id": order.id},
            )
            audit_logger.log_duplicate_operation(
                order_id=order.id,
                order_name=order.name,
                idempotency_key=idempotency_key,
                original_timestamp=cached_results.get("timestamp"),
            )
            slack_notifier.send_warning(
                f"Duplicate refund operation detected for order {order.name} - skipping",
                details={
                    "order_id": order.id,
                    "order_name": order.name,
                    "idempotency_key": idempotency_key,
                    "decision_branch": "duplicate_skipped",
                    "investigate": "Verify that the order is actually refunded",
                },
            )
            return None

        # Get the monetary calculations of this refund
        refund_calculation = refund_calculator.calculate_refund(order, tracking)

        # Ensuring at least a single transactions exists
        if not refund_calculation.transactions:
            error_msg = (
                f"No valid transactions calculated for refund in order {order.name}"
            )
            logger.error(
                error_msg,
                extra={
                    "order_id": order.id,
                    "order_name": order.name,
                    "decision_branch": "no_valid_transactions",
                },
            )
            log_refund_audit(
                order_id=order.id,
                order_name=order.name,
                refund_amount=order_amount,
                currency=currency,
                decision="failed",
                tracking_number=tracking_number,
                idempotency_key=idempotency_key,
                error="No valid transactions for refund",
            )
            slack_notifier.send_error(
                "No valid transactions calculated for refund in order {order.name}",
                details={
                    "order_id": order.id,
                    "order_name": order.name,
                    "decision_branch": "no_valid_transactions",
                    "tracking_number": tracking_number,
                },
            )
            return None

        return_id = order.valid_return_shipment.id

        logger.info(
            f"Sending {refund_calculation.refund_type} refund request to Shopify for order {order.name}",
            extra={
                "mode": EXECUTION_MODE,
                "order_id": order.id,
                "order_name": order.name,
                "return_id": return_id,
                "request_id": request_id,
                "tracking_number": tracking_number,
                **refund_calculation.model_dump(
                    exclude=["line_items_to_refund", "transactions"]
                ),
            },
        )

        # Prepare GraphQL variables with calculated data
        shipping = {}
        if refund_calculation.shipping_refund:
            shipping.update({"amount": refund_calculation.shipping_refund})

        refund_note = f"{refund_calculation.refund_type.capitalize()} refund - Total: ${refund_calculation.total_refund_amount}"
        variables = {
            "input": {
                "notify": True,
                "note": refund_note,
                "orderId": order.id,
                "shipping": shipping,
                "transactions": refund_calculation.transactions,
                "refundLineItems": refund_calculation.line_items_to_refund,
            }
        }

        if EXECUTION_MODE == "LIVE":
            # Execute the actual refund with retry mechanism
            refund = _execute_shopify_refund(order, variables, request_id)
        else:
            # Create a mock refund for dry run
            refund = create_dry_run_refund(order, refund_calculation)

        if refund:
            log_refund_audit(
                order_id=order.id,
                order_name=order.name,
                refund_amount=refund_calculation.total_refund_amount,
                currency=currency,
                decision="processed",
                tracking_number=tracking_number,
                idempotency_key=idempotency_key,
                refund_id=refund.id,
            )
            slack_notifier.send_success(
                f"Refund successfully processed for order {order.name}",
                details={
                    "order_id": order.id,
                    "return_id": return_id,
                    "refund_id": refund.id,
                    "request_id": request_id,
                    "order_name": order.name,
                    "tracking_number": tracking_number,
                    **refund_calculation.model_dump(
                        exclude=["line_items_to_refund", "transactions"]
                    ),
                },
            )
            logger.info(
                f"Refund successfully processed for order {order.name}",
                extra={
                    "order_id": order.id,
                    "return_id": return_id,
                    "refund_id": refund.id,
                    "request_id": request_id,
                    "order_name": order.name,
                    "tracking_number": tracking_number,
                    **refund_calculation.model_dump(
                        exclude=["line_items_to_refund", "transactions"]
                    ),
                },
            )
            idempotency_manager.mark_operation_completed(
                idempotency_key,
                order_id=order.id,
                operation="refund",
                result={
                    "order_id": order.id,
                    "return_id": return_id,
                    "refund_id": refund.id,
                    "request_id": request_id,
                    "order_name": order.name,
                    "tracking_number": tracking_number,
                    "variables": variables,
                    **refund_calculation.model_dump(),
                },
            )
        else:
            raise ValueError("Refund Creation Failed")

        return refund

    except Exception as e:
        error_msg = f"Refund failed for order {order.name}: {str(e)}"

        logger.error(
            error_msg,
            extra={
                "order_id": order.id,
                "order_name": order.name,
                "error": str(e),
                "request_id": request_id,
                "decision_branch": "failed",
            },
            exc_info=True,
        )

        # Log audit event for failure
        log_refund_audit(
            order_id=order.id,
            order_name=order.name,
            refund_amount=order_amount,
            currency=currency,
            decision="failed",
            tracking_number=tracking_number,
            error=str(e),
        )

        # Send error notification with request ID for escalation
        slack_notifier.send_error(
            error_msg,
            details={
                "order_id": order.id,
                "order_name": order.name,
                "error_type": type(e).__name__,
                "error": error_msg,
            },
            request_id=request_id,
        )

        return None


@exponential_backoff_retry(
    exceptions=(
        requests.exceptions.RequestException,
        requests.exceptions.Timeout,
        Exception,
    )
)
def _execute_shopify_refund(
    order: ShopifyOrder, variables: dict, request_id: str
) -> Optional[RefundCreateResponse]:
    """Execute the Shopify refund API call with retry mechanism."""

    start_time = time.time()

    # Log API request for audit
    audit_logger.log_api_interaction(
        request_type="POST", endpoint=endpoint, order_id=order.id, request_id=request_id
    )

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json={"query": REFUND_CREATE_MUTATION, "variables": variables},
            timeout=REQUEST_TIMEOUT,
        )

        response_time_ms = (time.time() - start_time) * 1000

        logger.debug(
            f"Shopify API response received for order {order.name}",
            extra={
                "status_code": response.status_code,
                "response_time_ms": response_time_ms,
                "order_id": order.id,
                "request_id": request_id,
            },
        )

        response.raise_for_status()
        data = response.json()

        # Log API response for audit
        audit_logger.log_api_interaction(
            request_type="POST",
            endpoint=endpoint,
            order_id=order.id,
            request_id=request_id,
            status_code=response.status_code,
            response_time_ms=response_time_ms,
        )

        # Handle null JSON response
        if data is None:
            logger.error(
                f"Received null JSON response from Shopify for order {order.name}",
                extra={"order_id": order.id, "request_id": request_id},
            )
            return None

        # Process Shopify response
        response_data = data.get("data") if data else None
        if response_data is None:
            logger.error(
                f"No 'data' field in Shopify response for order {order.name}",
                extra={
                    "order_id": order.id,
                    "request_id": request_id,
                    "response": data,
                },
            )
            return None

        user_errors = response_data.get("refundCreate", {}).get("userErrors", [])
        refund_data = response_data.get("refundCreate", {}).get("refund", None)

        if user_errors:
            error_messages = [err["message"] for err in user_errors]
            error_msg = f"Shopify API errors for order {order.name}: {error_messages}"

            logger.error(
                error_msg,
                extra={
                    "order_id": order.id,
                    "order_name": order.name,
                    "shopify_errors": error_messages,
                    "request_id": request_id,
                },
            )
            # Log API error for audit
            audit_logger.log_api_interaction(
                request_type="POST",
                endpoint=endpoint,
                order_id=order.id,
                request_id=request_id,
                status_code=response.status_code,
                response_time_ms=response_time_ms,
                error="Shopify user errors: " + "; ".join(error_messages),
            )
            slack_notifier.send_error(error_msg)

            if refund_data:
                error_msg = (
                    f"Shopify API errors for order {order.name}: {error_messages}"
                )
                logger.warning(
                    error_msg,
                    extra={
                        "order_id": order.id,
                        "order_name": order.name,
                        "shopify_errors": error_messages,
                        "request_id": request_id,
                    },
                )
                slack_notifier.send_warning(
                    error_msg,
                    details={
                        "message": "An error accurred while mutating refunds",
                        "request_id": request_id,
                        "order_id": order.id,
                        "order_name": order.name,
                    },
                )

        if not refund_data:
            logger.error(
                f"No refund data returned from Shopify for order {order.name}",
                extra={"order_id": order.id, "request_id": request_id},
            )
            slack_notifier.send_error(
                f"No refund data returned from Shopify for order {order.name}",
                details={
                    "order_id": order.id,
                    "request_id": request_id,
                    "response_data": (json.dumps(data)),
                },
            )
            return None

        # Enrich refund data
        refund_data["orderId"] = order.id
        refund_data["orderName"] = order.name

        return RefundCreateResponse(**refund_data)

    except requests.exceptions.RequestException as e:
        # Log API error for audit
        audit_logger.log_api_interaction(
            request_type="POST",
            endpoint=endpoint,
            order_id=order.id,
            request_id=request_id,
            error=str(e),
        )
        raise  # Re-raise for retry mechanism
