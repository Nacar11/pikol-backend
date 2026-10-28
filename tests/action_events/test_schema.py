import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType, ActorKind
from src.action_events.persistence.models import ActionEventModel
from src.action_events.persistence.repository import action_event_repository
from src.action_events.service import event_service
from src.bookings.domain.enums import BookingStatus, BookingType
from src.bookings.persistence.models import BookingGroupModel
from tests.auth.helpers import RoleCode, make_court, make_user, make_venue
from tests.bookings.helpers import insert_booking, next_hour


def _backfill_sql() -> str:
    """The migration's own SQL: a migration never imports app code, which
    would change under it."""
    path = Path("src/database/migrations/versions/0011_action_events.py")
    spec = importlib.util.spec_from_file_location("m0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.BACKFILL_SQL)


async def _events(session: AsyncSession, *group_ids: int) -> list[ActionEventModel]:
    """Only this test's bookings: the dev database's own rows are backfilled too."""
    rows = await session.execute(
        select(ActionEventModel)
        .where(ActionEventModel.booking_group_id.in_(group_ids))
        .order_by(ActionEventModel.id)
    )
    return list(rows.scalars().all())


async def test_event_needs_a_booking_or_checkout(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_action_event_has_subject"):
        async with db_session.begin_nested():
            await action_event_repository.insert(
                db_session,
                type=ActionEventType.CONFIRMED,
                actor_kind=ActorKind.SYSTEM,
                actor_user_id=None,
                occurred_at=datetime.now(UTC),
            )


async def test_system_events_have_no_user(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    court = await make_court(db_session, venue.id)
    group = await insert_booking(
        db_session, court_id=court.id, created_by=staff.id, starts_at=next_hour()
    )
    with pytest.raises(IntegrityError, match="ck_action_event_actor"):
        async with db_session.begin_nested():
            await action_event_repository.insert(
                db_session,
                type=ActionEventType.CONFIRMED,
                actor_kind=ActorKind.SYSTEM,
                actor_user_id=staff.id,
                occurred_at=datetime.now(UTC),
                booking_group_id=group.id,
            )


async def test_record_fills_checkout_from_group_and_names_the_customer(
    db_session: AsyncSession,
) -> None:
    venue = await make_venue(db_session)
    player = await make_user(db_session)
    court = await make_court(db_session, venue.id)
    expires = datetime.now(UTC) + timedelta(minutes=5)
    group = await insert_booking(
        db_session,
        court_id=court.id,
        created_by=player.id,
        starts_at=next_hour(),
        type=BookingType.PLAYER_BOOKING,
        status=BookingStatus.PENDING,
        player_id=player.id,
        expires_at=expires,
    )

    await event_service.record(
        db_session,
        ActionEventType.HOLD_PLACED,
        actor=player,
        group=group,
        data={"expires_at": expires},
    )

    [event] = await _events(db_session, group.id)
    assert event.booking_group_id == group.id
    assert event.checkout_id == group.checkout_id
    assert event.actor_kind == ActorKind.CUSTOMER
    assert event.actor_user_id == player.id
    assert event.data == {"expires_at": expires.isoformat()}


async def test_record_by_staff_and_by_system(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    court = await make_court(db_session, venue.id)
    group = await insert_booking(
        db_session, court_id=court.id, created_by=staff.id, starts_at=next_hour()
    )
    await event_service.record(db_session, ActionEventType.WALK_IN_BOOKED, actor=staff, group=group)
    await event_service.record(db_session, ActionEventType.EXPIRED, actor=None, group=group)

    walk_in, expired = await _events(db_session, group.id)
    assert (walk_in.actor_kind, walk_in.actor_user_id) == (ActorKind.STAFF, staff.id)
    assert (expired.actor_kind, expired.actor_user_id) == (ActorKind.SYSTEM, None)
    assert walk_in.checkout_id is None


async def test_backfill_creates_creation_and_cancellation_events(
    db_session: AsyncSession,
) -> None:
    venue = await make_venue(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    player = await make_user(db_session)
    court = await make_court(db_session, venue.id)
    walk_in = await insert_booking(
        db_session, court_id=court.id, created_by=staff.id, starts_at=next_hour()
    )
    held = await insert_booking(
        db_session,
        court_id=court.id,
        created_by=player.id,
        starts_at=next_hour(hour=12),
        type=BookingType.PLAYER_BOOKING,
        status=BookingStatus.PENDING,
        player_id=player.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    cancelled_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == held.id)
        .values(
            booking_status=BookingStatus.CANCELLED,
            expires_at=None,
            cancelled_at=cancelled_at,
            cancelled_by_user_id=player.id,
            cancellation_reason="Changed plans",
        )
    )

    await db_session.execute(text(_backfill_sql()))

    events = {
        (e.booking_group_id, e.type): e for e in await _events(db_session, walk_in.id, held.id)
    }
    assert set(events) == {
        (walk_in.id, ActionEventType.WALK_IN_BOOKED),
        (held.id, ActionEventType.HOLD_PLACED),
        (held.id, ActionEventType.CANCELLED),
    }
    assert events[(walk_in.id, ActionEventType.WALK_IN_BOOKED)].occurred_at == walk_in.created_at
    cancelled = events[(held.id, ActionEventType.CANCELLED)]
    assert cancelled.occurred_at == cancelled_at
    assert cancelled.actor_kind == ActorKind.CUSTOMER
    assert cancelled.data == {"reason": "Changed plans"}
    assert cancelled.checkout_id == held.checkout_id
