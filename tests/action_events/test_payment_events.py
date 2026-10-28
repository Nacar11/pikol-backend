from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType as T
from src.action_events.domain.enums import ActorKind
from src.action_events.persistence.models import ActionEventModel
from src.payments.adapters.mock import MockProvider
from tests.action_events.helpers import events_of, types_of
from tests.bookings.conftest import CHECKOUTS, World, hold
from tests.bookings.helpers import next_hour
from tests.bookings.test_staff_ops import walk_in
from tests.checkouts.helpers import action, item, lapse
from tests.payments.helpers import WEBHOOK, groups, open_maya, pay


async def _two_courts(client: AsyncClient, world: World) -> int:
    start = next_hour()
    response = await client.post(
        CHECKOUTS,
        headers=world.player,
        json={"items": [item(world.a.court.id, start, 2), item(world.a.court2.id, start)]},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def test_hold_records_hold_placed_per_group(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    checkout_id = await _two_courts(client, world)

    events = await events_of(db_session, checkout_id=checkout_id)
    by_group = {e.booking_group_id: e for e in events}
    assert [e.type for e in events] == [T.HOLD_PLACED, T.HOLD_PLACED]
    for g in await groups(db_session, checkout_id):
        event = by_group[g.id]
        assert (event.actor_kind, event.actor_user_id) == (
            ActorKind.CUSTOMER,
            world.player_user.id,
        )
        assert event.data == {
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "total_centavos": g.total_centavos,
        }


async def test_two_court_payment_records_one_payment_event_and_two_confirmed(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Review Focus 1."""
    checkout_id = await _two_courts(client, world)

    await pay(client, db_session, provider, world.player, checkout_id)

    events = await events_of(db_session, checkout_id=checkout_id)
    payment = [e for e in events if e.booking_group_id is None]
    assert [e.type for e in payment] == [T.PAYMENT_STARTED, T.PAYMENT_SUCCEEDED]
    assert payment[0].actor_kind == ActorKind.CUSTOMER
    assert payment[1].actor_kind == ActorKind.SYSTEM
    assert payment[1].data["method"] == "ONLINE"
    confirmed = [e for e in events if e.type == T.CONFIRMED]
    assert {e.booking_group_id for e in confirmed} == {
        g.id for g in await groups(db_session, checkout_id)
    }


async def test_failed_payment_records_failed_then_cancelled(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await pay(client, db_session, provider, world.player, booking["checkout_id"], "PAYMENT_FAILED")

    events = await events_of(db_session, checkout_id=booking["checkout_id"])
    assert [e.type for e in events] == [
        T.HOLD_PLACED,
        T.PAYMENT_STARTED,
        T.PAYMENT_FAILED,
        T.CANCELLED,
    ]
    assert events[-1].data == {"reason": "Payment failed"}
    assert events[-1].actor_kind == ActorKind.SYSTEM


async def test_backed_out_records_payment_cancelled(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await pay(
        client, db_session, provider, world.player, booking["checkout_id"], "PAYMENT_CANCELLED"
    )

    assert await types_of(db_session, checkout_id=booking["checkout_id"]) == [
        T.HOLD_PLACED,
        T.PAYMENT_STARTED,
        T.PAYMENT_CANCELLED,
        T.CANCELLED,
    ]


async def test_customer_cancel_records_cancelled_by_customer(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await action(client, world.player, booking["checkout_id"], "cancel")

    *_, cancelled = await events_of(db_session, group_id=booking["id"])
    assert cancelled.type == T.CANCELLED
    assert cancelled.actor_kind == ActorKind.CUSTOMER
    assert cancelled.data == {"reason": "Cancelled by customer"}


async def test_lazy_expiry_is_dated_at_the_deadline(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Review Focus 2: cleaned up two hours late, dated at the deadline."""
    start = next_hour()
    booking = await hold(client, world.player, world.a.court.id, start)
    await lapse(db_session, booking["checkout_id"], minutes=120)
    (group,) = await groups(db_session, booking["checkout_id"])
    deadline = group.expires_at

    taker = await walk_in(client, world.staff_a, world.a.court.id, start)

    assert taker.status_code == 201, taker.text
    *_, expired = await events_of(db_session, group_id=booking["id"])
    assert expired.type == T.EXPIRED
    assert expired.actor_kind == ActorKind.SYSTEM
    assert expired.occurred_at == deadline


async def test_collision_on_create_records_nothing(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Review Focus 3: the savepoint rolls back the first court's hold and
    its event with it."""
    start = next_hour()
    await walk_in(client, world.staff_a, world.a.court2.id, start)

    response = await client.post(
        CHECKOUTS,
        headers=world.player,
        json={"items": [item(world.a.court.id, start), item(world.a.court2.id, start)]},
    )

    assert response.status_code == 409
    holds = await db_session.execute(
        select(func.count())
        .select_from(ActionEventModel)
        .where(
            ActionEventModel.type == T.HOLD_PLACED,
            ActionEventModel.actor_user_id == world.player_user.id,
        )
    )
    assert holds.scalar_one() == 0


async def test_replayed_webhook_records_once(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Review Focus 4."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    provider.settle(payment_id, "PAYMENT_SUCCESS")

    await client.post(WEBHOOK, content=provider.webhook_body(payment_id))
    await client.post(WEBHOOK, content=provider.webhook_body(payment_id))

    assert await types_of(db_session, checkout_id=booking["checkout_id"]) == [
        T.HOLD_PLACED,
        T.PAYMENT_STARTED,
        T.PAYMENT_SUCCEEDED,
        T.CONFIRMED,
    ]


async def test_opening_maya_twice_records_one_payment_started(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await open_maya(client, world.player, booking["checkout_id"])
    await open_maya(client, world.player, booking["checkout_id"])

    assert await types_of(db_session, checkout_id=booking["checkout_id"]) == [
        T.HOLD_PLACED,
        T.PAYMENT_STARTED,
    ]


async def test_late_payment_with_free_hours_is_confirmed_late(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await open_maya(client, world.player, booking["checkout_id"])
    await lapse(db_session, booking["checkout_id"])

    await pay(client, db_session, provider, world.player, booking["checkout_id"])

    events = await events_of(db_session, checkout_id=booking["checkout_id"])
    succeeded = next(e for e in events if e.type == T.PAYMENT_SUCCEEDED)
    confirmed = next(e for e in events if e.type == T.CONFIRMED)
    assert succeeded.data["late"] is True
    assert confirmed.data == {"late": True}


async def test_late_payment_taken_hour_records_flagged_then_voided(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    start = next_hour()
    late = await hold(client, world.player, world.a.court.id, start)
    await open_maya(client, world.player, late["checkout_id"])
    await lapse(db_session, late["checkout_id"], minutes=30)
    await hold(client, world.player2, world.a.court.id, start)

    await pay(client, db_session, provider, world.player, late["checkout_id"])

    types = await types_of(db_session, checkout_id=late["checkout_id"])
    assert types == [
        T.HOLD_PLACED,
        T.PAYMENT_STARTED,
        T.EXPIRED,
        T.PAYMENT_SUCCEEDED,
        T.FLAGGED,
        T.VOIDED,
    ]
    voided = (await events_of(db_session, checkout_id=late["checkout_id"]))[-1]
    assert voided.booking_group_id is None
    assert voided.data["amount_centavos"] == late["checkout"]["total_centavos"]
    assert voided.data["reference"]
