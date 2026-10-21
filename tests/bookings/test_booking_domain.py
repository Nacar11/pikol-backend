import re
from datetime import UTC, datetime, timedelta

import pytest

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentStatus
from src.bookings.domain.errors import BookingError
from src.bookings.domain.rules import (
    BookingSnapshot,
    can_cancel_by_player,
    can_cancel_by_staff,
    can_cancel_closure,
    can_confirm_online_payment,
    can_mark_paid,
    can_set_outcome,
    check_on_the_hour,
    check_player_window,
    check_walk_in_window,
    display_status,
    new_reference_code,
    slot_starts,
)

NOW = datetime(2026, 9, 15, 10, 7, tzinfo=UTC)
START = datetime(2026, 9, 15, 12, tzinfo=UTC)


def snap(
    type: BookingType = BookingType.PLAYER_BOOKING,
    status: BookingStatus = BookingStatus.CONFIRMED,
    payment: PaymentStatus = PaymentStatus.PAID,
    starts_at: datetime = START,
    hours: int = 2,
    expires_at: datetime | None = None,
) -> BookingSnapshot:
    return BookingSnapshot(type, status, payment, starts_at, hours, expires_at)


def held(expires_at: datetime) -> BookingSnapshot:
    return snap(status=BookingStatus.PENDING, payment=PaymentStatus.UNPAID, expires_at=expires_at)


def walk_in(status: BookingStatus = BookingStatus.CONFIRMED) -> BookingSnapshot:
    return snap(BookingType.WALK_IN, status, PaymentStatus.UNPAID)


def closure() -> BookingSnapshot:
    return snap(BookingType.MAINTENANCE, payment=PaymentStatus.UNPAID)


def code_of(call: object) -> str:
    assert isinstance(call, BookingError)
    return call.code


# ── windows ──────────────────────────────────────────────────────────────


def test_slot_starts_are_consecutive_hours() -> None:
    assert slot_starts(START, 3) == [START + timedelta(hours=h) for h in range(3)]


@pytest.mark.parametrize(
    "bad", [START.replace(minute=30), START.replace(second=1), datetime(2026, 9, 15, 12)]
)
def test_not_on_the_hour_or_naive_rejected(bad: datetime) -> None:
    with pytest.raises(BookingError) as exc:
        check_on_the_hour(bad)
    assert exc.value.code == "NOT_ON_THE_HOUR"
    assert exc.value.kind == "invalid"


def test_player_window_enforces_min_lead() -> None:
    with pytest.raises(BookingError) as exc:
        check_player_window(
            NOW + timedelta(minutes=19), 1, NOW, min_lead_minutes=20, horizon_days=30
        )
    assert exc.value.code == "TOO_SOON"
    check_player_window(NOW + timedelta(minutes=20), 1, NOW, min_lead_minutes=20, horizon_days=30)


def test_player_window_enforces_horizon_on_the_last_slot() -> None:
    edge = NOW + timedelta(days=30)
    check_player_window(edge, 1, NOW, min_lead_minutes=20, horizon_days=30)
    with pytest.raises(BookingError) as exc:
        check_player_window(edge, 2, NOW, min_lead_minutes=20, horizon_days=30)
    assert exc.value.code == "BEYOND_HORIZON"


def test_walk_in_window_allows_backdating_within_limit() -> None:
    check_walk_in_window(NOW - timedelta(days=7), NOW, backdate_days=7)
    with pytest.raises(BookingError) as exc:
        check_walk_in_window(NOW - timedelta(days=7, hours=1), NOW, backdate_days=7)
    assert exc.value.code == "TOO_FAR_BACK"


# ── guards ───────────────────────────────────────────────────────────────


def test_player_may_abandon_live_hold_but_not_lapsed_one() -> None:
    can_cancel_by_player(held(NOW + timedelta(minutes=1)), NOW, cutoff_minutes=0)
    with pytest.raises(BookingError):
        can_cancel_by_player(held(NOW), NOW, cutoff_minutes=0)


def test_player_cancel_respects_cutoff() -> None:
    can_cancel_by_player(snap(), NOW, cutoff_minutes=60)  # 113 min before start
    with pytest.raises(BookingError) as exc:
        can_cancel_by_player(snap(), NOW, cutoff_minutes=120)
    assert exc.value.code == "CANCELLATION_CUTOFF_PASSED"


@pytest.mark.parametrize("status", [BookingStatus.COMPLETED, BookingStatus.CANCELLED])
def test_player_cannot_cancel_finished_booking(status: BookingStatus) -> None:
    with pytest.raises(BookingError) as exc:
        can_cancel_by_player(snap(status=status), NOW, cutoff_minutes=0)
    assert exc.value.kind == "conflict"


def test_staff_cancel_confirmed_only_and_never_a_closure() -> None:
    can_cancel_by_staff(walk_in())
    for denied in (closure(), held(NOW + timedelta(hours=1)), snap(status=BookingStatus.NO_SHOW)):
        with pytest.raises(BookingError):
            can_cancel_by_staff(denied)


def test_closure_cancel_only_for_closures() -> None:
    can_cancel_closure(closure())
    with pytest.raises(BookingError):
        can_cancel_closure(walk_in())


@pytest.mark.parametrize("status", [BookingStatus.CONFIRMED, BookingStatus.COMPLETED])
def test_mark_paid_unpaid_walk_in(status: BookingStatus) -> None:
    can_mark_paid(walk_in(status))


@pytest.mark.parametrize(
    "denied",
    [
        snap(payment=PaymentStatus.UNPAID),  # a player booking
        snap(BookingType.WALK_IN, payment=PaymentStatus.PAID),  # already paid
        walk_in(BookingStatus.CANCELLED),
        closure(),
    ],
)
def test_mark_paid_denied(denied: BookingSnapshot) -> None:
    with pytest.raises(BookingError):
        can_mark_paid(denied)


def test_outcome_only_once_started() -> None:
    with pytest.raises(BookingError) as exc:
        can_set_outcome(snap(), NOW)
    assert exc.value.code == "NOT_STARTED"
    can_set_outcome(snap(), START)
    with pytest.raises(BookingError):
        can_set_outcome(closure(), START)


def test_online_confirmation_needs_a_live_hold() -> None:
    can_confirm_online_payment(held(NOW + timedelta(seconds=1)), NOW)
    with pytest.raises(BookingError):
        can_confirm_online_payment(held(NOW), NOW)
    with pytest.raises(BookingError):
        can_confirm_online_payment(walk_in(), NOW)


# ── display ──────────────────────────────────────────────────────────────


def test_lapsed_pending_displays_expired() -> None:
    """The PENDING trap, applied to what the user sees."""
    assert display_status(held(NOW), NOW) == "EXPIRED"
    assert display_status(held(NOW + timedelta(seconds=1)), NOW) == "PENDING"


def test_confirmed_is_in_progress_only_during_its_hours() -> None:
    b = snap(hours=2)
    assert display_status(b, START - timedelta(seconds=1)) == "CONFIRMED"
    assert display_status(b, START) == "IN_PROGRESS"
    assert display_status(b, START + timedelta(hours=1, minutes=59)) == "IN_PROGRESS"
    assert display_status(b, START + timedelta(hours=2)) == "CONFIRMED"


def test_reference_code_shape() -> None:
    codes = {new_reference_code() for _ in range(200)}
    assert all(re.fullmatch(r"PKL-[A-HJ-KMNP-TV-Z2-9]{5}", c) for c in codes)
    assert len(codes) > 190
