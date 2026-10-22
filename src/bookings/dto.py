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


class WalkInRequest(HoldRequest):
    """Staff at the counter. May backdate up to staff_backdate_days."""

    notes: str | None = Field(default=None, max_length=1000)


class StaffCancelRequest(BaseModel):
    """The reason is REQUIRED: no refunds, so it is the whole audit trail of
    a disputed cancellation (spec §6.5)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class MarkPaidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ONLINE is absent on purpose: online money arrives only via the provider.
    payment_method: Literal[PaymentMethod.CASH, PaymentMethod.MANUAL_GCASH]


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[BookingStatus.COMPLETED, BookingStatus.NO_SHOW]


MAX_CLOSURE = 31  # days per request — 744 slot rows per court


class ClosureRequest(BaseModel):
    """Close courts for a range. One MAINTENANCE group per court, N slot
    rows each — the same rows that block bookings (ADR 0001)."""

    model_config = ConfigDict(extra="forbid")

    court_ids: list[int] = Field(min_length=1, max_length=50)
    starts_at: AwareDatetime
    ends_at: AwareDatetime  # exclusive, on the hour
    reason: str = Field(min_length=1, max_length=500)


class ResolveRequest(BaseModel):
    """How a human settled a paid-but-unhousable booking — refund, rebook,
    or void. Recorded in the booking's notes."""

    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(min_length=1, max_length=500)


class PlayerCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, min_length=1, max_length=500)


class SlotOut(BaseModel):
    starts_at: datetime
    price_centavos: int


class BookingOut(BaseModel):
    id: int
    reference_code: str
    court_id: int
    venue_id: int
    court_name: str
    venue_name: str
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
            court_name=group.court.name,
            venue_name=group.court.venue.name,
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


class PlayerSummary(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str


class AdminBookingOut(BookingOut):
    """Staff see who booked; players never do (spec §7.2)."""

    player: PlayerSummary | None
    created_by_user_id: int

    @classmethod
    def of_admin(cls, group: BookingGroupModel, now: datetime) -> "AdminBookingOut":
        p = group.player
        return cls(
            **BookingOut.of(group, now).model_dump(),
            player=None
            if p is None
            else PlayerSummary(
                id=p.id, first_name=p.first_name, last_name=p.last_name, email=p.email
            ),
            created_by_user_id=group.created_by_user_id,
        )


class CheckoutBookingOut(BookingOut):
    """A hold plus where to pay for it. The redirect back NEVER confirms the
    booking — the webhook does (spec §5.3)."""

    checkout_url: str

    @classmethod
    def with_url(cls, group: BookingGroupModel, now: datetime, url: str) -> "CheckoutBookingOut":
        return cls(**BookingOut.of(group, now).model_dump(), checkout_url=url)
