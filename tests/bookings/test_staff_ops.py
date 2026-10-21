from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingType
from src.utils.time import manila_day_bounds
from tests.bookings.conftest import ADMIN, World, hold, manila_date
from tests.bookings.helpers import insert_booking, next_hour

BOOKINGS = f"{ADMIN}/bookings"


async def walk_in(
    client: AsyncClient,
    headers: dict[str, str],
    court_id: int,
    starts_at: datetime,
    hours: int = 1,
    **extra: Any,
) -> Response:
    return await client.post(
        f"{BOOKINGS}/walk-in",
        headers=headers,
        json={"court_id": court_id, "starts_at": starts_at.isoformat(), "hours": hours, **extra},
    )


def this_hour() -> datetime:
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


async def test_walk_in_is_confirmed_unpaid_with_no_fee(client: AsyncClient, world: World) -> None:
    response = await walk_in(
        client, world.staff_a, world.a.court.id, next_hour(), hours=2, notes="Juan, 0917"
    )

    body = response.json()
    assert response.status_code == 201
    assert (body["type"], body["booking_status"], body["payment_status"]) == (
        "WALK_IN",
        "CONFIRMED",
        "UNPAID",
    )
    assert body["fee_centavos"] == 0
    assert body["total_centavos"] == body["price_centavos"] == 100000
    assert body["notes"] == "Juan, 0917"
    assert body["expires_at"] is None


async def test_walk_in_may_backdate_within_window(client: AsyncClient, world: World) -> None:
    ok = await walk_in(client, world.staff_a, world.a.court.id, this_hour() - timedelta(days=6))
    far = await walk_in(client, world.staff_a, world.a.court.id, this_hour() - timedelta(days=8))

    assert ok.status_code == 201
    assert far.status_code == 422
    assert far.json()["code"] == "TOO_FAR_BACK"


async def test_walk_in_collides_with_hold_is_409(client: AsyncClient, world: World) -> None:
    start = next_hour()
    await hold(client, world.player, world.a.court.id, start)

    response = await walk_in(client, world.staff_a, world.a.court.id, start)

    assert response.status_code == 409
    assert response.json()["code"] == "SLOT_UNAVAILABLE"


async def test_mark_paid_records_method(client: AsyncClient, world: World) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()

    response = await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "MANUAL_GCASH"},
    )
    again = await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "CASH"},
    )

    assert response.status_code == 200
    assert response.json()["payment_status"] == "PAID"
    assert response.json()["payment_method"] == "MANUAL_GCASH"
    assert again.status_code == 409


async def test_mark_paid_online_method_is_422(client: AsyncClient, world: World) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()

    response = await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "ONLINE"},
    )

    assert response.status_code == 422


