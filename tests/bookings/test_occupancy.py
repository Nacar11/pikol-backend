"""The two-week strip's per-day occupancy: Manila days, the PENDING trap,
and closures that shrink what is bookable rather than count as booked."""

from datetime import UTC, date, datetime, time, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, BookingType
from src.utils.time import MANILA
from tests.auth.helpers import RoleCode, bearer, login, make_user
from tests.bookings.conftest import ADMIN, World
from tests.bookings.helpers import insert_booking

OCCUPANCY = f"{ADMIN}/bookings/occupancy"


def manila(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=MANILA).astimezone(UTC)


def in_days(n: int) -> date:
    return datetime.now(MANILA).date() + timedelta(days=n)


async def strip(
    client: AsyncClient, headers: dict[str, str], first: date, days: int = 3, **params: object
) -> dict[date, tuple[int, int]]:
    response = await client.get(
        OCCUPANCY, headers=headers, params={"from": first.isoformat(), "days": days, **params}
    )
    assert response.status_code == 200, response.text
    return {
        date.fromisoformat(d["date"]): (d["booked_hours"], d["bookable_hours"])
        for d in response.json()
    }


async def test_counts_booked_hours_per_manila_day(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    day = in_days(2)
    await insert_booking(
        db_session,
        court_id=world.a.court.id,
        created_by=world.staff_a_user.id,
        starts_at=manila(day, 18),
        hours=2,
    )

    by_day = await strip(client, world.staff_a, day - timedelta(days=1))

    # Two active courts × 24 hours bookable each day.
    assert by_day == {
        day - timedelta(days=1): (0, 48),
        day: (2, 48),
        day + timedelta(days=1): (0, 48),
    }


async def test_midnight_slot_belongs_to_its_manila_day(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """12am–1am Manila is 4pm the previous day in UTC; `::date` would
    misplace it."""
    day = in_days(3)
    await insert_booking(
        db_session,
        court_id=world.a.court.id,
        created_by=world.staff_a_user.id,
        starts_at=manila(day, 0),
    )

    by_day = await strip(client, world.staff_a, day - timedelta(days=1), days=2)

    assert by_day[day][0] == 1
    assert by_day[day - timedelta(days=1)][0] == 0


async def test_lapsed_hold_is_not_booked(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    day = in_days(2)
    for hour, expires in ((9, -1), (10, +10)):  # one lapsed, one live
        await insert_booking(
            db_session,
            court_id=world.a.court.id,
            created_by=world.player_user.id,
            player_id=world.player_user.id,
            type=BookingType.PLAYER_BOOKING,
            status=BookingStatus.PENDING,
            starts_at=manila(day, hour),
            expires_at=datetime.now(UTC) + timedelta(minutes=expires),
        )

    by_day = await strip(client, world.staff_a, day, days=1)

    assert by_day[day][0] == 1


async def test_closure_shrinks_bookable_not_booked(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    day = in_days(2)
    await insert_booking(
        db_session,
        court_id=world.a.court2.id,
        created_by=world.staff_a_user.id,
        type=BookingType.MAINTENANCE,
        starts_at=manila(day, 6),
        hours=4,
    )

    by_day = await strip(client, world.staff_a, day, days=1)

    assert by_day[day] == (0, 44)


async def test_days_is_capped(client: AsyncClient, world: World) -> None:
    response = await client.get(
        OCCUPANCY, headers=world.staff_a, params={"from": in_days(0).isoformat(), "days": 32}
    )

    assert response.status_code == 422


async def test_another_venue_is_404(client: AsyncClient, world: World) -> None:
    response = await client.get(
        OCCUPANCY,
        headers=world.staff_a,
        params={"from": in_days(0).isoformat(), "venue_id": world.b.venue.id},
    )

    assert response.status_code == 404


async def test_owner_names_the_venue(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    headers = bearer(await login(client, admin.email))

    without = await client.get(OCCUPANCY, headers=headers, params={"from": in_days(0).isoformat()})
    by_day = await strip(client, headers, in_days(0), days=1, venue_id=world.b.venue.id)

    assert without.status_code == 422
    assert by_day[in_days(0)] == (0, 48)
