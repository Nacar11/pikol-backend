from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.courts.persistence.models import CourtModel
from src.utils.api import API_PREFIX
from tests.auth.helpers import make_court
from tests.venues.conftest import World

ADMIN = f"{API_PREFIX}/admin"
CATALOG = f"{API_PREFIX}/venues"


def _sans_request_id(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "request_id"}


async def test_manager_creates_court_at_own_venue(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/venues/{world.venue_a.id}/courts",
        headers=world.manager_a,
        json={"name": "Court 1", "sort_order": 2},
    )

    assert response.status_code == 201
    assert response.json()["venue_id"] == world.venue_a.id
    assert response.json()["sort_order"] == 2
    assert response.json()["is_active"] is True


async def test_manager_cannot_touch_other_venue_court(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """404, identical to a missing id — a 403 would confirm Venue B's courts
    exist (spec §6.2)."""
    theirs = await make_court(db_session, world.venue_b.id)

    create = await client.post(
        f"{ADMIN}/venues/{world.venue_b.id}/courts", headers=world.manager_a, json={"name": "X"}
    )
    listed = await client.get(f"{ADMIN}/venues/{world.venue_b.id}/courts", headers=world.manager_a)
    got = await client.get(f"{ADMIN}/courts/{theirs.id}", headers=world.manager_a)
    patched = await client.patch(
        f"{ADMIN}/courts/{theirs.id}", headers=world.manager_a, json={"name": "Mine"}
    )
    missing = await client.get(f"{ADMIN}/courts/999999", headers=world.manager_a)

    assert {create.status_code, listed.status_code, got.status_code, patched.status_code} == {404}
    assert _sans_request_id(got.json()) == _sans_request_id(missing.json())


async def test_staff_cannot_manage_courts(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/venues/{world.venue_a.id}/courts", headers=world.staff_a, json={"name": "X"}
    )

    assert response.status_code == 403


async def test_admin_manages_any_venue_courts(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/venues/{world.venue_b.id}/courts", headers=world.admin, json={"name": "B1"}
    )

    assert response.status_code == 201


async def test_courts_ordered_by_sort_order_then_id(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_court(db_session, world.venue_a.id, "Third", sort_order=2)
    await make_court(db_session, world.venue_a.id, "First", sort_order=1)
    await make_court(db_session, world.venue_a.id, "Second", sort_order=1)

    response = await client.get(f"{CATALOG}/{world.venue_a.id}/courts", headers=world.player)

    assert [c["name"] for c in response.json()["data"]] == ["First", "Second", "Third"]


async def test_catalog_hides_inactive_court_but_admin_sees_it(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    court = await make_court(db_session, world.venue_a.id, "Closed")

    await client.patch(
        f"{ADMIN}/courts/{court.id}", headers=world.manager_a, json={"is_active": False}
    )
    catalog = await client.get(f"{CATALOG}/{world.venue_a.id}/courts", headers=world.player)
    admin = await client.get(f"{ADMIN}/venues/{world.venue_a.id}/courts", headers=world.manager_a)

    assert court.id not in {c["id"] for c in catalog.json()["data"]}
    assert court.id in {c["id"] for c in admin.json()["data"]}


async def test_court_audit_columns_stamped(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    created = await client.post(
        f"{ADMIN}/venues/{world.venue_a.id}/courts", headers=world.manager_a, json={"name": "C"}
    )
    await client.patch(
        f"{ADMIN}/courts/{created.json()['id']}", headers=world.admin, json={"name": "C2"}
    )

    court = (
        await db_session.execute(select(CourtModel).where(CourtModel.id == created.json()["id"]))
    ).scalar_one()
    assert court.created_by_user_id == world.manager_a_user.id
    assert court.updated_by_user_id == world.admin_user.id


async def test_court_cannot_be_moved_to_another_venue(client: AsyncClient, world: World) -> None:
    created = await client.post(
        f"{ADMIN}/venues/{world.venue_a.id}/courts", headers=world.manager_a, json={"name": "C"}
    )

    response = await client.patch(
        f"{ADMIN}/courts/{created.json()['id']}",
        headers=world.manager_a,
        json={"venue_id": world.venue_b.id},
    )

    assert response.status_code == 422
