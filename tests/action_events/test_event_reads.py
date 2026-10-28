from datetime import datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.payments.adapters.mock import MockProvider
from tests.bookings.conftest import ADMIN, CHECKOUTS, World, hold
from tests.bookings.helpers import insert_booking, next_hour
from tests.bookings.test_staff_ops import BOOKINGS, this_hour, walk_in
from tests.checkouts.helpers import lapse
from tests.payments.helpers import groups, pay


async def _staff(client: AsyncClient, world: World, booking_id: int) -> list[dict[str, Any]]:
    response = await client.get(f"{BOOKINGS}/{booking_id}/events", headers=world.staff_a)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["page"] == 1 and body["has_more"] is False
    return list(body["data"])


async def _customer(client: AsyncClient, world: World, checkout_id: int) -> list[dict[str, Any]]:
    response = await client.get(f"{CHECKOUTS}/{checkout_id}/events", headers=world.player)
    assert response.status_code == 200, response.text
    return list(response.json()["data"])


async def test_staff_timeline_newest_first_with_names(client: AsyncClient, world: World) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()
    await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "CASH"},
    )

    events = await _staff(client, world, booking["id"])

    assert [e["type"] for e in events] == ["MARKED_PAID", "WALK_IN_BOOKED"]
    staff = world.staff_a_user
    assert events[0]["actor"] == {"kind": "STAFF", "name": f"{staff.first_name} {staff.last_name}"}
    assert events[0]["data"] == {"method": "CASH", "amount_centavos": booking["total_centavos"]}
    assert events[0]["booking_id"] == booking["id"]
    assert events[0]["reference_code"] == booking["reference_code"]
    assert events[0]["court_name"] == world.a.court.name
    assert events[0]["derived"] is False and isinstance(events[0]["id"], int)


async def test_staff_timeline_includes_the_checkouts_payment_events(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await pay(client, db_session, provider, world.player, booking["checkout_id"])

    events = await _staff(client, world, booking["id"])

    assert [e["type"] for e in events] == [
        "CONFIRMED",
        "PAYMENT_SUCCEEDED",
        "PAYMENT_STARTED",
        "HOLD_PLACED",
    ]
    player = world.player_user
    assert events[-1]["actor"] == {
        "kind": "CUSTOMER",
        "name": f"{player.first_name} {player.last_name}",
    }
    assert events[1]["actor"] == {"kind": "SYSTEM", "name": None}
    assert events[1]["booking_id"] is None


async def test_derived_play_started_after_start(client: AsyncClient, world: World) -> None:
    start = this_hour() - timedelta(hours=1)
    booking = (await walk_in(client, world.staff_a, world.a.court.id, start, hours=2)).json()

    events = await _staff(client, world, booking["id"])

    # Booked an hour after play began, so newest first puts the booking on top.
    assert [e["type"] for e in events] == ["WALK_IN_BOOKED", "PLAY_STARTED"]
    started = events[1]
    assert (started["id"], started["derived"]) == (None, True)
    assert datetime.fromisoformat(started["occurred_at"]) == start


async def test_no_play_started_before_the_first_hour(client: AsyncClient, world: World) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()

    assert [e["type"] for e in await _staff(client, world, booking["id"])] == ["WALK_IN_BOOKED"]


async def test_derived_hold_ended_for_untouched_lapsed_hold(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await lapse(db_session, booking["checkout_id"], minutes=30)
    (group,) = await groups(db_session, booking["checkout_id"])

    events = await _customer(client, world, booking["checkout_id"])

    assert sorted(e["type"] for e in events) == ["HOLD_ENDED", "HOLD_PLACED"]
    ended = next(e for e in events if e["type"] == "HOLD_ENDED")
    assert ended["derived"] is True
    assert datetime.fromisoformat(ended["occurred_at"]) == group.expires_at


async def test_customer_view_hides_flag_and_resolve_and_staff_names(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await pay(client, db_session, provider, world.player, booking["checkout_id"])
    group = (await groups(db_session, booking["checkout_id"]))[0]
    group.needs_resolution = True
    await db_session.flush()
    await client.post(
        f"{ADMIN}/bookings/{group.id}/resolve",
        headers=world.staff_a,
        json={"resolution": "Internal note"},
    )
    await client.post(
        f"{BOOKINGS}/{group.id}/cancel", headers=world.staff_a, json={"reason": "Court flooded"}
    )

    events = await _customer(client, world, booking["checkout_id"])

    assert [e["type"] for e in events] == [
        "CANCELLED",
        "CONFIRMED",
        "PAYMENT_SUCCEEDED",
        "PAYMENT_STARTED",
        "HOLD_PLACED",
    ]
    assert events[0]["actor"] == {"kind": "STAFF", "name": "Venue staff"}
    assert events[0]["data"] == {"reason": "Court flooded"}
    assert events[0]["court_name"] == world.a.court.name
    assert events[-1]["actor"] == {"kind": "CUSTOMER", "name": "You"}
    assert "RESOLVED" in [e["type"] for e in await _staff(client, world, group.id)]


async def test_other_venue_staff_gets_404(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Review Focus 5."""
    theirs = await insert_booking(
        db_session,
        court_id=world.b.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
    )

    response = await client.get(f"{BOOKINGS}/{theirs.id}/events", headers=world.staff_a)

    assert response.status_code == 404


async def test_other_customer_gets_404(client: AsyncClient, world: World) -> None:
    """Review Focus 5."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    response = await client.get(
        f"{CHECKOUTS}/{booking['checkout_id']}/events", headers=world.player2
    )

    assert response.status_code == 404
