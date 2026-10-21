from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentMethod, PaymentStatus
from src.bookings.domain.rules import DisplayStatus, display_status
from src.bookings.persistence.models import BookingGroupModel
from src.bookings.service_support import snapshot

MAX_HOURS = 12


class HoldRequest(BaseModel):
    """A booking is a start and a length — consecutive by construction."""

    model_config = ConfigDict(extra="forbid")

    court_id: int
    starts_at: AwareDatetime  # on the hour; any offset, e.g. 2026-09-19T18:00:00+08:00
    hours: int = Field(default=1, ge=1, le=MAX_HOURS)


class SlotOut(BaseModel):
    starts_at: datetime
    price_centavos: int


class BookingOut(BaseModel):
    id: int
    reference_code: str
    court_id: int
    venue_id: int
    type: BookingType
    booking_status: BookingStatus
    payment_status: PaymentStatus
    # What to SHOW: lapsed holds read EXPIRED, live sessions IN_PROGRESS.
    display_status: DisplayStatus
    starts_at: datetime
    ends_at: datetime
    hours: int
    slots: list[SlotOut]
    price_centavos: int
    fee_centavos: int
    total_centavos: int
    payment_method: PaymentMethod | None
    expires_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    notes: str | None
    created_at: datetime

    @classmethod
    def of(cls, group: BookingGroupModel, now: datetime) -> "BookingOut":
        b = snapshot(group)
        return cls(
            id=group.id,
            reference_code=group.reference_code,
            court_id=group.court_id,
            venue_id=group.court.venue_id,
            type=group.type,
            booking_status=group.booking_status,
            payment_status=group.payment_status,
            display_status=display_status(b, now),
            starts_at=b.starts_at,
            ends_at=b.ends_at,
            hours=b.hours,
            slots=[
                SlotOut(starts_at=s.starts_at, price_centavos=s.price_centavos) for s in group.slots
            ],
            price_centavos=group.price_centavos,
            fee_centavos=group.fee_centavos,
            total_centavos=group.total_centavos,
            payment_method=group.payment_method,
            expires_at=group.expires_at,
            cancelled_at=group.cancelled_at,
            cancellation_reason=group.cancellation_reason,
            notes=group.notes,
            created_at=group.created_at,
        )


GridStatus = Literal["AVAILABLE", "BOOKED", "CLOSED", "PAST"]


class GridSlot(BaseModel):
    starts_at: datetime
    hour: int  # Manila
    status: GridStatus
    price_centavos: int


class GridCourt(BaseModel):
    court_id: int
    name: str
    slots: list[GridSlot]


class Availability(BaseModel):
    venue_id: int
    date: date
    courts: list[GridCourt]