async def test_mark_paid_player_booking_is_409(client: AsyncClient, world: World) -> None:
    """Online money only ever arrives through the provider — staff cannot
    mark a player's hold paid by hand."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    response = await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "CASH"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_A_WALK_IN"


async def test_outcome_before_start_is_409(client: AsyncClient, world: World) -> None:
    booking = (await walk_in(client, world.staff_a, world.a.court.id, next_hour())).json()

    response = await client.post(
        f"{BOOKINGS}/{booking['id']}/outcome", headers=world.staff_a, json={"outcome": "NO_SHOW"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_STARTED"


async def test_no_show_still_blocks_the_slot(client: AsyncClient, world: World) -> None:
    start = this_hour() - timedelta(days=1)
    booking = (await walk_in(client, world.staff_a, world.a.court.id, start)).json()

    outcome = await client.post(
        f"{BOOKINGS}/{booking['id']}/outcome", headers=world.staff_a, json={"outcome": "NO_SHOW"}
    )
    backdated = await walk_in(client, world.staff_a, world.a.court.id, start)

    assert outcome.status_code == 200
    assert outcome.json()["booking_status"] == "NO_SHOW"
    assert backdated.status_code == 409


async def test_paid_after_playing(client: AsyncClient, world: World) -> None:
    booking = (
        await walk_in(client, world.staff_a, world.a.court.id, this_hour() - timedelta(days=1))
    ).json()
    await client.post(
        f"{BOOKINGS}/{booking['id']}/outcome", headers=world.staff_a, json={"outcome": "COMPLETED"}
    )

    paid = await client.post(
        f"{BOOKINGS}/{booking['id']}/mark-paid",
        headers=world.staff_a,
        json={"payment_method": "CASH"},
    )

    assert paid.status_code == 200
    assert (paid.json()["booking_status"], paid.json()["payment_status"]) == ("COMPLETED", "PAID")


async def test_staff_cancel_requires_reason_and_releases(client: AsyncClient, world: World) -> None:
    start = next_hour()
    booking = (await walk_in(client, world.staff_a, world.a.court.id, start)).json()

    missing = await client.post(
        f"{BOOKINGS}/{booking['id']}/cancel", headers=world.staff_a, json={}
    )
    blank = await client.post(
        f"{BOOKINGS}/{booking['id']}/cancel", headers=world.staff_a, json={"reason": ""}
    )
    done = await client.post(
        f"{BOOKINGS}/{booking['id']}/cancel", headers=world.staff_a, json={"reason": "Double entry"}
    )

    assert missing.status_code == blank.status_code == 422
    assert done.status_code == 200
    assert done.json()["cancellation_reason"] == "Double entry"
    assert (await walk_in(client, world.staff_a, world.a.court.id, start)).status_code == 201


async def test_staff_cannot_cancel_closure_via_booking_route(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    closure = await insert_booking(
        db_session,
        court_id=world.a.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
        type=BookingType.MAINTENANCE,
    )

    response = await client.post(
        f"{BOOKINGS}/{closure.id}/cancel", headers=world.staff_a, json={"reason": "x"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "IS_CLOSURE"


async def test_cross_venue_staff_gets_404(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    theirs = await insert_booking(
        db_session,
        court_id=world.b.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
    )

    responses = [
        await client.get(f"{BOOKINGS}/{theirs.id}", headers=world.staff_a),
        await client.post(
            f"{BOOKINGS}/{theirs.id}/cancel", headers=world.staff_a, json={"reason": "x"}
        ),
        await client.post(
            f"{BOOKINGS}/{theirs.id}/mark-paid",
            headers=world.staff_a,
            json={"payment_method": "CASH"},
        ),
        await client.post(
            f"{BOOKINGS}/{theirs.id}/outcome", headers=world.staff_a, json={"outcome": "COMPLETED"}
        ),
        await walk_in(client, world.staff_a, world.b.court.id, next_hour(2)),
    ]

    assert [r.status_code for r in responses] == [404] * 5


@pytest.mark.parametrize("path", ["", "/walk-in"])
async def test_player_cannot_reach_admin_bookings(
    client: AsyncClient, world: World, path: str
) -> None:
    if path:
        response = await walk_in(client, world.player, world.a.court.id, next_hour())
    else:
        response = await client.get(BOOKINGS, headers=world.player)

    assert response.status_code == 403


async def test_admin_list_filters_by_manila_date_and_shows_player(
    client: AsyncClient, world: World
) -> None:
    """The 12am Manila slot (16:00Z the day before) belongs to its Manila date."""
    day = manila_date(next_hour(3))
    midnight, _ = manila_day_bounds(day)
    on_day = (await walk_in(client, world.staff_a, world.a.court.id, midnight)).json()
    before = (
        await walk_in(client, world.staff_a, world.a.court.id, midnight - timedelta(hours=1))
    ).json()
    held = await hold(client, world.player, world.a.court2.id, midnight + timedelta(hours=10))

    response = await client.get(
        BOOKINGS, headers=world.staff_a, params={"date": str(day), "limit": 100}
    )

    rows = {b["id"]: b for b in response.json()["data"]}
    assert on_day["id"] in rows and held["id"] in rows
    assert before["id"] not in rows
    assert rows[held["id"]]["player"]["id"] == world.player_user.id
    assert rows[on_day["id"]]["player"] is None
    assert list(rows) == sorted(rows, key=lambda i: rows[i]["starts_at"])


async def test_admin_list_is_venue_scoped(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    theirs = await insert_booking(
        db_session,
        court_id=world.b.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
    )

    response = await client.get(BOOKINGS, headers=world.staff_a, params={"limit": 100})

    assert theirs.id not in {b["id"] for b in response.json()["data"]}
