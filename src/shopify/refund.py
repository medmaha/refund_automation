import sys
import uuid

from src.config import (
    DRY_RUN,
    REFUND_FULL_SHIPPING,
    REFUND_PARTIAL_SHIPPING,
)
from src.logger import get_logger
from src.models.order import RefundCreateResponse, ReverseFulfillment, ShopifyOrder
from src.models.tracking import TrackingData
from src.shopify.orders import retrieve_refundable_shopify_orders
from src.shopify.refund_calculator import (
    RefundCalculationResult,
    RefundCalculator,
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
        orders, trackings = retrieve_refundable_shopify_orders()
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
    total_refunded_amount = 0.0
    currency = "USD"
    refunded_orders = {}

    logger.info(f"Processing {len(trackings)} orders for potential refunds")

    refunded_returns: list[ReverseFulfillment] = []
    failed_returns: list[ReverseFulfillment] = []
    skipped_returns: list[ReverseFulfillment] = []

    for idx, order in enumerate(orders, start=1):
        failed_returns = []
        skipped_returns = []
        refunded_returns = []

        logger.info(
            f"Processing order {idx}/{len(orders)} - Order({order.name})",
        )
        extra_details = {
            "order_id": order.id,
            "order_name": order.name,
            "refunded_returns": ", ".join(
                [refund.name for refund in refunded_returns] or ["N/A"]
            ),
            "skipped_returns": ", ".join(
                [skipped_r.name for skipped_r in skipped_returns] or ["N/A"]
            ),
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

            if len(refunded_returns) > 0 and not DRY_RUN:
                close_processed_returns(order, refunded_returns)

                logger.info(
                    f"Successfully refunded Order({order.name})",
                    extra=extra_details,
                )
                extra_details.update(
                    {
                        "refunded_returns": ", ".join(
                            [refund.name for refund in refunded_returns] or ["N/A"]
                        ),
                        "skipped_returns": ", ".join(
                            [skipped_r.name for skipped_r in skipped_returns] or ["N/A"]
                        ),
                    }
                )

            elif not DRY_RUN:
                logger.warning(
                    f"Refund not processed for: Order({order.name}) Returns[{', '.join([rf.name for rf in refunded_returns])}]",
                    extra=extra_details,
                )

            slack_notifier.send_success(
                f"Refund processed completed for: Order({order.name})",
                details=extra_details,
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

    if not refunded_returns:
        logger.warning(
            "No refund processed",
            extra={
                "orders": len(orders),
                "trackings": len(trackings),
                "successful_refunds": len(refunded_returns),
                "failed_refunds": len(failed_returns),
                "skipped_refunds": len(skipped_returns),
            },
        )
        slack_notifier.send_warning(
            "No refund processed",
            details={
                "orders": len(orders),
                "trackings": len(trackings),
                "successful_refunds": len(refunded_returns),
                "failed_refunds": len(failed_returns),
                "skipped_refunds": len(skipped_returns),
            },
        )
        return sys.exit(0)

    # Log final summary
    summary_msg = "Refund processing completed"

    total_refunded_amount = sum([refund.returned_amount for refund in refunded_returns])

    logger.info(
        summary_msg,
        extra={
            "successful_refunds": len(refunded_returns),
            "failed_refunds": len(failed_returns),
            "skipped_refunds": len(skipped_returns),
            "total_refunded_amount": f"{total_refunded_amount:.2f}",
            "currency": currency,
            "mode": EXECUTION_MODE,
        },
    )

    # Send summary Slack notification
    slack_notifier.send_refund_summary(
        successful_refunds=len(refunded_returns),
        failed_refunds=len(failed_returns),
        skipped_refunds=len(skipped_returns),
        total_amount=total_refunded_amount,
        currency=currency,
    )

    if refunded_orders:
        logger.debug(
            "Detailed refund results",
            extra={"refunded_orders": list(refunded_orders.keys())},
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
        order.totalRefundedShippingSet.presentmentMoney.amount += refund_calculation.shipping_refund

        refunded_amount = refund.totalRefundedSet.presentmentMoney.amount
        for order_refund in order.refunds:
            if (
                order_refund.createdAt
                or order_refund.totalRefundedSet.presentmentMoney.amount
            ):
                continue

            refunded_line_items_ids = [
                refunded_line_item.lineItem.get("id")
                for refunded_line_item in order_refund.refundLineItems
            ]

            breaked = False
            for _line_item in reverse_fulfillment.returnLineItems:
                return_line_item_id = _line_item.fulfillmentLineItem.lineItem.get("id")

                if not return_line_item_id in refunded_line_items_ids:
                    continue

                order_refund.totalRefundedSet.presentmentMoney.amount = refunded_amount
                order_refund.createdAt = timezone_handler.get_current_time_store().__str__()
                breaked = True
                break

            if breaked:
                break

        # Remove the processed return from the list of pending returns
        order.returns = [rf for rf in order.returns if rf.id != reverse_fulfillment.id]
    
    except Exception as e:
        logger.warning(
            f"Failed updating attributes for: Order({order.name})",
            extra={
                "refund": refund.id,
                "order_name": order.name,
                "return_name": reverse_fulfillment.name,
                "error_type": type(e).__name__,
            }
        )

def get_reverse_fulfillment_tracking_details(
    reverse_fulfillment: ReverseFulfillment, trackings: list[TrackingData]
):
    if not reverse_fulfillment.reverseFulfillmentOrders:
        return None

    for rfo in reverse_fulfillment.reverseFulfillmentOrders:
        for reverse_delivery in rfo.reverseDeliveries:
            return_tracking_number = reverse_delivery.deliverable.tracking.number
            tracking = next(
                (t for t in trackings if t.number == return_tracking_number), None
            )
            if tracking:
                return tracking

    return None
