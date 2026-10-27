"""A freshly inserted booking must serialise without lazy-loading.

`lazy="joined"` applies only to SELECTs, so a group built in Python and
flushed by `BookingRepository.insert` has no `court` (or `court.venue`)
loaded. Touching it lazy-loads — IO outside the greenlet under asyncpg,
which is `MissingGreenlet` and a 500 on a real server.

The shared `world` fixture hides this: it holds strong references to the
CourtModel and VenueModel, so the session's weak identity map still has
them and the "lazy load" resolves with no IO. These tests keep only ids,
drop every reference, and empty the identity map before the request — the
state a production request starts from.
"""

import gc
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.helpers import RoleCode, bearer, login, make_court, make_user, make_venue
from tests.bookings.conftest import ADMIN, BOOKINGS
from tests.bookings.helpers import next_hour


async def _cold_court(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[int, dict[str, str], dict[str, str]]:
    """A court plus player and staff headers, with nothing left in the
    identity map that a lazy load could resolve against."""
    venue = await make_venue(db_session, "Cold")
    court = await make_court(db_session, venue.id, "Cold 1")
    player = await make_user(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    court_id = court.id
    player_headers = bearer(await login(client, player.email))
    staff_headers = bearer(await login(client, staff.email))
    del venue, court, player, staff
    gc.collect()
    db_session.expunge_all()
    return court_id, player_headers, staff_headers


async def test_hold_serialises_court_and_venue_with_a_cold_identity_map(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    court_id, player, _ = await _cold_court(client, db_session)

    response = await client.post(
        BOOKINGS,
        headers=player,
        json={"court_id": court_id, "starts_at": next_hour().isoformat(), "hours": 1},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["court_name"], body["venue_name"]) == ("Cold 1", "Cold")
    assert body["checkout_url"]


async def test_walk_in_serialises_court_and_venue_with_a_cold_identity_map(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    court_id, _, staff = await _cold_court(client, db_session)

    response = await client.post(
        f"{ADMIN}/bookings/walk-in",
        headers=staff,
        json={"court_id": court_id, "starts_at": next_hour().isoformat(), "hours": 1},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["court_name"], body["venue_name"]) == ("Cold 1", "Cold")


async def test_closure_serialises_court_and_venue_with_a_cold_identity_map(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    court_id, _, staff = await _cold_court(client, db_session)
    start = next_hour(2)

    response = await client.post(
        f"{ADMIN}/closures",
        headers=staff,
        json={
            "court_ids": [court_id],
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "reason": "Resurfacing",
        },
    )

    assert response.status_code == 201, response.text
    [body] = response.json()
    assert (body["court_name"], body["venue_name"]) == ("Cold 1", "Cold")
