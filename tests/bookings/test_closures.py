from datetime import UTC, datetime, timedelta

from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingType
from src.bookings.persistence.models import BookingGroupModel
from src.utils.api import API_PREFIX
from tests.bookings.conftest import ADMIN, BOOKINGS, World, hold, manila_date
from tests.bookings.helpers import insert_booking, next_hour

CLOSURES = f"{ADMIN}/closures"


async def close(
    client: AsyncClient,
    headers: dict[str, str],
    court_ids: list[int],
    starts_at: datetime,
    ends_at: datetime,
    reason: str = "Resurfacing",
) -> Response:
    return await client.post(
        CLOSURES,
        headers=headers,
        json={
            "court_ids": court_ids,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": reason,
        },
    )


async def _closures(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count()).where(BookingGroupModel.type == BookingType.MAINTENANCE)
        )
    ).scalar_one()


async def test_closure_blocks_bookings_and_shows_closed(client: AsyncClient, world: World) -> None:
    start = next_hour(2)

    response = await close(
        client,
        world.staff_a,
        [world.a.court.id, world.a.court2.id],
        start,
        start + timedelta(hours=3),
    )
    booking = await client.post(
        BOOKINGS,
        headers=world.player,
        json={"court_id": world.a.court.id, "starts_at": (start + timedelta(hours=2)).isoformat()},
    )
    grid = await client.get(
        f"{API_PREFIX}/availability",
        headers=world.player,
        params={"venue_id": world.a.venue.id, "date": str(manila_date(start))},
    )

    assert response.status_code == 201
    groups = response.json()
    assert [g["court_id"] for g in groups] == [world.a.court.id, world.a.court2.id]
    assert all(g["type"] == "MAINTENANCE" and g["hours"] == 3 for g in groups)
    assert groups[0]["notes"] == "Resurfacing"
    assert groups[0]["total_centavos"] == 0
    assert booking.status_code == 409
    statuses = {
        s["status"]
        for c in grid.json()["courts"]
        for s in c["slots"]
        if start.isoformat().replace("+00:00", "Z")
        <= s["starts_at"]
        < (start + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    }
    assert statuses == {"CLOSED"}


async def test_closure_cannot_evict_a_booking(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """All or nothing: court2 is free, but court 1 has a hold inside the
    range — so nothing is written for EITHER court (spec §6.5)."""
    start = next_hour(2)
    await hold(client, world.player, world.a.court.id, start + timedelta(hours=1))

    response = await close(
        client,
        world.staff_a,
        [world.a.court2.id, world.a.court.id],
        start,
        start + timedelta(hours=3),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SLOT_UNAVAILABLE"
    assert await _closures(db_session) == 0


async def test_cancel_closure_reopens(client: AsyncClient, world: World) -> None:
    start = next_hour(2)
    closure = (
        await close(client, world.staff_a, [world.a.court.id], start, start + timedelta(hours=2))
    ).json()[0]

    missing_reason = await client.post(
        f"{CLOSURES}/{closure['id']}/cancel", headers=world.staff_a, json={}
    )
    reopened = await client.post(
        f"{CLOSURES}/{closure['id']}/cancel", headers=world.staff_a, json={"reason": "Done early"}
    )

    assert missing_reason.status_code == 422
    assert reopened.status_code == 200
    assert reopened.json()["booking_status"] == "CANCELLED"
    await hold(client, world.player, world.a.court.id, start + timedelta(hours=1))


async def test_closure_route_rejects_bookings(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    response = await client.post(
        f"{CLOSURES}/{booking['id']}/cancel", headers=world.staff_a, json={"reason": "x"}
    )

    assert response.status_code == 409


async def test_closure_other_venue_court_is_404(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour(2)

    response = await close(
        client,
        world.staff_a,
        [world.a.court.id, world.b.court.id],
        start,
        start + timedelta(hours=1),
    )

    assert response.status_code == 404
    assert await _closures(db_session) == 0


async def test_player_cannot_close(client: AsyncClient, world: World) -> None:
    start = next_hour(2)

    response = await close(
        client, world.player, [world.a.court.id], start, start + timedelta(hours=1)
    )

    assert response.status_code == 403


async def test_closure_range_limits(client: AsyncClient, world: World) -> None:
    start = next_hour(2)
    this_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    court = [world.a.court.id]

    too_long = await close(client, world.staff_a, court, start, start + timedelta(days=31, hours=1))
    past = await close(client, world.staff_a, court, this_hour - timedelta(hours=1), this_hour)
    backwards = await close(client, world.staff_a, court, start, start)
    off_hour = await close(client, world.staff_a, court, start, start + timedelta(minutes=90))
    now_ok = await close(client, world.staff_a, court, this_hour, this_hour + timedelta(hours=1))
    a_month = await close(
        client, world.staff_a, [world.a.court2.id], start, start + timedelta(days=31)
    )

    assert [r.status_code for r in (too_long, past, backwards, off_hour)] == [422] * 4
    assert now_ok.status_code == 201  # the current hour may be closed
    assert a_month.status_code == 201
    assert a_month.json()[0]["hours"] == 31 * 24


async def test_admin_list_ends_after_filters_ended_closures(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    kwargs = {"created_by": world.staff_a_user.id, "type": BookingType.MAINTENANCE}
    past = await insert_booking(
        db_session, court_id=world.a.court.id, starts_at=now - timedelta(hours=5), **kwargs
    )
    current = await insert_booking(
        db_session,
        court_id=world.a.court.id,
        starts_at=now - timedelta(hours=1),
        hours=3,
        **kwargs,
    )
    future = await insert_booking(
        db_session, court_id=world.a.court.id, starts_at=now + timedelta(days=2), **kwargs
    )
    other_venue = await insert_booking(
        db_session, court_id=world.b.court.id, starts_at=now + timedelta(days=2), **kwargs
    )

    response = await client.get(
        f"{API_PREFIX}/admin/bookings",
        headers=world.staff_a,
        params={"type": "MAINTENANCE", "ends_after": datetime.now(UTC).isoformat()},
    )
    unfiltered = await client.get(
        f"{API_PREFIX}/admin/bookings", headers=world.staff_a, params={"type": "MAINTENANCE"}
    )

    ids = [b["id"] for b in response.json()["data"]]
    assert response.status_code == 200
    assert ids == [current.id, future.id]
    assert past.id in [b["id"] for b in unfiltered.json()["data"]]
    assert other_venue.id not in ids
