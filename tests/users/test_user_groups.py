"""Group CRUD (ADR 0003). One test per row of the design's management-rules
table; each fails if its rule is removed."""

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import GroupKind
from src.users.persistence.group_repository import group_repository
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue

GROUPS = f"{API_PREFIX}/admin/user-groups"


@dataclass
class World:
    a: VenueModel
    b: VenueModel
    admin: dict[str, str]
    master_a: dict[str, str]
    staff_a: dict[str, str]
    master_group_a: int
    front_desk_a: int
    front_desk_b: int
    customers: int


@pytest.fixture
async def world(client: AsyncClient, db_session: AsyncSession) -> World:
    a = await make_venue(db_session, "Alpha")
    b = await make_venue(db_session, "Bravo")
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    master = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=a.id)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=a.id)
    return World(
        a=a,
        b=b,
        admin=bearer(await login(client, admin.email)),
        master_a=bearer(await login(client, master.email)),
        staff_a=bearer(await login(client, staff.email)),
        master_group_a=await group_repository.venue_group_id(db_session, a.id, GroupKind.MASTER),
        front_desk_a=await group_repository.venue_group_id(db_session, a.id, GroupKind.STAFF),
        front_desk_b=await group_repository.venue_group_id(db_session, b.id, GroupKind.STAFF),
        customers=await group_repository.customer_group_id(db_session),
    )


async def _create(client: AsyncClient, headers: dict[str, str], **body: Any) -> Any:
    return await client.post(GROUPS, headers=headers, json=body)


# ── Reading ──────────────────────────────────────────────────────────────


async def test_master_lists_only_their_venue_master_first(
    client: AsyncClient, world: World
) -> None:
    body = (await client.get(GROUPS, headers=world.master_a)).json()

    assert [(g["id"], g["read_only"]) for g in body["data"]] == [
        (world.master_group_a, True),
        (world.front_desk_a, False),
    ]


async def test_owner_lists_every_venue_and_customer(client: AsyncClient, world: World) -> None:
    every = (await client.get(GROUPS, headers=world.admin, params={"limit": 200})).json()
    one_venue = await client.get(GROUPS, headers=world.admin, params={"venue_id": world.b.id})

    ids = {g["id"] for g in every["data"]}
    assert {world.customers, world.front_desk_a, world.front_desk_b} <= ids
    assert {g["venue_id"] for g in one_venue.json()["data"]} == {world.b.id}
    assert not any(g["read_only"] for g in every["data"])


async def test_another_venues_group_is_404_like_a_missing_one(
    client: AsyncClient, world: World
) -> None:
    foreign = await client.get(f"{GROUPS}/{world.front_desk_b}", headers=world.master_a)
    missing = await client.get(f"{GROUPS}/999999", headers=world.master_a)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"]


async def test_customer_group_is_the_owners_alone(client: AsyncClient, world: World) -> None:
    as_master = await client.get(f"{GROUPS}/{world.customers}", headers=world.master_a)
    as_owner = await client.get(f"{GROUPS}/{world.customers}", headers=world.admin)

    assert as_master.status_code == 404
    assert as_owner.status_code == 200
    assert as_owner.json()["kind"] == "CUSTOMER"


async def test_without_user_manage_groups_are_403(client: AsyncClient, world: World) -> None:
    assert (await client.get(GROUPS, headers=world.staff_a)).status_code == 403


# ── Creating ─────────────────────────────────────────────────────────────


async def test_master_creates_a_staff_group_at_their_venue(
    client: AsyncClient, world: World
) -> None:
    response = await _create(client, world.master_a, name="Weekend crew")

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["venue_id"]) == ("STAFF", world.a.id)
    assert (body["member_count"], body["permissions"]) == (0, [])


