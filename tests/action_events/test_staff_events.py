from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType as T
from src.action_events.domain.enums import ActorKind
from src.action_events.persistence.models import ActionEventModel
from src.bookings.persistence.models import BookingGroupModel
from tests.action_events.helpers import events_of, types_of
from tests.bookings.conftest import ADMIN, World, hold
from tests.bookings.helpers import insert_booking, next_hour
from tests.bookings.test_closures import CLOSURES, close
from tests.bookings.test_staff_ops import BOOKINGS, this_hour, walk_in


async def test_walk_in_records_walk_in_booked(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = (
        await walk_in(client, world.staff_a, world.a.court.id, next_hour(), notes="Lessons")
    ).json()

    [event] = await events_of(db_session, group_id=booking["id"])
    assert event.type == T.WALK_IN_BOOKED
    assert (event.actor_kind, event.actor_user_id) == (ActorKind.STAFF, world.staff_a_user.id)
    assert event.data == {"notes": "Lessons"}


async def test_mark_paid_records_method_and_amount(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()

    await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "CASH"},
    )

    *_, paid = await events_of(db_session, group_id=booking["id"])
    assert paid.type == T.MARKED_PAID
    assert paid.actor_user_id == world.staff_a_user.id
    assert paid.data == {"method": "CASH", "amount_centavos": booking["total_centavos"]}


@pytest.mark.parametrize("outcome", ["COMPLETED", "NO_SHOW"])
async def test_outcome_records_its_event(
    client: AsyncClient, world: World, db_session: AsyncSession, outcome: str
) -> None:
    start = this_hour() - timedelta(days=1)
    booking = (await walk_in(client, world.staff_a, world.a.court.id, start)).json()

    await client.post(
        f"{BOOKINGS}/{booking['id']}/outcome", headers=world.staff_a, json={"outcome": outcome}
    )

    assert await types_of(db_session, group_id=booking["id"]) == [T.WALK_IN_BOOKED, outcome]


async def test_staff_cancel_records_reason(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()

    await client.post(
        f"{BOOKINGS}/{booking['id']}/cancel", headers=world.staff_a, json={"reason": "Rain"}
    )

    *_, cancelled = await events_of(db_session, group_id=booking["id"])
    assert cancelled.type == T.CANCELLED
    assert cancelled.actor_kind == ActorKind.STAFF
    assert cancelled.data == {"reason": "Rain"}


async def test_closure_records_one_event_per_court_and_reopen(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour(2)
    closures = (
        await close(
            client,
            world.staff_a,
            [world.a.court.id, world.a.court2.id],
            start,
            start + timedelta(hours=2),
        )
    ).json()

    await client.post(
        f"{CLOSURES}/{closures[0]['id']}/cancel",
        headers=world.staff_a,
        json={"reason": "Done early"},
    )

    first = await events_of(db_session, group_id=closures[0]["id"])
    assert [(e.type, e.data) for e in first] == [
        (T.CLOSURE_CREATED, {"reason": "Resurfacing"}),
        (T.REOPENED, {"reason": "Done early"}),
    ]
    assert await types_of(db_session, group_id=closures[1]["id"]) == [T.CLOSURE_CREATED]


async def test_resolve_records_resolved(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    group = await insert_booking(
        db_session,
        court_id=world.a.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
    )
    group.needs_resolution = True
    await db_session.flush()

    await client.post(
        f"{ADMIN}/bookings/{group.id}/resolve",
        headers=world.staff_a,
        json={"resolution": "Refunded via Maya portal"},
    )

    [resolved] = await events_of(db_session, group_id=group.id)
    assert resolved.type == T.RESOLVED
    assert resolved.actor_user_id == world.staff_a_user.id
    assert resolved.data == {"resolution": "Refunded via Maya portal"}


async def test_failed_walk_in_records_nothing(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour()
    held = await hold(client, world.player, world.a.court.id, start)

    response = await walk_in(client, world.staff_a, world.a.court.id, start)

    assert response.status_code == 409
    assert await types_of(db_session, checkout_id=held["checkout_id"]) == [T.HOLD_PLACED]


async def test_failed_closure_records_nothing(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour(2)
    blocker = (await walk_in(client, world.staff_a, world.a.court2.id, start)).json()

    response = await close(
        client,
        world.staff_a,
        [world.a.court.id, world.a.court2.id],
        start,
        start + timedelta(hours=1),
    )

    assert response.status_code == 409
    assert await types_of(db_session, group_id=blocker["id"]) == [T.WALK_IN_BOOKED]
    closure_events = await db_session.execute(
        select(func.count())
        .select_from(ActionEventModel)
        .join(BookingGroupModel, BookingGroupModel.id == ActionEventModel.booking_group_id)
        .where(
            ActionEventModel.type == T.CLOSURE_CREATED,
            BookingGroupModel.court_id.in_([world.a.court.id, world.a.court2.id]),
        )
    )
    assert closure_events.scalar_one() == 0
