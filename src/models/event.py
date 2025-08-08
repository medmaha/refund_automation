from enum import Enum

from pydantic import BaseModel

from src.models.tracking import TrackingData


class EventType(Enum):
    TRACKING_STOPPED = "TRACKING_STOPPED"
    TRACKING_UPDATED = "TRACKING_UPDATED"


class WebhookEvent(BaseModel):
    event: EventType
    data: TrackingData
