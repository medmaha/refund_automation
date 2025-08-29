import sys
import uuid
from dataclasses import dataclass

from src.config import (
    DRY_RUN,
    REFUND_FULL_SHIPPING,
    REFUND_PARTIAL_SHIPPING,
)
from src.logger import get_logger
from src.models.order import (
    Refund,
    RefundCreateResponse,
    ReverseFulfillment,
    ShopifyOrder,
)
from src.models.tracking import TrackingData
from src.shopify.orders import retrieve_refundable_shopify_orders
from src.shopify.refund_calculator import (
    RefundCalculationResult,
    refund_calculator,
)
from src.shopify.refund_mutation import execute_shopify_refund
from src.shopify.refund_validator import validate_order_before_refund
from src.shopify.return_closing import close_processed_returns
from src.utils.audit import audit_logger, log_refund_audit
from src.utils.dry_run import create_dry_run_refund
from src.utils.idempotency import idempotency_manager
from src.utils.slack import slack_notifier
from src.utils.timezone import get_current_time_iso8601, timezone_handler

logger = get_logger(__name__)

EXECUTION_MODE = "LIVE" if not DRY_RUN else "DRY_RUN"


@dataclass
class Summary:
    mode = EXECUTION_MODE
    failed_refunds = 0
    skipped_refunds = 0
    successful_refunds = 0
    total_refunded_amount = 0

    @property
    def total_count(self):
        return self.successful_refunds + self.skipped_refunds + self.failed_refunds


def process_refund_automation(max_retry=2, retry_count=0, summary: Summary = None):
    """Process fulfilled Shopify orders and handle refunds if eligible."""

    # Log timezone information
    tz_info = timezone_handler.get_timezone_info()

    automation_summary = summary or Summary()

    if retry_count == 0:
        logger.info(
            f"Starting refund automation in {EXECUTION_MODE} mode",
            extra={"mode": EXECUTION_MODE, "timezone_info": tz_info},
        )
        # Send startup notification
        slack_notifier.send_info(
            "Refund automation starting",
            details={"timezone:": f"\t{tz_info['store_timezone']}"},
        )
    else:
        logger.info(
            f"Refund automation retry #{retry_count} for failed refunds",
            extra={"mode": EXECUTION_MODE, "timezone_info": tz_info},
        )
        # Send retry notification
        slack_notifier.send_info(
            f"Refund automation retry #{retry_count}",
            details={"timezone:": f"\t{tz_info['store_timezone']}"},
        )

    try:
        orders, trackings = retrieve_refundable_shopify_orders()
    except Exception as e:
        error_msg = f"Failed to retrieve Shopify orders: {e}"
        logger.error(error_msg, extra={"error": str(e)})
        slack_notifier.send_error(error_msg, details={"error": str(e)})
        if retry_count == 0:
            # If this is not a retry and we can't get orders, exit
            return sys.exit(1)

    if not trackings:
        logger.warning(
            "No eligible tracking data found", extra={"trackings": len(trackings)}
        )
        slack_notifier.send_warning("No eligible orders found for refund processing")
        if retry_count == 0:
            # If this is not a retry and no trackings, we're done
            return sys.exit(0)

    logger.info(f"Processing {len(trackings)} orders for potential refunds")

    # Move these outside the loop so they persist across orders
    refunded_returns: list[ReverseFulfillment] = []
    failed_returns: list[ReverseFulfillment] = []
    skipped_returns: list[ReverseFulfillment] = []

    for idx, order in enumerate(orders, start=1):
        logger.info(
            f"Processing order {idx}/{len(orders)} - Order({order.name})",
        )

        extra_details = {
            "order_id": order.id,
            "order_name": order.name,
            "full_return_shipping": (
                "Policy OFF" if not REFUND_FULL_SHIPPING else "Policy ON"
            ),
            "partial_return_shipping": (
                "Policy OFF" if not REFUND_PARTIAL_SHIPPING else "Policy ON"
            ),
        }

        # Process refund with comprehensive error handling
        try:
            _refunded_returns, _skipped_returns, _failed_returns = refund_order(
                order, trackings
            )

            failed_returns.extend(_failed_returns)
            skipped_returns.extend(_skipped_returns)
            refunded_returns.extend(_refunded_returns)

            automation_summary.failed_refunds += len(_failed_returns)
            automation_summary.skipped_refunds += len(_skipped_returns)
            automation_summary.successful_refunds += len(_refunded_returns)
            automation_summary.total_refunded_amount += sum(
                [refund.returned_amount for refund in _refunded_returns]
            )

            if len(_refunded_returns) > 0 and not DRY_RUN:
                close_processed_returns(order, _refunded_returns)
                logger.info(
                    f"Successfully refunded Order({order.name})",
                    extra=extra_details,
                )

            elif not DRY_RUN:
                logger.warning(
                    f"Refund not processed for: Order({order.name})",
                    extra=extra_details,
                )

        except Exception as e:
            logger.error(
                f"Unexpected error processing order {order.name}: {e}",
                extra={
                    **extra_details,
                    "error": str(e),
                },
            )
            # Send error notification
            slack_notifier.send_error(
                f"Failed to process refund for order {order.name}",
                details={"order_id": order.id, "error": str(e)},
            )
            # Count this as a failed refund

    # Retry logic

    potential_fail_count = len(skipped_returns) + len(failed_returns)
    if potential_fail_count > 0 and retry_count < max_retry:
        new_retry_count = retry_count + 1
        logger.info(
            f"Retrying {len(failed_returns)} failed refunds (attempt {new_retry_count}/{max_retry})"
        )
        return process_refund_automation(
            max_retry=max_retry, retry_count=new_retry_count, summary=automation_summary
        )

    # Final summary
    logger.info(
        f"Refund processing completed for {automation_summary.total_count} items",
        extra={
            "successful_refunds": automation_summary.successful_refunds,
            "failed_refunds": automation_summary.failed_refunds,
            "skipped_refunds": automation_summary.skipped_refunds,
            "total_refunded_amount": f"{automation_summary.total_refunded_amount:.2f}",
            "mode": automation_summary.mode,
            "retry_attempts": retry_count,
        },
    )

    # Send summary Slack notification
    slack_notifier.send_refund_summary(
        successful_refunds=automation_summary.successful_refunds,
        failed_refunds=automation_summary.failed_refunds,
        skipped_refunds=automation_summary.skipped_refunds,
        total_amount=automation_summary.total_refunded_amount,
        retry_attempts=retry_count,
    )


