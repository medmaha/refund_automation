from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class TrackingStatus(Enum):
    NOTFOUND = "NotFound"
    InfoReceived = "InfoReceived"
    IN_TRANSIT = "InTransit"
    Expired = "Expired"
    AvailableForPickup = "AvailableForPickup"
    OutForDelivery = "OutForDelivery"
    DeliveryFailure = "DeliveryFailure"
    DELIVERED = "Delivered"
    Exception = "Exception"


class TrackingSubStatus(Enum):
    IN_TRANSIT_OTHER = "InTransit"
    NOTFOUND_OTHER = "NotFound_Other"
    DELIVERED_OTHER = "Delivered_Other"
    Exception_Returned = "Exception_Returned"
    Exception_Returning = "Exception_Returning"


class LatestStatus(BaseModel):
    status: Optional[TrackingStatus]
    sub_status: Optional[TrackingSubStatus]
    sub_status_descr: Optional[str]


class LatestEvent(BaseModel):
    time_iso: Optional[str]
    time_utc: Optional[str]
    description: Optional[str]
    location: Optional[str]
    stage: Optional[str]
    sub_status: Optional[str]


class Milestone(BaseModel):
    key_stage: str
    time_iso: Optional[str]
    time_utc: Optional[str]


class TrackInfo(BaseModel):
    milestone: List[Milestone]
    latest_status: LatestStatus
    latest_event: Optional[LatestEvent]


class TrackingData(BaseModel):
    tag: str
    number: str
    carrier: int
    track_info: Optional[TrackInfo]

    def __str__(self):
        return f"TrackingData: ({self.number}, {self.carrier})"

    def __repr__(self):
        return f"TrackingData(number={self.number}, carrier={self.carrier})"
