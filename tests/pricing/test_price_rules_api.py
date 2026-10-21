from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.courts.persistence.models import CourtModel
from src.utils.api import API_PREFIX
from tests.auth.helpers import make_court
from tests.venues.conftest import World

ADMIN = f"{API_PREFIX}/admin"
SATURDAY, MONDAY = "2026-09-19", "2026-09-21"


@pytest.fixture
async def court(world: World, db_session: AsyncSession) -> CourtModel:
    return await make_court(db_session, world.venue_a.id)


async def _add(client: AsyncClient, headers: dict[str, str], court_id: int, **rule: Any) -> Any:
    return await client.post(f"{ADMIN}/courts/{court_id}/price-rules", headers=headers, json=rule)


async def _prices(client: AsyncClient, world: World, court_id: int, day: str) -> list[int]:
    response = await client.get(
        f"{ADMIN}/courts/{court_id}/prices", headers=world.manager_a, params={"date": day}
    )
    assert response.status_code == 200, response.text
    return [p["price_centavos"] for p in response.json()]


async def test_prices_follow_the_spec_table(
    client: AsyncClient, world: World, court: CourtModel
) -> None:
    """The §4.4 table: flat ₱700, Saturday ₱800, 6pm ₱600, Saturday 6pm ₱900."""
    for rule in (
        {"price_centavos": 70000},
        {"day_of_week": 6, "price_centavos": 80000},
        {"hour_of_day": 18, "price_centavos": 60000},
        {"day_of_week": 6, "hour_of_day": 18, "price_centavos": 90000},
    ):
        assert (await _add(client, world.manager_a, court.id, **rule)).status_code == 201

    saturday = await _prices(client, world, court.id, SATURDAY)
    monday = await _prices(client, world, court.id, MONDAY)

    assert (saturday[18], saturday[10]) == (90000, 80000)
    assert (monday[18], monday[10]) == (60000, 70000)


async def test_prices_fall_back_to_parameter(
    client: AsyncClient, world: World, court: CourtModel
) -> None:
    assert await _prices(client, world, court.id, MONDAY) == [50000] * 24


async def test_prices_day_is_manila(client: AsyncClient, world: World, court: CourtModel) -> None:
    response = await client.get(
        f"{ADMIN}/courts/{court.id}/prices", headers=world.manager_a, params={"date": SATURDAY}
    )

    body = response.json()
    assert len(body) == 24
    assert [p["hour"] for p in body] == list(range(24))
    assert body[0]["starts_at"] == "2026-09-18T16:00:00Z"
    assert body[-1]["starts_at"] == "2026-09-19T15:00:00Z"


async def test_rules_listed_most_specific_first(
    client: AsyncClient, world: World, court: CourtModel
) -> None:
    await _add(client, world.manager_a, court.id, price_centavos=1)
    await _add(client, world.manager_a, court.id, day_of_week=6, price_centavos=2)
    await _add(client, world.manager_a, court.id, hour_of_day=18, price_centavos=3)
    await _add(client, world.manager_a, court.id, day_of_week=6, hour_of_day=18, price_centavos=4)

    response = await client.get(f"{ADMIN}/courts/{court.id}/price-rules", headers=world.manager_a)

    assert [r["price_centavos"] for r in response.json()] == [4, 3, 2, 1]


async def test_duplicate_rule_is_409(client: AsyncClient, world: World, court: CourtModel) -> None:
    await _add(client, world.manager_a, court.id, price_centavos=70000)

    response = await _add(client, world.manager_a, court.id, price_centavos=75000)

    assert response.status_code == 409
    assert response.json()["code"] == "PRICE_RULE_EXISTS"


async def test_patch_changes_price_only(
    client: AsyncClient, world: World, court: CourtModel
) -> None:
    rule = (await _add(client, world.manager_a, court.id, price_centavos=70000)).json()

    ok = await client.patch(
        f"{ADMIN}/price-rules/{rule['id']}", headers=world.manager_a, json={"price_centavos": 1}
    )
    moved = await client.patch(
        f"{ADMIN}/price-rules/{rule['id']}", headers=world.manager_a, json={"day_of_week": 1}
    )

    assert ok.status_code == 200
    assert ok.json()["price_centavos"] == 1
    assert moved.status_code == 422


async def test_delete_rule(client: AsyncClient, world: World, court: CourtModel) -> None:
    rule = (await _add(client, world.manager_a, court.id, price_centavos=70000)).json()

    deleted = await client.delete(f"{ADMIN}/price-rules/{rule['id']}", headers=world.manager_a)

    assert deleted.status_code == 204
    assert await _prices(client, world, court.id, MONDAY) == [50000] * 24


async def test_manager_cannot_price_other_venue_court(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    theirs = await make_court(db_session, world.venue_b.id)
    rule = (await _add(client, world.admin, theirs.id, price_centavos=70000)).json()

    responses = [
        await client.get(f"{ADMIN}/courts/{theirs.id}/price-rules", headers=world.manager_a),
        await _add(client, world.manager_a, theirs.id, hour_of_day=1, price_centavos=1),
        await client.get(
            f"{ADMIN}/courts/{theirs.id}/prices", headers=world.manager_a, params={"date": MONDAY}
        ),
        await client.patch(
            f"{ADMIN}/price-rules/{rule['id']}", headers=world.manager_a, json={"price_centavos": 1}
        ),
        await client.delete(f"{ADMIN}/price-rules/{rule['id']}", headers=world.manager_a),
    ]

    assert [r.status_code for r in responses] == [404] * 5


async def test_staff_cannot_manage_prices(
    client: AsyncClient, world: World, court: CourtModel
) -> None:
    response = await _add(client, world.staff_a, court.id, price_centavos=1)

    assert response.status_code == 403


@pytest.mark.parametrize(
    "rule",
    [
        {"hour_of_day": 24, "price_centavos": 1},
        {"day_of_week": 7, "price_centavos": 1},
        {"price_centavos": -1},
        {"price_centavos": 1.5},
    ],
)
async def test_invalid_rule_is_422(
    client: AsyncClient, world: World, court: CourtModel, rule: dict[str, Any]
) -> None:
    assert (await _add(client, world.manager_a, court.id, **rule)).status_code == 422
