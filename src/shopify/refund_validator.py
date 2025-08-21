from src.logger import get_logger
from src.models.order import ShopifyOrder
from src.models.tracking import (
    TrackingData,
    TrackingStatus,
    TrackingSubStatus,
)
from src.utils.audit import log_refund_audit
from src.utils.slack import SlackNotifier
from src.utils.timing_validator import validate_refund_timing

logger = get_logger(__name__)

CHARGEBACK_TAG = ["chargeback"]
FORCE_REFUND_TAGS = ["refund:force:now"]
NO_AUTO_REFUND_TAGS = ["refund:auto:off"]

# List of tags to lookup for
lookup_tags = [
    "refund-auto-off",
    "no-auto-refund",
    "manual-refund-only",
    *CHARGEBACK_TAG,
    *FORCE_REFUND_TAGS,
    *NO_AUTO_REFUND_TAGS,
]


def validate_order_before_refund(
    order: ShopifyOrder, tracking: TrackingData, slack_notifier: SlackNotifier
):
    tracking_number = order.get_tracking_number()
    order_tags_string = " ".join(order.tags).lower()
    currency = order.totalPriceSet.presentmentMoney.currencyCode or "USD"

    is_force_refund = any(
        (keyword in order_tags_string) for keyword in FORCE_REFUND_TAGS
    )
    if is_force_refund:
        log_invalid_tags_or_chargeback_error(
            tag=FORCE_REFUND_TAGS[0],
            force_refund=is_force_refund,
            is_auto_off=FORCE_REFUND_TAGS[0],
            tracking_number=tracking_number,
            currency=currency,
            order=order,
            slack_notifier=slack_notifier,
        )

    if not is_force_refund:
        has_chargeback_disputes = any(
            dispute.is_chargeback() for dispute in order.disputes
        )
        if has_chargeback_disputes:
            log_invalid_tags_or_chargeback_error(
                tag="chargeback",
                force_refund=False,
                is_auto_off=False,
                tracking_number=tracking_number,
                currency=currency,
                order=order,
                slack_notifier=slack_notifier,
            )
            return False

        is_auto_refund_off = any(
            (keyword in order_tags_string) for keyword in NO_AUTO_REFUND_TAGS
        )
        for tag in lookup_tags:
            if tag in order_tags_string:
                log_invalid_tags_or_chargeback_error(
                    tag=tag,
                    force_refund=False,
                    is_auto_off=is_auto_refund_off,
                    tracking_number=tracking_number,
                    currency=currency,
                    order=order,
                    slack_notifier=slack_notifier,
                )
                if is_auto_refund_off:
                    return False
                return False

        if tracking_number != tracking.number:
            return log_tracking_number_error(
                order, tracking, tracking_number, currency, slack_notifier
            )

        latest_event = tracking.track_info.latest_event
        if not latest_event or not tracking.track_info:
            return log_no_tracking_event(
                order, tracking_number, currency, latest_event, slack_notifier
            )

        if tracking.is_carrier_disagreement:
            return log_carrier_disagreement_error(
                order, tracking, currency, slack_notifier
            )

        # Retrieve the latests status information from the tracking data
        latest_status = tracking.track_info.latest_status
        tracking_status: str = latest_status.status.value if latest_status else ""
        tracking_sub_status: str = latest_status.sub_status.value if latest_status else ""

        allowed_sub_statuses = [
            None,
            TrackingSubStatus.DELIVERED_OTHER.value.lower(),
            TrackingSubStatus.DELIVERED_SIGNED.value.lower(),
            TrackingSubStatus.DELIVERED_AT_LOCKER.value.lower(),
        ]

        # Block refund if any of these conditions are met
        if (
            not (tracking_status.lower() == TrackingStatus.DELIVERED.value.lower())
            or
            not (
                (tracking_sub_status is None)
                or
                (tracking_sub_status and not tracking_sub_status.startswith("DELIVERED_"))
                or
                (tracking_sub_status and not tracking_sub_status.lower() in allowed_sub_statuses)
            )
        ):
            return log_invalid_tracking_status(
                order, tracking_number, currency, tracking_status, tracking_sub_status, slack_notifier
            )

        is_eligible, timing_details = validate_refund_timing(tracking)
        if not is_eligible:
            return log_timing_validation_error(
                order, timing_details, tracking_number, currency, slack_notifier
            )

    return True


