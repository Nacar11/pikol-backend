import random
from datetime import UTC, datetime, timedelta

import pytest

from src.bookings.domain.errors import BookingError
from src.checkouts.domain.rules import (
    CheckoutItem,
    allocate_fee,
    awaiting_payment,
    check_items,
    checkout_display_status,
    is_live,
    undo_kind,
)
from src.payments.domain.enums import PaymentOutcome, RefundKind

NOW = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
TEN = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)  # 10:00 Manila


def _code(items: list[CheckoutItem]) -> str:
    with pytest.raises(BookingError) as exc:
        check_items(items)
    return exc.value.code


def test_items_may_not_overlap_on_one_court() -> None:
    first = CheckoutItem(court_id=1, starts_at=TEN, hours=2)
    overlap = CheckoutItem(court_id=1, starts_at=TEN + timedelta(hours=1), hours=1)

    assert _code([first, overlap]) == "OVERLAPPING_ITEMS"
    check_items([first, CheckoutItem(court_id=2, starts_at=TEN, hours=2)])


def test_twenty_five_hours_is_too_many() -> None:
    day = [CheckoutItem(court_id=c, starts_at=TEN, hours=12) for c in (1, 2)]

    check_items(day)
    assert _code([*day, CheckoutItem(court_id=3, starts_at=TEN, hours=1)]) == "TOO_MANY_HOURS"


def test_empty_checkout_is_invalid() -> None:
    assert _code([]) == "EMPTY_CHECKOUT"


def test_items_start_on_the_hour() -> None:
    assert _code([CheckoutItem(1, TEN + timedelta(minutes=30), 1)]) == "NOT_ON_THE_HOUR"


def test_fee_allocation_sums_to_the_checkout_fee() -> None:
    assert allocate_fee(46, [50000, 50000, 60000]) == [14, 14, 18]
    rng = random.Random(7)
    for _ in range(200):
        prices = [rng.randint(0, 200000) for _ in range(rng.randint(1, 6))]
        fee = rng.randint(0, 10000)
        shares = allocate_fee(fee, prices)
        assert sum(shares) == fee
        assert all(s >= 0 for s in shares)


def test_fee_allocation_with_free_hours() -> None:
    assert allocate_fee(0, [0, 0]) == [0, 0]
    assert allocate_fee(5, [0, 0]) == [0, 5]


def test_lapsed_pending_reads_cancelled() -> None:
    lapsed = NOW - timedelta(seconds=1)

    assert checkout_display_status(PaymentOutcome.PENDING, lapsed, NOW) == "CANCELLED"
    assert not is_live(PaymentOutcome.PENDING, lapsed, NOW)
    assert not is_live(PaymentOutcome.PENDING, NOW, NOW)  # the deadline itself is over


def test_live_pending_reads_pending() -> None:
    live = NOW + timedelta(minutes=5)

    assert checkout_display_status(PaymentOutcome.PENDING, live, NOW) == "PENDING"
    assert is_live(PaymentOutcome.PENDING, live, NOW)


@pytest.mark.parametrize(
    "status",
    [
        PaymentOutcome.SUCCESSFUL,
        PaymentOutcome.FAILED,
        PaymentOutcome.CANCELLED,
        PaymentOutcome.REFUNDED,
    ],
)
def test_final_statuses_read_as_stored(status: PaymentOutcome) -> None:
    assert checkout_display_status(status, NOW - timedelta(hours=1), NOW) == status
    assert not is_live(status, NOW + timedelta(minutes=5), NOW)


def test_void_until_manila_midnight() -> None:
    """Scenario 10: a void works until 12:00 AM GMT+8 of the payment's day."""
    paid = datetime(2026, 10, 28, 15, 59, tzinfo=UTC)  # 23:59 Manila

    assert undo_kind(paid, paid + timedelta(seconds=30)) == RefundKind.VOID
    assert undo_kind(paid, datetime(2026, 10, 28, 16, 0, tzinfo=UTC)) == RefundKind.REFUND


def test_awaiting_payment_while_maya_may_still_take_it() -> None:
    opened = NOW - timedelta(minutes=10)

    assert awaiting_payment(PaymentOutcome.CANCELLED, None, opened, NOW)
    assert awaiting_payment(PaymentOutcome.PENDING, None, opened, NOW)
    assert not awaiting_payment(PaymentOutcome.CANCELLED, "PAYMENT_CANCELLED", opened, NOW)
    assert not awaiting_payment(PaymentOutcome.SUCCESSFUL, None, opened, NOW)
    assert not awaiting_payment(PaymentOutcome.CANCELLED, None, NOW - timedelta(hours=1), NOW)
    assert not awaiting_payment(PaymentOutcome.CANCELLED, None, None, NOW)
