from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.bookings.domain.enums import BookingStatus
from src.bookings.domain.rules import SLOT
from src.bookings.dto import MAX_HOURS
from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.domain.rules import MAX_CHECKOUT_HOURS, checkout_display_status
from src.checkouts.persistence.models import CheckoutModel
from src.payments.domain.enums import PaymentOutcome, RefundKind


class CheckoutItemIn(BaseModel):
    """A run of consecutive hours on one court — one booking group."""

    model_config = ConfigDict(extra="forbid")

    court_id: int
    starts_at: AwareDatetime  # on the hour; any offset
    hours: int = Field(default=1, ge=1, le=MAX_HOURS)


class CheckoutCreate(BaseModel):
    """Everything picked before "Pay now", at one venue (ADR 0004)."""

    model_config = ConfigDict(extra="forbid")

    items: list[CheckoutItemIn] = Field(min_length=1, max_length=MAX_CHECKOUT_HOURS)


class CheckoutBookingOut(BaseModel):
    id: int
    reference_code: str
    court_id: int
    court_name: str
    starts_at: datetime
    ends_at: datetime
    hours: int
    price_centavos: int
    booking_status: BookingStatus

    @classmethod
    def of(cls, group: BookingGroupModel) -> "CheckoutBookingOut":
        starts_at = group.slots[0].starts_at
        return cls(
            id=group.id,
            reference_code=group.reference_code,
            court_id=group.court_id,
            court_name=group.court.name,
            starts_at=starts_at,
            ends_at=starts_at + len(group.slots) * SLOT,
            hours=len(group.slots),
            price_centavos=group.price_centavos,
            booking_status=group.booking_status,
        )


class CheckoutOut(BaseModel):
    id: int
    reference_code: str
    venue_id: int
    venue_name: str
    # What to SHOW: a hold that ended reads CANCELLED before anything writes it.
    status: PaymentOutcome
    expires_at: datetime
    price_centavos: int
    fee_centavos: int
    total_centavos: int
    # Maya's page, while there is still something to pay for.
    checkout_url: str | None
    refund_kind: RefundKind | None
    # Maya may still take money (an open attempt): keep confirming.
    awaiting_payment: bool
    bookings: list[CheckoutBookingOut]
    created_at: datetime

    @classmethod
    def of(
        cls,
        checkout: CheckoutModel,
        now: datetime,
        *,
        checkout_url: str | None,
        refund_kind: RefundKind | None,
        awaiting_payment: bool,
    ) -> "CheckoutOut":
        status = checkout_display_status(checkout.status, checkout.expires_at, now)
        return cls(
            id=checkout.id,
            reference_code=checkout.reference_code,
            venue_id=checkout.venue_id,
            venue_name=checkout.venue.name,
            status=status,
            expires_at=checkout.expires_at,
            price_centavos=checkout.price_centavos,
            fee_centavos=checkout.fee_centavos,
            total_centavos=checkout.total_centavos,
            checkout_url=checkout_url if status == PaymentOutcome.PENDING else None,
            refund_kind=refund_kind,
            awaiting_payment=awaiting_payment,
            bookings=[CheckoutBookingOut.of(g) for g in checkout.bookings],
            created_at=checkout.created_at,
        )
