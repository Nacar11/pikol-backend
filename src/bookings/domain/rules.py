"""Every booking rule (spec §5.2), as pure functions over a snapshot.

No SQLAlchemy and no clock: `now` is always a parameter, so each guard is
unit-tested at the exact instant that matters. The service layer applies
the resulting transition through the one status writer.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentStatus
from src.bookings.domain.errors import BookingError

SLOT = timedelta(hours=1)

DisplayStatus = Literal[
    "PENDING", "CONFIRMED", "IN_PROGRESS", "COMPLETED", "NO_SHOW", "CANCELLED", "EXPIRED"
]

# No 0/O, 1/I/L, or U: staff read these aloud over the phone.
_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


@dataclass(frozen=True)
class BookingSnapshot:
    type: BookingType
    booking_status: BookingStatus
    payment_status: PaymentStatus
    starts_at: datetime  # first slot
    hours: int
    expires_at: datetime | None

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + self.hours * SLOT

    def is_live_hold(self, now: datetime) -> bool:
        """The PENDING trap in one place: PENDING alone never means held."""
        return (
            self.booking_status == BookingStatus.PENDING
            and self.expires_at is not None
            and self.expires_at > now
        )


def _conflict(code: str, detail: str) -> BookingError:
    return BookingError(code, detail, "conflict")


def _invalid(code: str, detail: str) -> BookingError:
    return BookingError(code, detail, "invalid")


# ── windows ──────────────────────────────────────────────────────────────


def slot_starts(starts_at: datetime, hours: int) -> list[datetime]:
    return [starts_at + h * SLOT for h in range(hours)]


def check_on_the_hour(starts_at: datetime) -> None:
    if starts_at.tzinfo is None or (starts_at.minute, starts_at.second, starts_at.microsecond) != (
        0,
        0,
        0,
    ):
        raise _invalid("NOT_ON_THE_HOUR", "Bookings start on the hour, with an explicit timezone.")


def check_player_window(
    starts_at: datetime, hours: int, now: datetime, *, min_lead_minutes: int, horizon_days: int
) -> None:
    if starts_at < now + timedelta(minutes=min_lead_minutes):
        raise _invalid(
            "TOO_SOON", f"Book at least {min_lead_minutes} minutes before the slot starts."
        )
    if slot_starts(starts_at, hours)[-1] > now + timedelta(days=horizon_days):
        raise _invalid("BEYOND_HORIZON", f"You can book up to {horizon_days} days ahead.")


def check_walk_in_window(starts_at: datetime, now: datetime, *, backdate_days: int) -> None:
    if starts_at < now - timedelta(days=backdate_days):
        raise _invalid("TOO_FAR_BACK", f"Walk-ins can be recorded up to {backdate_days} days back.")


# ── transition guards ────────────────────────────────────────────────────


def can_cancel_by_player(b: BookingSnapshot, now: datetime, *, cutoff_minutes: int) -> None:
    if b.type != BookingType.PLAYER_BOOKING:
        raise _conflict("NOT_CANCELLABLE", "This booking cannot be cancelled here.")
    if b.booking_status == BookingStatus.PENDING:
        if not b.is_live_hold(now):
            raise _conflict("NOT_CANCELLABLE", "This hold has already expired.")
        return
    if b.booking_status != BookingStatus.CONFIRMED:
        raise _conflict("NOT_CANCELLABLE", "This booking can no longer be cancelled.")
    if now >= b.starts_at - timedelta(minutes=cutoff_minutes):
        raise _conflict(
            "CANCELLATION_CUTOFF_PASSED", "It is too close to the start time to cancel."
        )


def can_cancel_by_staff(b: BookingSnapshot) -> None:
    if b.type == BookingType.MAINTENANCE:
        raise _conflict("IS_CLOSURE", "Reopen a closure through the closures route.")
    if b.booking_status != BookingStatus.CONFIRMED:
        raise _conflict("NOT_CANCELLABLE", "Only a confirmed booking can be cancelled.")


def can_cancel_closure(b: BookingSnapshot) -> None:
    if b.type != BookingType.MAINTENANCE or b.booking_status != BookingStatus.CONFIRMED:
        raise _conflict("NOT_CANCELLABLE", "This is not an open closure.")


def can_mark_paid(b: BookingSnapshot) -> None:
    """Walk-ins only: online bookings are paid by the provider, never by hand.
    COMPLETED is allowed because people often pay after they play."""
    if b.type != BookingType.WALK_IN:
        raise _conflict("NOT_A_WALK_IN", "Only walk-in bookings are marked paid by staff.")
    if b.payment_status != PaymentStatus.UNPAID:
        raise _conflict("ALREADY_PAID", "This booking is already paid.")
    if b.booking_status not in (BookingStatus.CONFIRMED, BookingStatus.COMPLETED):
        raise _conflict("NOT_PAYABLE", "This booking can no longer be paid.")


def can_set_outcome(b: BookingSnapshot, now: datetime) -> None:
    if b.type == BookingType.MAINTENANCE or b.booking_status != BookingStatus.CONFIRMED:
        raise _conflict("NO_OUTCOME", "Only a confirmed booking can be marked played or no-show.")
    if b.starts_at > now:
        raise _conflict("NOT_STARTED", "This booking has not started yet.")


def can_confirm_online_payment(b: BookingSnapshot, now: datetime) -> None:
    """PENDING → CONFIRMED, the webhook's transition (wired in Phase 5). A
    payment for a lapsed hold goes to re-acquisition instead (spec §5.5)."""
    if b.type != BookingType.PLAYER_BOOKING or not b.is_live_hold(now):
        raise _conflict("HOLD_NOT_LIVE", "There is no live hold to confirm.")


# ── display ──────────────────────────────────────────────────────────────


def display_status(b: BookingSnapshot, now: datetime) -> DisplayStatus:
    """What a user is shown. IN_PROGRESS is derived, never stored (spec
    §5.1); a lapsed PENDING reads EXPIRED even if no write has expired it."""
    if b.booking_status == BookingStatus.PENDING and not b.is_live_hold(now):
        return "EXPIRED"
    if b.booking_status == BookingStatus.CONFIRMED and b.starts_at <= now < b.ends_at:
        return "IN_PROGRESS"
    return b.booking_status.value


def new_reference_code() -> str:
    """Human-quotable — staff on the phone cannot read out an integer id."""
    return "PKL-" + "".join(secrets.choice(_ALPHABET) for _ in range(5))
