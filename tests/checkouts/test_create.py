from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus
from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.persistence.models import CheckoutModel
from src.pricing.persistence.models import PriceRuleModel
from src.utils.api import API_PREFIX
from src.utils.time import MANILA
from tests.bookings.conftest import CHECKOUTS, World, hold, items, manila_date, set_parameter
from tests.bookings.helpers import next_hour

AVAILABILITY = f"{API_PREFIX}/availability"


def _item(court_id: int, starts_at: datetime, hours: int = 1) -> dict[str, Any]:
    return {"court_id": court_id, "starts_at": starts_at.isoformat(), "hours": hours}


async def _create(client: AsyncClient, headers: dict[str, str], *its: dict[str, Any]) -> Response:
    return await client.post(CHECKOUTS, headers=headers, json={"items": list(its)})


async def _lapse(session: AsyncSession, checkout_id: int) -> None:
    past = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(
        update(CheckoutModel).where(CheckoutModel.id == checkout_id).values(expires_at=past)
    )
    await session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.checkout_id == checkout_id)
        .values(expires_at=past)
    )


async def _cell(client: AsyncClient, world: World, court_id: int, starts_at: datetime) -> str:
    response = await client.get(
        AVAILABILITY,
        headers=world.player,
        params={"venue_id": world.a.venue.id, "date": manila_date(starts_at).isoformat()},
    )
    court = next(c for c in response.json()["courts"] if c["court_id"] == court_id)
    slot = next(s for s in court["slots"] if datetime.fromisoformat(s["starts_at"]) == starts_at)
    return str(slot["status"])


