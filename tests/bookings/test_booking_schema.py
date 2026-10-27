from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, BookingType
from src.bookings.persistence.models import BookingGroupModel
from src.bookings.persistence.repository import booking_repository
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import RoleCode, make_court, make_user, make_venue
from tests.bookings.helpers import insert_booking, next_hour


@pytest.fixture
async def venue(db_session: AsyncSession) -> VenueModel:
    return await make_venue(db_session)


@pytest.fixture
async def staff(db_session: AsyncSession, venue: VenueModel) -> UserModel:
    return await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)


@pytest.fixture
async def court_id(db_session: AsyncSession, venue: VenueModel) -> int:
    return (await make_court(db_session, venue.id)).id


async def _book(
    session: AsyncSession,
    court_id: int,
    staff: UserModel,
    *,
    hours: int = 1,
    player_id: int | None = None,
) -> BookingGroupModel:
    return await insert_booking(
        session,
        court_id=court_id,
        created_by=staff.id,
        starts_at=next_hour(),
        hours=hours,
        player_id=player_id,
    )


async def test_slot_blocked_by_active_booking(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    await _book(db_session, court_id, staff)

    with pytest.raises(AppError) as exc:
        await _book(db_session, court_id, staff)

    assert exc.value.status == 409
    assert exc.value.code == "SLOT_UNAVAILABLE"


async def test_cancelled_slot_frees_it(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    group = await _book(db_session, court_id, staff)
    await booking_repository.set_status(
        db_session,
        group,
        BookingStatus.CANCELLED,
        cancelled_at=datetime.now(UTC),
        cancelled_by_user_id=staff.id,
        cancellation_reason="test",
    )

    await _book(db_session, court_id, staff)


@pytest.mark.parametrize("finished", [BookingStatus.COMPLETED, BookingStatus.NO_SHOW])
async def test_completed_and_no_show_still_block(
    db_session: AsyncSession, court_id: int, staff: UserModel, finished: BookingStatus
) -> None:
    """The predicate is inverted deliberately (ADR 0001): a finished booking
    keeps its slot, so a backdated walk-in cannot land on top of it."""
    group = await _book(db_session, court_id, staff)
    await booking_repository.set_status(db_session, group, finished)

    with pytest.raises(AppError):
        await _book(db_session, court_id, staff)


async def _raw_violation(session: AsyncSession, sql: str) -> str:
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            await session.execute(text(sql))
    return str(exc.value)


async def test_slot_must_be_on_the_hour(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    group = await _book(db_session, court_id, staff)

    message = await _raw_violation(
        db_session,
        f"INSERT INTO booking_slots (booking_group_id, court_id, starts_at, price_centavos, "
        f"booking_status) VALUES ({group.id}, {court_id}, '2030-01-01 10:30:00+00', 0, "
        f"'CANCELLED')",
    )

    assert "ck_slot_on_the_hour" in message


async def test_pending_requires_expiry_and_only_pending_has_one(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    group = await _book(db_session, court_id, staff)
    gid = group.id

    assert "ck_pending_has_expiry" in await _raw_violation(
        db_session, f"UPDATE booking_groups SET booking_status = 'PENDING' WHERE id = {gid}"
    )
    assert "ck_pending_has_expiry" in await _raw_violation(
        db_session, f"UPDATE booking_groups SET expires_at = now() WHERE id = {gid}"
    )


async def test_cancellation_must_be_documented(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    group = await _book(db_session, court_id, staff)

    message = await _raw_violation(
        db_session,
        f"UPDATE booking_groups SET cancelled_at = now() WHERE id = {group.id}",
    )

    assert "ck_cancellation_is_documented" in message


async def test_only_player_booking_has_player(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    with pytest.raises(IntegrityError) as exc:
        await _book(db_session, court_id, staff, player_id=staff.id)  # a WALK_IN with a player

    assert "ck_player_only_on_player_booking" in str(exc.value)


async def test_set_status_mirrors_every_slot(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    """Spec §4.2's invariant, and the dedicated test it asks for."""
    group = await _book(db_session, court_id, staff, hours=3)

    for status in (BookingStatus.COMPLETED, BookingStatus.CANCELLED):
        extra = (
            {
                "cancelled_at": datetime.now(UTC),
                "cancelled_by_user_id": staff.id,
                "cancellation_reason": "x",
            }
            if status == BookingStatus.CANCELLED
            else {}
        )
        await booking_repository.set_status(db_session, group, status, **extra)
        rows = await db_session.execute(
            text("SELECT booking_status FROM booking_slots WHERE booking_group_id = :g"),
            {"g": group.id},
        )
        assert [r[0] for r in rows] == [status.value] * 3


async def test_expire_lapsed_releases_all_slots_of_the_group(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    """Contesting ONE hour of a lapsed 2-hour hold frees BOTH hours: the
    group is the unit that was never paid for."""
    player = await make_user(db_session)
    start = next_hour()
    now = datetime.now(UTC)
    hold = await insert_booking(
        db_session,
        court_id=court_id,
        created_by=player.id,
        player_id=player.id,
        type=BookingType.PLAYER_BOOKING,
        status=BookingStatus.PENDING,
        expires_at=now - timedelta(seconds=1),
        starts_at=start,
        hours=2,
    )

    expired = await booking_repository.expire_lapsed(
        db_session, court_id, [start + timedelta(hours=1)], now
    )

    await db_session.refresh(hold)
    assert expired == [hold.id]
    assert hold.booking_status == BookingStatus.EXPIRED
    assert hold.expires_at is None
    assert {s.booking_status for s in hold.slots} == {BookingStatus.EXPIRED}


async def test_expire_lapsed_leaves_live_holds_alone(
    db_session: AsyncSession, court_id: int, staff: UserModel
) -> None:
    player = await make_user(db_session)
    start = next_hour()
    await insert_booking(
        db_session,
        court_id=court_id,
        created_by=player.id,
        player_id=player.id,
        type=BookingType.PLAYER_BOOKING,
        status=BookingStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        starts_at=start,
    )

    assert (
        await booking_repository.expire_lapsed(db_session, court_id, [start], datetime.now(UTC))
        == []
    )
