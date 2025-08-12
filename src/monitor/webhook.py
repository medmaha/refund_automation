from datetime import datetime, timedelta

from src.logger import get_logger
from src.models.event import EventType, WebhookEvent

logger = get_logger(__name__)


def handle_17track_webhook(payload: WebhookEvent):
    
    # TODO: To be used later with webhooks

    event = payload.event
    data = payload.data
    order_id = data.number
    tracking_info = data.track_info

    logger.info(
        f"Received webhook payload for order_id={data.number}: status={payload.event}, destination={tracking_info.latest_event.location}"
    )

    if event == EventType.TRACKING_UPDATED:
        last_modified_time = datetime.fromisoformat(tracking_info.latest_event.time_iso)
        delay_days = 3
        refund_time = last_modified_time + timedelta(days=delay_days)

        logger.info(
            f"Order {order_id} updated at {last_modified_time}. Scheduling refund at {refund_time}."
        )

        # TODO: handle webhook payload data
    else:
        logger.info(
            f"Order {order_id} not eligible for refund scheduling. Status: status={payload.event}, destination={tracking_info.latest_event.location}"
        )