async def test_one_payment_covers_two_courts_on_two_days(client: AsyncClient, world: World) -> None:
    response = await _create(
        client,
        world.player,
        _item(world.a.court.id, next_hour(1), 2),
        _item(world.a.court2.id, next_hour(2, hour=10), 1),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["venue_id"] == world.a.venue.id
    assert str(body["reference_code"]).startswith("PKL-")
    assert [b["hours"] for b in body["bookings"]] == [2, 1]
    assert {b["booking_status"] for b in body["bookings"]} == {"PENDING"}
    assert body["price_centavos"] == sum(b["price_centavos"] for b in body["bookings"])
    assert body["total_centavos"] == body["price_centavos"] + body["fee_centavos"]
    assert body["fee_centavos"] == 4500  # 3.00% of ₱1,500
    assert body["checkout_url"] is None  # Maya is opened by /pay, not here


async def test_bookings_share_the_checkouts_hold(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    body = (
        await _create(
            client,
            world.player,
            _item(world.a.court.id, next_hour(1), 1),
            _item(world.a.court2.id, next_hour(1), 1),
        )
    ).json()

    checkout = await db_session.get(CheckoutModel, body["id"])
    assert checkout is not None
    groups = (
        (
            await db_session.execute(
                select(BookingGroupModel).where(BookingGroupModel.checkout_id == body["id"])
            )
        )
        .scalars()
        .all()
    )
    assert {g.expires_at for g in groups} == {checkout.expires_at}
    assert sum(g.fee_centavos for g in groups) == checkout.fee_centavos


async def test_hold_lasts_hold_duration_minutes(client: AsyncClient, world: World) -> None:
    body = (await _create(client, world.player, _item(world.a.court.id, next_hour()))).json()

    expires = datetime.fromisoformat(body["expires_at"])
    assert abs(expires - (datetime.now(UTC) + timedelta(minutes=5))) < timedelta(seconds=10)


async def test_hold_freezes_each_hours_price(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour()
    db_session.add(PriceRuleModel(court_id=world.a.court.id, price_centavos=70000))
    db_session.add(
        PriceRuleModel(
            court_id=world.a.court.id,
            hour_of_day=(start + timedelta(hours=1)).astimezone(MANILA).hour,
            price_centavos=90000,
        )
    )
    await db_session.flush()

    booking = await hold(client, world.player, world.a.court.id, start, hours=2)

    assert booking["price_centavos"] == 160000
    assert booking["checkout"]["fee_centavos"] == 4800
    assert booking["checkout"]["total_centavos"] == 164800


async def test_two_venues_in_one_payment_is_refused(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Scenario 16: one venue per payment."""
    response = await _create(
        client,
        world.player,
        _item(world.a.court.id, next_hour()),
        _item(world.b.court.id, next_hour()),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "MIXED_VENUES"
    mine = select(func.count()).where(CheckoutModel.customer_id == world.player_user.id)
    assert (await db_session.execute(mine)).scalar() == 0


async def test_second_customer_is_told_the_hour_is_taken(client: AsyncClient, world: World) -> None:
    """Scenario 2: the first to press Pay now gets the hour; the second never
    reaches Maya and holds nothing, not even the hour that was free."""
    taken, free = next_hour(1, hour=10), next_hour(1, hour=13)
    await hold(client, world.player, world.a.court.id, taken)

    response = await _create(
        client,
        world.player2,
        _item(world.a.court.id, taken),
        _item(world.a.court2.id, free),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SLOT_UNAVAILABLE"
    detail = response.json()["detail"]
    assert world.a.court.name in detail
    assert taken.astimezone(MANILA).strftime("%-I %p") in detail
    assert world.a.court2.name not in detail
    assert await _cell(client, world, world.a.court2.id, free) == "AVAILABLE"


async def test_overlapping_hold_is_409(client: AsyncClient, world: World) -> None:
    start = next_hour()
    await hold(client, world.player, world.a.court.id, start + timedelta(hours=1))

    response = await _create(client, world.player2, _item(world.a.court.id, start, 2))

    assert response.status_code == 409


async def test_same_hour_on_two_courts_in_one_checkout_is_fine(
    client: AsyncClient, world: World
) -> None:
    start = next_hour()
    response = await _create(
        client, world.player, _item(world.a.court.id, start), _item(world.a.court2.id, start)
    )
    assert response.status_code == 201


async def test_picking_one_hour_twice_is_422(client: AsyncClient, world: World) -> None:
    start = next_hour()
    response = await _create(
        client, world.player, _item(world.a.court.id, start, 2), _item(world.a.court.id, start)
    )
    assert (response.status_code, response.json()["code"]) == (422, "OVERLAPPING_ITEMS")


async def test_lapsed_hold_is_expired_when_its_hour_is_taken(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour()
    old = await hold(client, world.player, world.a.court.id, start, hours=2)
    await _lapse(db_session, old["checkout_id"])

    await hold(client, world.player2, world.a.court.id, start + timedelta(hours=1))

    group = await db_session.get(BookingGroupModel, old["id"], populate_existing=True)
    assert group is not None
    for slot in group.slots:
        await db_session.refresh(slot)
    assert group.booking_status == BookingStatus.EXPIRED
    assert {s.booking_status for s in group.slots} == {BookingStatus.EXPIRED}


async def test_hold_cap_counts_checkouts_not_courts(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await set_parameter(db_session, "max_active_holds_per_player", "1")
    first = await _create(
        client,
        world.player,
        _item(world.a.court.id, next_hour(1)),
        _item(world.a.court2.id, next_hour(1)),
    )
    second = await _create(client, world.player, _item(world.a.court.id, next_hour(2)))

    assert first.status_code == 201
    assert (second.status_code, second.json()["code"]) == (409, "HOLD_LIMIT")


async def test_lapsed_checkouts_do_not_count_toward_the_cap(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Abandoning checkout is the COMMON case; counting lapsed holds would
    lock this customer out for good (the PENDING trap)."""
    await set_parameter(db_session, "max_active_holds_per_player", "1")
    old = await hold(client, world.player, world.a.court.id, next_hour(1))
    await _lapse(db_session, old["checkout_id"])

    await hold(client, world.player, world.a.court.id, next_hour(2))


async def test_zero_lead_books_the_next_hour(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await set_parameter(db_session, "min_lead_minutes", "0")
    this_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    upcoming = await _create(
        client, world.player, _item(world.a.court.id, this_hour + timedelta(hours=1))
    )
    started = await _create(client, world.player, _item(world.a.court2.id, this_hour))

    assert upcoming.status_code == 201
    assert (started.status_code, started.json()["code"]) == (422, "TOO_SOON")


async def test_beyond_the_horizon_is_422(client: AsyncClient, world: World) -> None:
    far = next_hour(15)  # booking_horizon_days is 14
    response = await _create(client, world.player, _item(world.a.court.id, far))
    assert (response.status_code, response.json()["code"]) == (422, "BEYOND_HORIZON")


async def test_midnight_hour_is_priced_on_its_manila_day(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Scenario 17: 12-1 AM belongs to the Manila day it starts on."""
    today = datetime.now(UTC).astimezone(MANILA).date()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    midnight = datetime(saturday.year, saturday.month, saturday.day, tzinfo=MANILA)
    db_session.add(
        PriceRuleModel(
            court_id=world.a.court.id,
            day_of_week=6,
            hour_of_day=0,
            price_centavos=33300,  # 6 = Saturday
        )
    )
    await db_session.flush()

    booking = await hold(client, world.player, world.a.court.id, midnight)

    assert booking["price_centavos"] == 33300
    assert await _cell(client, world, world.a.court.id, midnight.astimezone(UTC)) == "BOOKED"


async def test_not_on_the_hour_and_naive_starts_are_422(client: AsyncClient, world: World) -> None:
    half = await _create(
        client, world.player, _item(world.a.court.id, next_hour() + timedelta(minutes=30))
    )
    naive = await client.post(
        CHECKOUTS,
        headers=world.player,
        json={"items": [{"court_id": world.a.court.id, "starts_at": "2030-01-01T10:00:00"}]},
    )

    assert (half.status_code, half.json()["code"]) == (422, "NOT_ON_THE_HOUR")
    assert naive.status_code == 422


async def test_inactive_court_is_404(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    world.a.court.is_active = False
    await db_session.flush()

    assert (
        await _create(client, world.player, _item(world.a.court.id, next_hour()))
    ).status_code == 404


async def test_staff_who_are_not_customers_cannot_check_out(
    client: AsyncClient, world: World
) -> None:
    response = await client.post(
        CHECKOUTS, headers=world.staff_a, json=items(world.a.court.id, next_hour())
    )
    assert response.status_code == 403


async def test_someone_elses_checkout_is_404(client: AsyncClient, world: World) -> None:
    mine = await hold(client, world.player, world.a.court.id, next_hour())

    theirs = await client.get(f"{CHECKOUTS}/{mine['checkout_id']}", headers=world.player2)
    own = await client.get(f"{CHECKOUTS}/{mine['checkout_id']}", headers=world.player)

    assert theirs.status_code == 404
    assert own.status_code == 200
    assert own.json()["bookings"][0]["court_name"] == world.a.court.name
    assert own.json()["venue_name"] == world.a.venue.name


async def test_list_is_mine_newest_first_and_paginated(client: AsyncClient, world: World) -> None:
    first = await hold(client, world.player, world.a.court.id, next_hour(1))
    second = await hold(client, world.player, world.a.court.id, next_hour(2))
    await hold(client, world.player2, world.a.court.id, next_hour(3))

    response = await client.get(CHECKOUTS, headers=world.player, params={"limit": 1})

    body = response.json()
    assert set(body) == {"data", "total", "page", "limit", "has_more"}
    assert (body["total"], body["has_more"]) == (2, True)
    assert body["data"][0]["id"] == second["checkout_id"]
    page2 = (
        await client.get(CHECKOUTS, headers=world.player, params={"page": 2, "limit": 1})
    ).json()
    assert page2["data"][0]["id"] == first["checkout_id"]


async def test_a_lapsed_hold_reads_cancelled(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    mine = await hold(client, world.player, world.a.court.id, next_hour())
    await _lapse(db_session, mine["checkout_id"])

    body = (await client.get(f"{CHECKOUTS}/{mine['checkout_id']}", headers=world.player)).json()

    assert body["status"] == "CANCELLED"


async def test_availability_reports_the_fee_percent(client: AsyncClient, world: World) -> None:
    response = await client.get(
        AVAILABILITY,
        headers=world.player,
        params={"venue_id": world.a.venue.id, "date": manila_date(next_hour()).isoformat()},
    )
    assert response.json()["convenience_fee_percent"] == "3.00"
