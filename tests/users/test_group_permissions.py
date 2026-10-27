"""Setting a group's permissions (ADR 0003): owner-only codes are never
grantable, a non-owner touches only codes they hold, codes they don't hold
are kept, and nobody removes their own last way to manage groups."""

from dataclasses import dataclass

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import FRONT_DESK_CODES, GroupKind
from src.users.domain.roles import PERMISSIONS
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupModel
from src.utils.api import API_PREFIX
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue

ADMIN = f"{API_PREFIX}/admin"
GROUPS = f"{ADMIN}/user-groups"
LEAD_CODES = frozenset({"USER:Manage", "BOOKING:ReadAny"})


@dataclass
class World:
    admin: dict[str, str]
    master: dict[str, str]
    lead: dict[str, str]  # holds only LEAD_CODES, through the Leads group
    master_group: int
    front_desk: int
    leads: int
    customers: int


@pytest.fixture
async def world(client: AsyncClient, db_session: AsyncSession) -> World:
    venue = await make_venue(db_session)
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    master = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=venue.id)
    leads = UserGroupModel(kind=GroupKind.STAFF, venue_id=venue.id, name="Leads")
    db_session.add(leads)
    await db_session.flush()
    await group_repository.set_codes(db_session, leads.id, LEAD_CODES)
    lead = await make_user(db_session)
    await group_repository.add_member(db_session, leads.id, lead.id, added_by=None)
    return World(
        admin=bearer(await login(client, admin.email)),
        master=bearer(await login(client, master.email)),
        lead=bearer(await login(client, lead.email)),
        master_group=await group_repository.venue_group_id(db_session, venue.id, GroupKind.MASTER),
        front_desk=await group_repository.venue_group_id(db_session, venue.id, GroupKind.STAFF),
        leads=leads.id,
        customers=await group_repository.customer_group_id(db_session),
    )


async def put(
    client: AsyncClient, headers: dict[str, str], group_id: int, codes: set[str] | frozenset[str]
) -> Response:
    return await client.put(
        f"{GROUPS}/{group_id}/permissions", headers=headers, json={"codes": sorted(codes)}
    )


async def test_owner_replaces_the_whole_set(client: AsyncClient, world: World) -> None:
    response = await put(client, world.admin, world.front_desk, {"BOOKING:ReadAny"})

    assert response.status_code == 200
    assert response.json()["permissions"] == ["BOOKING:ReadAny"]


@pytest.mark.parametrize("who", ["admin", "master"])
@pytest.mark.parametrize("code", ["PARAMETER:Manage", "VENUE:Manage"])
async def test_owner_only_codes_are_never_grantable(
    client: AsyncClient, world: World, who: str, code: str
) -> None:
    """Not even by the owner: these live on system_admin, never on a group."""
    response = await put(client, getattr(world, who), world.front_desk, {code})

    assert response.status_code == 422
    assert response.json()["code"] == "OWNER_ONLY_PERMISSION"


async def test_unknown_code_is_422(client: AsyncClient, world: World) -> None:
    response = await put(client, world.admin, world.front_desk, {"COURT:Destroy"})

    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_PERMISSION"


async def test_customer_group_holds_no_codes(client: AsyncClient, world: World) -> None:
    response = await put(client, world.admin, world.customers, {"BOOKING:ReadAny"})

    assert response.status_code == 422
    assert response.json()["code"] == "CUSTOMER_HAS_NO_PERMISSIONS"


async def test_master_group_is_the_owners_to_set(client: AsyncClient, world: World) -> None:
    response = await put(client, world.master, world.master_group, {"BOOKING:ReadAny"})

    assert response.status_code == 403


async def test_master_grants_what_they_hold(client: AsyncClient, world: World) -> None:
    response = await put(
        client, world.master, world.front_desk, FRONT_DESK_CODES | {"COURT:Manage"}
    )

    assert response.status_code == 200
    assert "COURT:Manage" in response.json()["permissions"]


async def test_cannot_grant_a_code_you_do_not_hold(client: AsyncClient, world: World) -> None:
    response = await put(client, world.lead, world.front_desk, FRONT_DESK_CODES | {"COURT:Manage"})

    assert response.status_code == 403


async def test_codes_you_do_not_hold_are_kept(client: AsyncClient, world: World) -> None:
    await put(client, world.admin, world.front_desk, {"COURT:Manage", "BOOKING:ReadAny"})

    # The lead's checkbox state omits COURT:Manage, which they cannot touch,
    # and drops BOOKING:ReadAny, which they hold.
    response = await put(client, world.lead, world.front_desk, set())

    assert response.status_code == 200
    assert response.json()["permissions"] == ["COURT:Manage"]


async def test_cannot_remove_your_own_last_user_manage(client: AsyncClient, world: World) -> None:
    response = await put(client, world.lead, world.leads, {"BOOKING:ReadAny"})

    assert response.status_code == 409
    assert response.json()["code"] == "SELF_LOCKOUT"


async def test_may_remove_user_manage_held_another_way(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await put(client, world.admin, world.front_desk, FRONT_DESK_CODES | {"USER:Manage"})
    lead_id = (await client.get(f"{API_PREFIX}/users/me", headers=world.lead)).json()["id"]
    await group_repository.add_member(db_session, world.front_desk, lead_id, added_by=None)

    response = await put(client, world.lead, world.leads, {"BOOKING:ReadAny"})

    assert response.status_code == 200


async def test_permission_catalogue_flags_owner_only(client: AsyncClient, world: World) -> None:
    body = (await client.get(f"{ADMIN}/permissions", headers=world.master)).json()

    assert {p["code"] for p in body} == set(PERMISSIONS)
    assert {p["code"] for p in body if p["owner_only"]} == {"VENUE:Manage", "PARAMETER:Manage"}
