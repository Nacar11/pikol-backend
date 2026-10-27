"""The checkout's rules (ADR 0004), as pure functions.

No SQLAlchemy and no clock: `now` is always a parameter. A checkout is one
"Pay now" at one venue; each item is a run of consecutive hours on one court
and becomes one booking group.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.bookings.domain.errors import BookingError
from src.bookings.domain.rules import check_on_the_hour, slot_starts
from src.payments.domain.enums import PaymentOutcome, RefundKind
from src.utils.time import MANILA

MAX_CHECKOUT_HOURS = 24
# Maya's session lives an hour and cannot be closed early (ADR 0004).
MAYA_SESSION = timedelta(hours=1)


@dataclass(frozen=True)
class CheckoutItem:
    court_id: int
    starts_at: datetime
    hours: int


def check_items(items: Sequence[CheckoutItem]) -> None:
    if not items:
        raise BookingError("EMPTY_CHECKOUT", "Pick at least one hour.", "invalid")
    for item in items:
        check_on_the_hour(item.starts_at)
    if sum(item.hours for item in items) > MAX_CHECKOUT_HOURS:
        raise BookingError(
            "TOO_MANY_HOURS", f"Book at most {MAX_CHECKOUT_HOURS} hours at a time.", "invalid"
        )
    seen: set[tuple[int, datetime]] = set()
    for item in items:
        for start in slot_starts(item.starts_at, item.hours):
            if (item.court_id, start) in seen:
                raise BookingError(
                    "OVERLAPPING_ITEMS", "The same court-hour is picked twice.", "invalid"
                )
            seen.add((item.court_id, start))


def allocate_fee(fee_centavos: int, prices: Sequence[int]) -> list[int]:
    """Split the checkout's fee across its groups, in proportion to price.

    The fee is computed once on the subtotal — what the customer was shown —
    so the groups' shares must add back to exactly that: each takes its floor
    share and the last takes the remainder.
    """
    total = sum(prices)
    shares = [fee_centavos * p // total if total else 0 for p in prices[:-1]]
    return [*shares, fee_centavos - sum(shares)]


def is_live(status: PaymentOutcome, expires_at: datetime, now: datetime) -> bool:
    """The PENDING trap for checkouts: PENDING alone never means held."""
    return status == PaymentOutcome.PENDING and expires_at > now


def checkout_display_status(
    status: PaymentOutcome, expires_at: datetime, now: datetime
) -> PaymentOutcome:
    """What the customer sees. A hold that ended reads CANCELLED even before
    anything writes it, because cleanup is lazy (ADR 0004 decision 4)."""
    if status == PaymentOutcome.PENDING and not is_live(status, expires_at, now):
        return PaymentOutcome.CANCELLED
    return status


def awaiting_payment(
    status: PaymentOutcome,
    provider_status: str | None,
    attempt_opened_at: datetime | None,
    now: datetime,
) -> bool:
    """Could Maya still take money for this checkout? True while an attempt
    is open — Maya has reported no outcome and its hour has not run out —
    and nothing has settled the checkout. A hold that ended or a customer who
    cancelled may still pay on a Maya tab, and the result page must keep
    confirming (and asking Maya) rather than settle on "cancelled"."""
    return (
        status in (PaymentOutcome.PENDING, PaymentOutcome.CANCELLED)
        and attempt_opened_at is not None
        and provider_status is None
        and now < attempt_opened_at + MAYA_SESSION
    )


def undo_kind(paid_at: datetime, now: datetime) -> RefundKind:
    """Maya voids only until 12:00 AM GMT+8 of the payment's day; after that
    it is a refund."""
    same_day = paid_at.astimezone(MANILA).date() == now.astimezone(MANILA).date()
    return RefundKind.VOID if same_day else RefundKind.REFUND
