from typing import List, Optional

from pydantic import BaseModel

main_status = [
    "NotFound",
    "InfoReceived",
    "InTransit",
    "Expired",
    "AvailableForPickup",
    "OutForDelivery",
    "DeliveryFailure",
    "Delivered",
    "Exception",
]

sub_statues = [
    "Exception_Returned",
    "Exception_Returning",
]


class LatestStatus(BaseModel):
    status: Optional[str]
    sub_status: Optional[str]
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
    latest_status: LatestStatus
    latest_event: LatestEvent
    milestone: List[Milestone]


class TrackingData(BaseModel):
    tag: str
    number: str
    carrier: int
    param: Optional[str]
    track_info: Optional[TrackInfo]

    def __str__(self):
        return f"TrackingData: ({self.number}, {self.carrier})"

    def __repr__(self):
        return f"TrackingData(number={self.number}, carrier={self.carrier})"