def refund_order(order: ShopifyOrder, trackings=list[TrackingData]):
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())[:8]

    # Extract basic order information
    order_amount = order.totalPriceSet.presentmentMoney.amount
    currency = order.totalPriceSet.presentmentMoney.currencyCode

    logger.info(
        f"Initiating refund for order {order.name} (${order_amount} {currency}) - mode: {EXECUTION_MODE}",
        extra={
            "order_id": order.id,
            "order_name": order.name,
            "order_amount": order_amount,
            "currency": currency,
            "mode": EXECUTION_MODE,
            "request_id": request_id,
            "timestamp": get_current_time_iso8601(),
        },
    )

    skipped_reverse_fulfillments: list[ReverseFulfillment] = []
    refunded_reverse_fulfillments: list[ReverseFulfillment] = []
    errored_reverse_fulfillments: list[ReverseFulfillment] = []

    try:
        valid_reverse_fulfillments = order.get_valid_return_shipment()
        valid_reverse_fulfillments_count = len(valid_reverse_fulfillments)

        # Handle each refund independently
        for index, reverse_fulfillment in enumerate(
            valid_reverse_fulfillments, start=1
        ):
            logger.info(
                f"Processing refund {index}/{valid_reverse_fulfillments_count} - "
                f"Return({reverse_fulfillment.name}) Order({order.name})",
            )
            tracking = get_reverse_fulfillment_tracking_details(
                reverse_fulfillment, trackings
            )

            if not tracking:
                logger.warning(
                    f"No tracking data found for return {reverse_fulfillment.name}",
                    extra={
                        "order_id": order.id,
                        "order_name": order.name,
                        "return_id": reverse_fulfillment.id,
                        "return_name": reverse_fulfillment.name,
                        "decision_branch": "no_tracking_data",
                    },
                )

                log_refund_audit(
                    order_id=order.id,
                    order_name=order.name,
                    refund_amount=order_amount,
                    currency=currency,
                    decision="skipped",
                    tracking_number=None,
                    error="No tracking data found",
                )

                slack_notifier.send_warning(
                    f"Skipping refund for return {reverse_fulfillment.name} - No tracking data",
                    details={
                        "order_id": order.id,
                        "order_name": order.name,
                        "return_id": reverse_fulfillment.id,
                        "return_name": reverse_fulfillment.name,
                    },
                )

                skipped_reverse_fulfillments.append(reverse_fulfillment)
                continue

            idempotency_key, is_duplicated = (
                idempotency_manager.check_operation_idempotency(
                    order.id,
                    operation="refund",
                    return_id=reverse_fulfillment.id,
                    tracking_no=tracking.number,
                )
            )

            if is_duplicated:
                cached_results = idempotency_manager.get_operation_result(
                    idempotency_key
                )
                logger.warning(
                    f"Idempotency: Skipping Order: {order.id}-{order.name}",
                    extra={
                        "idempotency_key": idempotency_key,
                        "order_id": order.id,
                        "Return Id": reverse_fulfillment.id,
                        "Return Name": reverse_fulfillment.name,
                    },
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
                        "Order ID": order.id,
                        "Order Name": order.name,
                        "Return Id": reverse_fulfillment.id,
                        "Return Name": reverse_fulfillment.name,
                        "Idempotency Key": idempotency_key,
                        "Decision Branch": "Duplicate_skipped",
                        "Investigate": "Verify that the order is actually refunded",
                    },
                )

                skipped_reverse_fulfillments.append(reverse_fulfillment)
                continue

            # Validate the order and the tracking information before performing any mutations
            is_valid_refund = validate_order_before_refund(
                order, reverse_fulfillment, tracking, slack_notifier
            )

            if not is_valid_refund:
                skipped_reverse_fulfillments.append(reverse_fulfillment)
                continue

            tracking_number = tracking.number

            # Get the monetary calculations of this refund
            refund_calculation = refund_calculator.calculate_refund(
                order, reverse_fulfillment
            )

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
                        "Return Id": reverse_fulfillment.id,
                        "Return Name": reverse_fulfillment.name,
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
                        "tracking_number": tracking_number,
                        "Return Id": reverse_fulfillment.id,
                        "Return Name": reverse_fulfillment.name,
                        "decision_branch": "no_valid_transactions",
                    },
                )

                skipped_reverse_fulfillments.append(reverse_fulfillment)
                continue

            logger.info(
                f"Sending {refund_calculation.refund_type} refund request to Shopify for order {order.name}",
                extra={
                    "mode": EXECUTION_MODE,
                    "order_id": order.id,
                    "order_name": order.name,
                    "return_id": reverse_fulfillment.id,
                    "request_id": request_id,
                    "tracking_number": tracking_number,
                    **refund_calculation.model_dump(
                        exclude=["line_items_to_refund", "transactions"]
                    ),
                },
            )

            # Prepare GraphQL variables with calculated data
            shipping = {}
            if refund_calculation.refund_type == "FULL":
                shipping.update(
                    {
                        "fullRefund": True,
                    }
                )
            elif refund_calculation.shipping_refund:
                shipping.update(
                    {
                        "amount": refund_calculation.shipping_refund,
                    }
                )

            refund_note = f"{refund_calculation.refund_type.capitalize()} refund - Total: {currency} {refund_calculation.total_refund_amount}"
            variables = {
                "input": {
                    "notify": True,
                    "note": refund_note,
                    "orderId": order.id,
                    "shipping": shipping,
                    "transactions": refund_calculation.transactions,
                    "refundLineItems": refund_calculation.line_items_to_refund,
                    "currency": currency,
                }
            }
            try:
                if EXECUTION_MODE == "LIVE":
                    # Execute the actual refund with retry mechanism
                    refund: RefundCreateResponse = execute_shopify_refund(
                        order, variables, request_id, reverse_fulfillment.name
                    )
                else:
                    # Create a mock refund for dry run
                    refund = create_dry_run_refund(
                        order, refund_calculation, reverse_fulfillment.name
                    )
            except Exception as e:
                refund = None
                errored_reverse_fulfillments.append(reverse_fulfillment)
                error_msg = f"Refund failed for: Order{order.name} Return({reverse_fulfillment.name})"

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

            if refund:
                update_order_attributes(
                    order, reverse_fulfillment, refund_calculation, refund
                )

                reverse_fulfillment.returned_amount = (
                    refund.totalRefundedSet.presentmentMoney.amount
                )

                refunded_reverse_fulfillments.append(reverse_fulfillment)

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
                        "return_id": reverse_fulfillment.id,
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
                        "return_id": reverse_fulfillment.id,
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
                        "order_name": order.name,
                        "return": reverse_fulfillment.id,
                        "refund_name": reverse_fulfillment.name,
                        "refund_id": refund.id,
                        "request_id": request_id,
                        "tracking_number": tracking_number,
                        "variables": variables,
                        **refund_calculation.model_dump(
                            exclude=["transactions", "line_items_to_refund"]
                        ),
                    },
                )
            else:
                skipped_reverse_fulfillments.append(reverse_fulfillment)

    except Exception as e:
        error_msg = f"Refund failed for order {order.name}"

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

    return (
        refunded_reverse_fulfillments,
        skipped_reverse_fulfillments,
        errored_reverse_fulfillments,
    )


