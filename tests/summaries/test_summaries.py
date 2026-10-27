from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, BookingType
from src.utils.api import API_PREFIX
from src.utils.time import MANILA, manila_day_bounds
from tests.auth.helpers import bearer, login, make_court, make_user
from tests.bookings.helpers import insert_booking
from tests.venues.conftest import World

ADMIN = f"{API_PREFIX}/admin"


def today_start() -> datetime:
    return manila_day_bounds(datetime.now(MANILA).date())[0]


async def courts_summary(client: AsyncClient, venue_id: int, headers: dict[str, str]):
    return await client.get(f"{ADMIN}/venues/{venue_id}/courts/summary", headers=headers)


async def test_courts_summary_rates_and_hours(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    busy = await make_court(db_session, world.venue_a.id)
    idle = await make_court(db_session, world.venue_a.id)
    hour = datetime.now(MANILA).hour
    rule = await client.post(
        f"{ADMIN}/courts/{busy.id}/price-rules",
        headers=world.manager_a,
        json={"hour_of_day": hour, "price_centavos": 70000},
    )
    assert rule.status_code == 201
    await insert_booking(
        db_session,
        court_id=busy.id,
        created_by=world.manager_a_user.id,
        starts_at=today_start() + timedelta(hours=2),
    )

    response = await courts_summary(client, world.venue_a.id, world.manager_a)

    assert response.status_code == 200
    by_court = {row["court_id"]: row for row in response.json()}
    assert by_court[busy.id] == {"court_id": busy.id, "rate_centavos": 70000, "booked_hours": 1}
    assert by_court[idle.id]["booked_hours"] == 0
    assert by_court[idle.id]["rate_centavos"] == 50000  # the seeded default price


async def test_courts_summary_hides_what_the_caller_cannot_see(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_court(db_session, world.venue_a.id)
    courts_only = await make_user(db_session)
    group = await client.post(
        f"{ADMIN}/user-groups", headers=world.manager_a, json={"name": "Courts only"}
    )
    group_id = group.json()["id"]
    await client.put(
        f"{ADMIN}/user-groups/{group_id}/permissions",
        headers=world.manager_a,
        json={"codes": ["COURT:Manage"]},
    )
    await client.post(
        f"{ADMIN}/user-groups/{group_id}/members",
        headers=world.manager_a,
        json={"email": courts_only.email},
    )

    response = await courts_summary(
        client, world.venue_a.id, bearer(await login(client, courts_only.email))
    )

    assert response.status_code == 200
    assert response.json()[0]["rate_centavos"] is None
    assert response.json()[0]["booked_hours"] is None


async def test_booked_hours_ignore_lapsed_holds_and_closures(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    court = await make_court(db_session, world.venue_a.id)
    player = await make_user(db_session)
    await insert_booking(
        db_session,
        court_id=court.id,
        created_by=player.id,
        player_id=player.id,
        type=BookingType.PLAYER_BOOKING,
        status=BookingStatus.PENDING,
        starts_at=today_start() + timedelta(hours=20),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await insert_booking(
        db_session,
        court_id=court.id,
        created_by=world.manager_a_user.id,
        type=BookingType.MAINTENANCE,
        starts_at=today_start() + timedelta(hours=21),
    )

    response = await courts_summary(client, world.venue_a.id, world.manager_a)

    assert response.json()[0]["booked_hours"] == 0


async def test_booked_hours_count_the_midnight_slot_on_its_manila_day(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    court = await make_court(db_session, world.venue_a.id)
    await insert_booking(
        db_session, court_id=court.id, created_by=world.manager_a_user.id, starts_at=today_start()
    )

    response = await courts_summary(client, world.venue_a.id, world.manager_a)

    assert response.json()[0]["booked_hours"] == 1


async def test_courts_summary_other_venue_is_404(client: AsyncClient, world: World) -> None:
    response = await courts_summary(client, world.venue_b.id, world.manager_a)

    assert response.status_code == 404


async def test_venues_summary_is_owner_only(client: AsyncClient, world: World) -> None:
    response = await client.get(f"{ADMIN}/venues/summary", headers=world.manager_a)

    assert response.status_code == 403


async def test_venues_summary_counts_active_courts_and_hours(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    court = await make_court(db_session, world.venue_a.id)
    await make_court(db_session, world.venue_a.id)
    retired = await make_court(db_session, world.venue_a.id)
    retired.is_active = False
    await insert_booking(
        db_session,
        court_id=court.id,
        created_by=world.manager_a_user.id,
        starts_at=today_start() + timedelta(hours=3),
        hours=2,
    )

    response = await client.get(f"{ADMIN}/venues/summary", headers=world.admin)

    assert response.status_code == 200
    by_venue = {row["venue_id"]: row for row in response.json()}
    assert by_venue[world.venue_a.id] == {
        "venue_id": world.venue_a.id,
        "active_courts": 2,
        "booked_hours": 2,
    }
    assert by_venue[world.venue_b.id] == {
        "venue_id": world.venue_b.id,
        "active_courts": 0,
        "booked_hours": 0,
    }
