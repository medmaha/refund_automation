from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class TrackingStatus(Enum):
    NOTFOUND = "NotFound"
    IN_TRANSIT = "InTransit"
    DELIVERED = "Delivered"

class TrackingSubStatus(Enum):
    IN_TRANSIT_OTHER = "InTransit"
    NOTFOUND_OTHER = "NotFound_Other"
    DELIVERED_OTHER = "Delivered_Other"
    DELIVERED_SIGNED = "Delivered_Signed"
    DELIVERED_AT_LOCKER = "Delivered_at_locked"
    EXCEPTION_RETURNED = (
        "Exception_Returned"  # Sender has successfully received the returned package.
    )
    EXCEPTION_RETURNING = (
        "Exception_Returning"  # Package is being returned to the sender.
    )


class LatestStatus(BaseModel):
    status: Optional[TrackingStatus]
    sub_status: Optional[TrackingSubStatus] = Field(default=None)
    sub_status_descr: Optional[str] = Field(default="")


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
    milestone: List[Milestone] = Field(default_factory=list)
    latest_event: Optional[LatestEvent] = Field(default=None)


class TrackingData(BaseModel):
    tag: Optional[str] = Field(default_factory=list)
    carrier: Optional[int]
    number: Optional[str]
    carrier_disagreement: Optional[dict] = Field(default_factory=dict)
    track_info: Optional[TrackInfo]

    def __str__(self):
        return f"TrackingData: ({self.number}, {self.carrier})"

    def __repr__(self):
        return f"TrackingData(number={self.number}, carrier={self.carrier})"

    @property
    def is_carrier_disagreement(self):
        return bool(self.tag) and ("carrier_mismatch" in self.tag)