def update_order_attributes(
    order: ShopifyOrder,
    reverse_fulfillment: ReverseFulfillment,
    refund_calculation: RefundCalculationResult,
    refund: RefundCreateResponse,
):
    """Update the refund amount for tracking subsequent refund operations"""

    try:
        refunded_line_items_ids = [
            line_item_id
            for line_item in refund_calculation.line_items_to_refund
            if (line_item_id := line_item.get("lineItemId", None))
        ]
        corresponding_refund: Refund = next(
            (
                refund
                for refund in order.refunds
                for li in refund.refundLineItems
                if not refund.createdAt
                and li.lineItem.get("id") in refunded_line_items_ids
            ),
            None,
        )
        refunded_amount = refund.totalRefundedSet.presentmentMoney.amount

        if corresponding_refund:
            corresponding_refund.createdAt = (
                timezone_handler.get_current_time_store().__str__()
            )
            corresponding_refund.totalRefundedSet.presentmentMoney.amount = (
                refunded_amount
            )

        order.update_prior_refund_amount(amount=refunded_amount)
        order.totalRefundedShippingSet.presentmentMoney.amount += (
            refund_calculation.shipping_refund
        )
        order.totalRefundedShippingSet.presentmentMoney.currencyCode = (
            refund_calculation.currency
        )

        for rf in order.returns:
            if rf.id == reverse_fulfillment.id:
                rf.status = "REFUNDED"

    except Exception as e:
        logger.warning(
            f"Failed updating attributes for: Order({order.name}) Return({reverse_fulfillment.name})",
            extra={
                "refund": refund.id,
                "order_name": order.name,
                "return_name": reverse_fulfillment.name,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )


def get_reverse_fulfillment_tracking_details(
    reverse_fulfillment: ReverseFulfillment, trackings: list[TrackingData]
):
    if not reverse_fulfillment.reverseFulfillmentOrders:
        return None

    for rfo in reverse_fulfillment.reverseFulfillmentOrders:
        for reverse_delivery in rfo.reverseDeliveries:
            return_tracking_number = reverse_delivery.deliverable.tracking.number
            tracking = get_tracking_by_number(return_tracking_number, trackings)
            if tracking:
                return tracking

    return None


def get_tracking_by_number(number: str, trackings: list[TrackingData]):
    for tracking in trackings:
        if tracking.number == number:
            return tracking
    return None
