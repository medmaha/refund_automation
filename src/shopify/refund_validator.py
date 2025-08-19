from src.logger import get_logger
from src.models.order import ShopifyOrder
from src.models.tracking import (
    TrackingData,
    TrackingStatus,
    TrackingSubStatus,
)
from src.shopify.refund_calculator import refund_calculator
from src.utils.audit import audit_logger, log_refund_audit
from src.utils.idempotency import idempotency_manager
from src.utils.slack import SlackNotifier
from src.utils.timing_validator import validate_refund_timing

logger = get_logger(__name__)


CHARGEBACK_TAG = ["chargeback"]
FORCE_REFUND_TAG = ["refund:auto:now"]
NO_AUTO_REFUND_TAG = ["refund:auto:off"]


def order_refund_validation(
    order: ShopifyOrder, tracking: TrackingData, slack_notifier: SlackNotifier
):
    tracking_number = order.get_tracking_number()
    currency = order.totalPriceSet.presentmentMoney.currencyCode or "USD"

    order_tags_string = "".join(order.tags).lower()

    #
    lookup_tags = [
        "refund-auto-off",
        "no-auto-refund",
        "manual-refund-only",
        *CHARGEBACK_TAG,
        *FORCE_REFUND_TAG,
        *NO_AUTO_REFUND_TAG,
    ]

    # A bypassing flag, when True no validation check is made
    is_force_refund = any(
        (keyword in order_tags_string) for keyword in FORCE_REFUND_TAG
    )

    # A flag to stop the refund automation process for this order
    is_auto_refund_off = any(
        (keyword in order_tags_string) for keyword in NO_AUTO_REFUND_TAG
    )

    # Check if any active review is present
    has_chargeback_disputes = any(dispute.is_chargeback() for dispute in order.disputes)

    if has_chargeback_disputes:
        invalid_tag_log(
            tag="chargeback",
            is_auto_off=is_auto_refund_off,
            force_refund=is_force_refund,
            tracking_number=tracking_number,
            order=order,
        )

    for tag in lookup_tags:
        if tag in order_tags_string:
            invalid_tag_log(
                tag=tag,
                is_auto_off=is_auto_refund_off,
                force_refund=is_force_refund,
                tracking_number=tracking_number,
                order=order,
            )
            # Force refund should bypass blocking validators
            if is_force_refund:
                break
            if is_auto_refund_off:
                return None

            # Return because the lookup tag matches the order.tags
            return None

    if not is_force_refund and (
        not tracking_number or tracking_number != tracking.number
    ):
        message = f"Missing tracking-no Order({order.name})"

        details = {
            "order_id": order.id,
            "order_name": order.name,
            "order_tracking_number": tracking_number,
            "provided_tracking_number": tracking.number,
        }

        if tracking_number != tracking.number:
            logger.info(
                "ORDER",
                extra={
                    "abc": order.returns[0]
                    .reverseFulfillmentOrders[0]
                    .reverseDeliveries[0]
                    .deliverable.tracking.model_dump_json()
                },
            )
            details.update(
                {
                    "investigate": f"Do a review of this order on shopify admin #{order.name.replace('#', '')}"
                }
            )
            message = f"Mismatched tracking numbers: OrderTracking({tracking_number})"

        logger.warning(message, extra=details)

        # Log audit event for skipped order
        log_refund_audit(
            order_id=order.id,
            order_name=order.name,
            refund_amount=0.0,
            currency=currency,
            decision="skipped",
            tracking_number=tracking_number,
            error=message,
        )

        slack_notifier.send_warning(message, details=details)
        return None

    latest_event = tracking.track_info.latest_event
    if not is_force_refund and (not latest_event or not tracking.track_info):
        logger.warning(
            "No latest event found for tracking - skipping",
            extra={"order_id": order.id, "order_name": order.name},
        )

        # Log audit event for skipped order
        log_refund_audit(
            order_id=order.id,
            order_name=order.name,
            refund_amount=0.0,
            currency=currency,
            decision="skipped",
            tracking_number=tracking_number,
            error="No latest tracking event",
        )
        slack_notifier.send_warning(
            f"No latest tracking event: Order({order.name})",
            details={
                "order_id": order.id,
                "order_name": order.name,
                "delivery": str(latest_event or "N/A"),
            },
        )
        return None

    if not is_force_refund:
        is_eligible, timing_details = validate_refund_timing(tracking)

        if not is_eligible:
            err_message = timing_details.pop(
                "reason", "Tracking failed timing validation"
            )
            logger.warning(
                err_message,
                extra={
                    "order_id": order.id,
                    "order_name": order.name,
                    **timing_details,
                },
            )
            # Log audit event for skipped order
            log_refund_audit(
                order_id=order.id,
                order_name=order.name,
                refund_amount=0.0,
                currency=currency,
                decision="skipped",
                tracking_number=tracking_number,
                error=err_message,
            )
            slack_notifier.send_warning(
                err_message,
                details={
                    "order_id": order.id,
                    "order_name": order.name,
                    **timing_details,
                },
            )
            return None

    latest_status = tracking.track_info.latest_status  # Can be null
    tracking_status = latest_status.status.value if latest_status else None
    tracking_sub_status = latest_status.sub_status.value if latest_status else None

    if not is_force_refund and (
        tracking_status != TrackingStatus.DELIVERED.value
        or tracking_sub_status != TrackingSubStatus.DELIVERED_OTHER.value
    ):
        logger.warning(
            f"Invalid tracking status for: Order({order.name})",
            extra={
                "order_id": order.id,
                "order_name": order.name,
                "tracking_status": tracking_status,
                "tracking_sub_status": tracking_sub_status,
            },
        )

        # Log audit event for skipped order
        log_refund_audit(
            order_id=order.id,
            order_name=order.name,
            refund_amount=0.0,
            currency=currency,
            decision="skipped",
            tracking_number=tracking_number,
            error=f"Invalid tracking status for: Order({order.name})",
        )
        slack_notifier.send_warning(
            f"Invalid tracking status for: Order({order.name})",
            details={
                "order_id": order.id,
                "order_name": order.name,
                "tracking_status": tracking_status,
                "tracking_sub_status": tracking_sub_status,
            },
        )
        return None

    refund_calculation = refund_calculator.calculate_refund(order, tracking)
    idempotency_key, is_duplicated = idempotency_manager.check_operation_idempotency(
        order.id,
        operation="refund",
        tracking_no=tracking.number,
        delivered_at_iso=latest_event.time_iso,
    )

    if is_duplicated:
        logger.warning(
            f"Idempotency: Skipping Order: {order.id}-{order.name}",
            extra={"idempotency_key": idempotency_key, "order_id": order.id},
        )

        # Log audit event for duplicate
        audit_logger.log_duplicate_operation(
            order_id=order.id,
            order_name=order.name,
            idempotency_key=idempotency_key,
            original_timestamp="unknown",  # Could be enhanced to get from cache
        )

        #
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

    return order, refund_calculation, idempotency_key


