from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingType
from src.bookings.persistence.models import BookingGroupModel
from src.pricing.persistence.models import PriceRuleModel
from src.utils.api import API_PREFIX
from src.utils.time import manila_day_bounds
from tests.bookings.conftest import World, hold, manila_date, set_parameter
from tests.bookings.helpers import insert_booking, next_hour

GRID = f"{API_PREFIX}/availability"


async def _grid(
    client: AsyncClient, world: World, day: object
) -> dict[str, dict[str, dict[str, object]]]:
    """{court_id: {starts_at: slot}}"""
    response = await client.get(
        GRID, headers=world.player, params={"venue_id": world.a.venue.id, "date": str(day)}
    )
    assert response.status_code == 200, response.text
    return {
        str(c["court_id"]): {s["starts_at"]: s for s in c["slots"]}
        for c in response.json()["courts"]
    }


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def test_grid_first_row_is_manila_midnight(client: AsyncClient, world: World) -> None:
    day = manila_date(next_hour(2))

    response = await client.get(
        GRID, headers=world.player, params={"venue_id": world.a.venue.id, "date": str(day)}
    )

    courts = response.json()["courts"]
    start, _ = manila_day_bounds(day)
    assert [c["court_id"] for c in courts] == [world.a.court.id, world.a.court2.id]
    assert len(courts[0]["slots"]) == 24
    assert courts[0]["slots"][0]["starts_at"] == _iso(start)  # 16:00Z the day before
    assert courts[0]["slots"][0]["hour"] == 0


async def test_grid_marks_booked_closed_and_hides_lapsed_holds(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour(2)
    live = await hold(client, world.player, world.a.court.id, start)
    lapsed = await hold(client, world.player, world.a.court.id, start + timedelta(hours=1))
    await db_session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == lapsed["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await insert_booking(
        db_session,
        court_id=world.a.court2.id,
        created_by=world.staff_a_user.id,
        starts_at=start,
        type=BookingType.MAINTENANCE,
    )

    grid = await _grid(client, world, manila_date(start))

    court, court2 = grid[str(world.a.court.id)], grid[str(world.a.court2.id)]
    assert live["id"]
    assert court[_iso(start)]["status"] == "BOOKED"
    assert court[_iso(start + timedelta(hours=1))]["status"] == "AVAILABLE"  # lapsed
    assert court2[_iso(start)]["status"] == "CLOSED"


async def test_grid_prices_resolve_per_cell(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    db_session.add(PriceRuleModel(court_id=world.a.court.id, hour_of_day=18, price_centavos=60000))
    await db_session.flush()

    grid = await _grid(client, world, manila_date(next_hour(2)))

    court = list(grid[str(world.a.court.id)].values())
    other = list(grid[str(world.a.court2.id)].values())
    assert [s["price_centavos"] for s in court if s["hour"] == 18] == [60000]
    assert {s["price_centavos"] for s in court if s["hour"] != 18} == {50000}
    assert {s["price_centavos"] for s in other} == {50000}


async def test_grid_marks_too_soon_as_past(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """A slot a player can no longer book must not render as bookable."""
    await set_parameter(db_session, "min_lead_minutes", "120")
    upcoming = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    grid = await _grid(client, world, manila_date(upcoming))

    assert grid[str(world.a.court.id)][_iso(upcoming)]["status"] == "PAST"


async def test_grid_does_not_reveal_who_booked(client: AsyncClient, world: World) -> None:
    start = next_hour(2)
    await hold(client, world.player, world.a.court.id, start)

    response = await client.get(
        GRID,
        headers=world.player2,
        params={"venue_id": world.a.venue.id, "date": str(manila_date(start))},
    )

    slot = next(s for s in response.json()["courts"][0]["slots"] if s["starts_at"] == _iso(start))
    assert set(slot) == {"starts_at", "hour", "status", "price_centavos"}


async def test_grid_hides_inactive_courts_and_404s_inactive_venue(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    world.a.court2.is_active = False
    await db_session.flush()
    day = str(manila_date(next_hour(2)))

    grid = await _grid(client, world, day)
    world.a.venue.is_active = False
    await db_session.flush()
    closed = await client.get(
        GRID, headers=world.player, params={"venue_id": world.a.venue.id, "date": day}
    )

    assert list(grid) == [str(world.a.court.id)]
    assert closed.status_code == 404


async def test_grid_requires_auth(client: AsyncClient, world: World) -> None:
    response = await client.get(GRID, params={"venue_id": world.a.venue.id, "date": "2030-01-01"})

    assert response.status_code == 401
