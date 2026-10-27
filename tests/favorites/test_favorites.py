from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.favorites.persistence.models import FavoriteVenueModel
from src.utils.api import API_PREFIX
from tests.bookings.conftest import World

URL = f"{API_PREFIX}/users/me/favorite-venues"


async def _ids(client: AsyncClient, headers: dict[str, str]) -> list[int]:
    response = await client.get(URL, headers=headers)
    assert response.status_code == 200, response.text
    return [v["id"] for v in response.json()]


async def test_add_lists_newest_first(client: AsyncClient, world: World) -> None:
    assert (await client.put(f"{URL}/{world.a.venue.id}", headers=world.player)).status_code == 204
    await client.put(f"{URL}/{world.b.venue.id}", headers=world.player)

    assert await _ids(client, world.player) == [world.b.venue.id, world.a.venue.id]
    body = (await client.get(URL, headers=world.player)).json()
    assert set(body[0]) == {"id", "name", "address", "is_active"}


async def test_adding_twice_keeps_one(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    for _ in range(2):
        assert (
            await client.put(f"{URL}/{world.a.venue.id}", headers=world.player)
        ).status_code == 204

    count = await db_session.scalar(
        select(func.count()).where(FavoriteVenueModel.user_id == world.player_user.id)
    )
    assert count == 1


async def test_remove_is_idempotent(client: AsyncClient, world: World) -> None:
    await client.put(f"{URL}/{world.a.venue.id}", headers=world.player)

    first = await client.delete(f"{URL}/{world.a.venue.id}", headers=world.player)
    again = await client.delete(f"{URL}/{world.a.venue.id}", headers=world.player)

    assert first.status_code == again.status_code == 204
    assert await _ids(client, world.player) == []


async def test_unknown_or_inactive_venue_is_404(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    world.b.venue.is_active = False
    await db_session.flush()

    assert (await client.put(f"{URL}/999999", headers=world.player)).status_code == 404
    assert (await client.put(f"{URL}/{world.b.venue.id}", headers=world.player)).status_code == 404


async def test_an_inactive_favorite_is_not_listed(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await client.put(f"{URL}/{world.a.venue.id}", headers=world.player)
    world.a.venue.is_active = False
    await db_session.flush()

    assert await _ids(client, world.player) == []


async def test_favorites_are_per_customer(client: AsyncClient, world: World) -> None:
    await client.put(f"{URL}/{world.a.venue.id}", headers=world.player)

    assert await _ids(client, world.player2) == []


async def test_non_customer_is_403(client: AsyncClient, world: World) -> None:
    assert (await client.get(URL, headers=world.staff_a)).status_code == 403
    assert (await client.put(f"{URL}/{world.a.venue.id}", headers=world.staff_a)).status_code == 403