def invalid_tag_log(
    tag: str,
    force_refund: bool,
    is_auto_off: bool,
    tracking_number: str,
    currency: str,
    order: ShopifyOrder,
    slack_notifier: SlackNotifier,
):
    extra_log_details = {}

    log_decision = "skipped"
    tag = tag.lower()
    err_message = f'Invalid tag detected "{tag}"'

    "Force should override blocking conditions"
    if force_refund:
        extra_log_details.update(
            {
                "tag": tag,
                "action": "immediately processed the refund",
                "reason": f'Bypassed validation checks because of the "{tag}"',
            }
        )
        log_decision = "bypass_blocking" if not is_auto_off else log_decision
        err_message = (
            f"Force refund detected for Order({order.name}) | Warning(Force Override) |"
            if not is_auto_off
            else err_message
        )

    if is_auto_off:
        err_message += " | Warning(Automation-Off) try a manual refund |"

    logger.warning(
        f"{err_message}: Order({order.name})",
        extra={
            "order_id": order.id,
            "order_name": order.name,
            "tag": tag,
            **extra_log_details,
        },
    )

    # Log audit event for skipped order
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision=log_decision,
        tracking_number=tracking_number,
        error=err_message,
    )

    slack_notifier.send_warning(
        f"{err_message}: Order({order.name})",
        details={
            "order_id": order.id,
            "order_name": order.name,
            "tag": tag,
            **extra_log_details,
        },
    )