def log_carrier_disagreement_error(
    order: ShopifyOrder,
    tracking: TrackingData,
    currency: str,
    slack_notifier: SlackNotifier,
):
    message = f"Carrier disagreement detected for Order({order.name})"
    details = {
        "order_id": order.id,
        "order_name": order.name,
        "order_tracking_number": tracking.number,
        "provided_tracking_number": tracking.number,
        "carrier": tracking.carrier,
        "carrier_code": tracking.carrier,
    }
    logger.warning(message, extra=details)
    slack_notifier.send_warning(message, details=details)
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision="skipped",
        tracking_number=tracking.number,
        error=message,
    )
    return False


def log_tracking_number_error(
    order: ShopifyOrder,
    tracking: TrackingData,
    tracking_number: str,
    currency: str,
    slack_notifier: SlackNotifier,
):
    message = f"Missing tracking-no Order({order.name})"
    details = {
        "order_id": order.id,
        "order_name": order.name,
        "order_tracking_number": tracking_number,
        "provided_tracking_number": tracking.number,
    }

    if tracking_number != tracking.number:
        details.update(
            {
                "investigate": f"Do a review of this order on shopify admin #{order.name.replace('#', '')}"
            }
        )
        message = f"Mismatched tracking numbers: OrderTracking({tracking_number})"

    logger.warning(message, extra=details)
    slack_notifier.send_warning(message, details=details)
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision="skipped",
        tracking_number=tracking_number,
        error=message,
    )
    return False


def log_no_tracking_event(
    order: ShopifyOrder,
    tracking_number: str,
    currency: str,
    latest_event,
    slack_notifier: SlackNotifier,
):
    logger.warning(
        "No latest event found for tracking - skipping",
        extra={"order_id": order.id, "order_name": order.name},
    )
    slack_notifier.send_warning(
        f"No latest tracking event: Order({order.name})",
        details={
            "order_id": order.id,
            "order_name": order.name,
            "delivery": str(latest_event or "N/A"),
        },
    )
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision="skipped",
        tracking_number=tracking_number,
        error="No latest tracking event",
    )
    return False


def log_timing_validation_error(
    order: ShopifyOrder,
    timing_details: dict,
    tracking_number: str,
    currency: str,
    slack_notifier: SlackNotifier,
):
    err_message = timing_details.pop("reason", "Tracking failed timing validation")
    logger.warning(
        err_message,
        extra={
            "order_id": order.id,
            "order_name": order.name,
            **timing_details,
        },
    )
    slack_notifier.send_warning(
        err_message,
        details={
            "order_id": order.id,
            "order_name": order.name,
            **timing_details,
        },
    )
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision="skipped",
        tracking_number=tracking_number,
        error=err_message,
    )
    return False


def log_invalid_tracking_status(
    order: ShopifyOrder,
    tracking_number: str,
    currency: str,
    tracking_status: str,
    tracking_sub_status: str,
    slack_notifier: SlackNotifier,
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
    slack_notifier.send_warning(
        f"Invalid tracking status for: Order({order.name})",
        details={
            "order_id": order.id,
            "order_name": order.name,
            "tracking_status": tracking_status,
            "tracking_sub_status": tracking_sub_status,
        },
    )
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision="skipped",
        tracking_number=tracking_number,
        error=f"Invalid tracking status for: Order({order.name})",
    )
    return False


def log_invalid_tags_or_chargeback_error(
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
    slack_notifier.send_warning(
        f"{err_message}: Order({order.name})",
        details={
            "order_id": order.id,
            "order_name": order.name,
            "tag": tag,
            **extra_log_details,
        },
    )
    log_refund_audit(
        order_id=order.id,
        order_name=order.name,
        refund_amount=0.0,
        currency=currency,
        decision=log_decision,
        tracking_number=tracking_number,
        error=err_message,
    )