@pytest.mark.parametrize("smuggled", [{"venue_id": "B"}, {"kind": "MASTER"}])
async def test_non_owner_cannot_choose_venue_or_kind(
    client: AsyncClient, world: World, smuggled: dict[str, Any]
) -> None:
    body = {"name": "Sneaky", **smuggled}
    if body.get("venue_id") == "B":
        body["venue_id"] = world.b.id

    response = await _create(client, world.master_a, **body)

    assert response.status_code == 422


async def test_owner_creates_at_a_named_venue(client: AsyncClient, world: World) -> None:
    without = await _create(client, world.admin, name="Floating")
    at_b = await _create(client, world.admin, name="Night shift", venue_id=world.b.id)

    assert without.status_code == 422
    assert without.json()["code"] == "VENUE_REQUIRED"
    assert at_b.status_code == 201
    assert at_b.json()["venue_id"] == world.b.id


async def test_owner_cannot_create_a_second_master(client: AsyncClient, world: World) -> None:
    response = await _create(
        client, world.admin, name="Masters 2", venue_id=world.a.id, kind="MASTER"
    )

    assert response.status_code == 422


async def test_names_are_unique_per_venue_ignoring_case(client: AsyncClient, world: World) -> None:
    clash = await _create(client, world.master_a, name="front DESK")
    elsewhere = await _create(client, world.admin, name="Weekend crew", venue_id=world.b.id)
    same_name_a = await _create(client, world.master_a, name="Weekend crew")

    assert clash.status_code == 409
    assert clash.json()["code"] == "GROUP_NAME_TAKEN"
    assert elsewhere.status_code == same_name_a.status_code == 201


# ── Renaming ─────────────────────────────────────────────────────────────


async def test_master_renames_a_staff_group(client: AsyncClient, world: World) -> None:
    response = await client.patch(
        f"{GROUPS}/{world.front_desk_a}", headers=world.master_a, json={"name": "Counter"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Counter"


async def test_master_group_is_read_only_to_its_masters(client: AsyncClient, world: World) -> None:
    by_master = await client.patch(
        f"{GROUPS}/{world.master_group_a}", headers=world.master_a, json={"name": "Bosses"}
    )
    by_owner = await client.patch(
        f"{GROUPS}/{world.master_group_a}", headers=world.admin, json={"name": "Managers"}
    )

    assert by_master.status_code == 403
    assert by_owner.status_code == 200


async def test_customer_group_cannot_be_renamed(client: AsyncClient, world: World) -> None:
    response = await client.patch(
        f"{GROUPS}/{world.customers}", headers=world.admin, json={"name": "Players"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "BUILT_IN_GROUP"


# ── Deleting ─────────────────────────────────────────────────────────────


async def test_group_with_members_cannot_be_deleted(client: AsyncClient, world: World) -> None:
    # Front desk at A has the staff member from the fixture.
    response = await client.delete(f"{GROUPS}/{world.front_desk_a}", headers=world.master_a)

    assert response.status_code == 409
    assert response.json()["code"] == "GROUP_NOT_EMPTY"


async def test_empty_staff_group_is_deleted(client: AsyncClient, world: World) -> None:
    created = (await _create(client, world.master_a, name="Temp")).json()

    deleted = await client.delete(f"{GROUPS}/{created['id']}", headers=world.master_a)
    gone = await client.get(f"{GROUPS}/{created['id']}", headers=world.master_a)

    assert deleted.status_code == 204
    assert gone.status_code == 404


@pytest.mark.parametrize("which", ["master_group_a", "customers"])
async def test_built_in_groups_cannot_be_deleted(
    client: AsyncClient, world: World, which: str
) -> None:
    response = await client.delete(f"{GROUPS}/{getattr(world, which)}", headers=world.admin)

    assert response.status_code == 409
    assert response.json()["code"] == "BUILT_IN_GROUP"


async def test_master_cannot_delete_their_master_group(client: AsyncClient, world: World) -> None:
    response = await client.delete(f"{GROUPS}/{world.master_group_a}", headers=world.master_a)

    assert response.status_code == 403
