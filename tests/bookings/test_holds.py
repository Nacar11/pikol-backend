from datetime import UTC, datetime, timedelta

from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus
from src.bookings.persistence.models import BookingGroupModel
from src.pricing.persistence.models import PriceRuleModel
from tests.bookings.conftest import BOOKINGS, World, hold
from tests.bookings.helpers import next_hour


async def _lapse(session: AsyncSession, booking_id: object) -> None:
    await session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == booking_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )


async def _post(client: AsyncClient, world: World, starts_at: datetime) -> Response:
    body = {"court_id": world.a.court.id, "starts_at": starts_at.isoformat(), "hours": 1}
    return await client.post(BOOKINGS, headers=world.player, json=body)


async def test_hold_freezes_prices_and_fee(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour()
    db_session.add(PriceRuleModel(court_id=world.a.court.id, price_centavos=70000))
    db_session.add(
        PriceRuleModel(
            court_id=world.a.court.id,
            hour_of_day=(start + timedelta(hours=9)).hour,  # the 2nd slot, Manila hour
            price_centavos=90000,
        )
    )
    await db_session.flush()

    body = await hold(client, world.player, world.a.court.id, start, hours=2)

    assert [s["price_centavos"] for s in body["slots"]] == [70000, 90000]  # type: ignore[index]
    assert body["price_centavos"] == 160000
    assert body["fee_centavos"] == 4800  # 3.00%
    assert body["total_centavos"] == 164800
    assert body["booking_status"] == body["display_status"] == "PENDING"
    assert body["payment_status"] == "UNPAID"
    expires = datetime.fromisoformat(str(body["expires_at"]))
    assert abs(expires - (datetime.now(UTC) + timedelta(minutes=15))) < timedelta(seconds=10)
    assert str(body["reference_code"]).startswith("PKL-")


async def test_second_hold_on_same_slot_is_409(client: AsyncClient, world: World) -> None:
    start = next_hour()
    await hold(client, world.player, world.a.court.id, start)

    response = await client.post(
        BOOKINGS,
        headers=world.player2,
        json={"court_id": world.a.court.id, "starts_at": start.isoformat()},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SLOT_UNAVAILABLE"


async def test_overlapping_hold_is_409(client: AsyncClient, world: World) -> None:
    start = next_hour()
    await hold(client, world.player, world.a.court.id, start + timedelta(hours=1))

    response = await client.post(
        BOOKINGS,
        headers=world.player2,
        json={"court_id": world.a.court.id, "starts_at": start.isoformat(), "hours": 2},
    )

    assert response.status_code == 409


async def test_same_hour_other_court_is_fine(client: AsyncClient, world: World) -> None:
    start = next_hour()
    await hold(client, world.player, world.a.court.id, start)
    await hold(client, world.player2, world.a.court2.id, start)


async def test_lapsed_hold_is_expired_when_its_slot_is_taken(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour()
    old = await hold(client, world.player, world.a.court.id, start, hours=2)
    await _lapse(db_session, old["id"])

    await hold(client, world.player2, world.a.court.id, start + timedelta(hours=1))

    group = (
        await db_session.execute(select(BookingGroupModel).where(BookingGroupModel.id == old["id"]))
    ).scalar_one()
    await db_session.refresh(group)
    for slot in group.slots:
        await db_session.refresh(slot)
    assert group.booking_status == BookingStatus.EXPIRED
    assert {s.booking_status for s in group.slots} == {BookingStatus.EXPIRED}


async def test_lapsed_holds_do_not_count_toward_cap(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Abandoning checkout is the COMMON case. Counting lapsed holds would
    lock this player out of booking forever (spec §4.3)."""
    for day in (1, 2, 3):
        await _lapse(
            db_session, (await hold(client, world.player, world.a.court.id, next_hour(day)))["id"]
        )

    await hold(client, world.player, world.a.court.id, next_hour(4))


async def test_live_holds_hit_the_cap(client: AsyncClient, world: World) -> None:
    for day in (1, 2, 3):
        await hold(client, world.player, world.a.court.id, next_hour(day))

    response = await _post(client, world, next_hour(4))

    assert response.status_code == 409
    assert response.json()["code"] == "HOLD_LIMIT"


async def test_too_soon_and_beyond_horizon_are_422(client: AsyncClient, world: World) -> None:
    this_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    soon = await _post(client, world, this_hour)
    far = await _post(client, world, this_hour + timedelta(days=31))

    assert soon.status_code == far.status_code == 422
    assert soon.json()["code"] == "TOO_SOON"
    assert far.json()["code"] == "BEYOND_HORIZON"


async def test_not_on_the_hour_is_422(client: AsyncClient, world: World) -> None:
    response = await _post(client, world, next_hour() + timedelta(minutes=30))

    assert response.status_code == 422
    assert response.json()["code"] == "NOT_ON_THE_HOUR"


async def test_naive_start_is_422(client: AsyncClient, world: World) -> None:
    response = await client.post(
        BOOKINGS,
        headers=world.player,
        json={"court_id": world.a.court.id, "starts_at": "2030-01-01T10:00:00"},
    )

    assert response.status_code == 422


async def test_inactive_court_is_404(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    world.a.court.is_active = False
    await db_session.flush()

    assert (await _post(client, world, next_hour())).status_code == 404


async def test_staff_cannot_place_player_hold(client: AsyncClient, world: World) -> None:
    response = await client.post(
        BOOKINGS,
        headers=world.staff_a,
        json={"court_id": world.a.court.id, "starts_at": next_hour().isoformat()},
    )

    assert response.status_code == 403
