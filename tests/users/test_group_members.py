"""Group members (ADR 0003): exact-email adds, owner-only master and
Customer membership, masters protected from non-masters, no self-lockout."""

from dataclasses import dataclass

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import GroupKind
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupModel, UserModel
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue

ADMIN = f"{API_PREFIX}/admin"
GROUPS = f"{ADMIN}/user-groups"


@dataclass
class World:
    a: VenueModel
    admin: dict[str, str]
    master: dict[str, str]
    master_user: UserModel
    lead: dict[str, str]  # USER:Manage at A through Leads, not a master
    lead_user: UserModel
    master_group: int
    front_desk: int
    front_desk_b: int
    leads: int
    customers: int


@pytest.fixture
async def world(client: AsyncClient, db_session: AsyncSession) -> World:
    a = await make_venue(db_session, "Alpha")
    b = await make_venue(db_session, "Bravo")
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    master = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=a.id)
    leads = UserGroupModel(kind=GroupKind.STAFF, venue_id=a.id, name="Leads")
    db_session.add(leads)
    await db_session.flush()
    await group_repository.set_codes(db_session, leads.id, frozenset({"USER:Manage"}))
    lead = await make_user(db_session, role=RoleCode.SUPER_ADMIN)  # no group yet
    await group_repository.add_member(db_session, leads.id, lead.id, added_by=None)
    return World(
        a=a,
        admin=bearer(await login(client, admin.email)),
        master=bearer(await login(client, master.email)),
        master_user=master,
        lead=bearer(await login(client, lead.email)),
        lead_user=lead,
        master_group=await group_repository.venue_group_id(db_session, a.id, GroupKind.MASTER),
        front_desk=await group_repository.venue_group_id(db_session, a.id, GroupKind.STAFF),
        front_desk_b=await group_repository.venue_group_id(db_session, b.id, GroupKind.STAFF),
        leads=leads.id,
        customers=await group_repository.customer_group_id(db_session),
    )


async def add(client: AsyncClient, headers: dict[str, str], group_id: int, email: str) -> Response:
    return await client.post(f"{GROUPS}/{group_id}/members", headers=headers, json={"email": email})


async def remove(
    client: AsyncClient, headers: dict[str, str], group_id: int, user_id: int
) -> Response:
    return await client.delete(f"{GROUPS}/{group_id}/members/{user_id}", headers=headers)


# ── Adding ───────────────────────────────────────────────────────────────


async def test_customer_becomes_staff_and_stays_a_customer(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    kim = await make_user(db_session, email="kim@x.com")

    response = await add(client, world.master, world.front_desk, "KIM@x.com")

    assert response.status_code == 201
    assert (response.json()["user_id"], response.json()["is_customer"]) == (kim.id, True)
    kim_headers = bearer(await login(client, kim.email))
    me = (await client.get(f"{API_PREFIX}/users/me", headers=kim_headers)).json()
    venues = (await client.get(f"{API_PREFIX}/users/me/venues", headers=kim_headers)).json()
    assert me["is_customer"] is True
    assert [v["venue_id"] for v in venues] == [world.a.id]


async def test_missing_and_deleted_accounts_read_the_same(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    gone = await make_user(db_session, email="gone@x.com")
    await db_session.execute(
        update(UserModel).where(UserModel.id == gone.id).values(deleted_at=text("now()"))
    )

    deleted = await add(client, world.master, world.front_desk, "gone@x.com")
    missing = await add(client, world.master, world.front_desk, "nobody@x.com")

    assert deleted.status_code == missing.status_code == 404
    assert deleted.json()["detail"] == missing.json()["detail"]


async def test_deactivated_account_is_refused(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_user(db_session, email="off@x.com", active=False)

    response = await add(client, world.master, world.front_desk, "off@x.com")

    assert response.status_code == 422
    assert response.json()["code"] == "USER_INACTIVE"


async def test_pending_invitee_can_join_more_groups(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_user(db_session, email="invited@x.com", password=None, active=False)

    response = await add(client, world.master, world.front_desk, "invited@x.com")

    assert response.status_code == 201
    assert response.json()["pending_invite"] is True


async def test_already_a_member_is_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_user(db_session, email="kim@x.com")
    await add(client, world.master, world.front_desk, "kim@x.com")

    response = await add(client, world.master, world.front_desk, "kim@x.com")

    assert response.status_code == 409


async def test_only_the_owner_makes_masters(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_user(db_session, email="kim@x.com")

    by_master = await add(client, world.master, world.master_group, "kim@x.com")
    by_owner = await add(client, world.admin, world.master_group, "kim@x.com")

    assert by_master.status_code == 403
    assert by_owner.status_code == 201


async def test_only_the_owner_sees_customer_membership(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await make_user(db_session, email="kim@x.com")

    by_master = await add(client, world.master, world.customers, "kim@x.com")
    listed = await client.get(f"{GROUPS}/{world.customers}/members", headers=world.admin)

    assert by_master.status_code == 404
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1


async def test_another_venues_members_are_404(client: AsyncClient, world: World) -> None:
    listed = await client.get(f"{GROUPS}/{world.front_desk_b}/members", headers=world.master)

    assert listed.status_code == 404


# ── Removing ─────────────────────────────────────────────────────────────


async def test_master_removes_staff(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=world.a.id)

    response = await remove(client, world.master, world.front_desk, staff.id)
    again = await remove(client, world.master, world.front_desk, staff.id)

    assert response.status_code == 204
    assert again.status_code == 404


async def test_non_master_cannot_remove_a_master(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await group_repository.add_member(
        db_session, world.front_desk, world.master_user.id, added_by=None
    )

    by_lead = await remove(client, world.lead, world.front_desk, world.master_user.id)
    by_owner = await remove(client, world.admin, world.front_desk, world.master_user.id)

    assert by_lead.status_code == 403
    assert by_owner.status_code == 204


async def test_master_group_membership_is_the_owners(client: AsyncClient, world: World) -> None:
    response = await remove(client, world.master, world.master_group, world.master_user.id)

    assert response.status_code == 403


async def test_cannot_remove_yourself_from_your_last_user_manage(
    client: AsyncClient, world: World
) -> None:
    response = await remove(client, world.lead, world.leads, world.lead_user.id)

    assert response.status_code == 409
    assert response.json()["code"] == "SELF_LOCKOUT"
