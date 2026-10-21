from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.api import API_PREFIX
from tests.auth.helpers import make_court
from tests.venues.conftest import World

ADMIN = f"{API_PREFIX}/admin/venues"
CATALOG = f"{API_PREFIX}/venues"


async def test_manager_cannot_create_venue(client: AsyncClient, world: World) -> None:
    response = await client.post(ADMIN, headers=world.manager_a, json={"name": "Mine"})

    assert response.status_code == 403


async def test_admin_creates_and_deactivates_venue(client: AsyncClient, world: World) -> None:
    created = await client.post(
        ADMIN, headers=world.admin, json={"name": "Charlie", "address": "Pasig"}
    )
    venue_id = created.json()["id"]

    patched = await client.patch(
        f"{ADMIN}/{venue_id}", headers=world.admin, json={"is_active": False}
    )
    listed = await client.get(ADMIN, headers=world.admin, params={"limit": 100})

    assert created.status_code == 201
    assert created.json() == {
        "id": venue_id,
        "name": "Charlie",
        "address": "Pasig",
        "is_active": True,
    }
    assert patched.json()["is_active"] is False
    assert venue_id in {v["id"] for v in listed.json()["data"]}  # admin still sees it


async def test_admin_venue_rejects_unknown_fields(client: AsyncClient, world: World) -> None:
    response = await client.post(ADMIN, headers=world.admin, json={"name": "X", "id": 5})

    assert response.status_code == 422


async def test_player_can_browse_catalog(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_court(db_session, world.venue_a.id, "Court 1")

    venues = await client.get(CATALOG, headers=world.player, params={"limit": 100})
    courts = await client.get(f"{CATALOG}/{world.venue_a.id}/courts", headers=world.player)

    assert {world.venue_a.id, world.venue_b.id} <= {v["id"] for v in venues.json()["data"]}
    assert [c["name"] for c in courts.json()["data"]] == ["Court 1"]


async def test_catalog_requires_auth(client: AsyncClient) -> None:
    assert (await client.get(CATALOG)).status_code == 401


async def test_catalog_hides_inactive_venue_and_its_courts(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_court(db_session, world.venue_b.id)
    world.venue_b.is_active = False
    await db_session.flush()

    venues = await client.get(CATALOG, headers=world.player, params={"limit": 100})
    courts = await client.get(f"{CATALOG}/{world.venue_b.id}/courts", headers=world.player)

    assert world.venue_b.id not in {v["id"] for v in venues.json()["data"]}
    assert courts.status_code == 404
