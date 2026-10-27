import re
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.email.adapters import MemoryEmailSender
from src.users.domain.groups import GroupKind
from src.users.domain.roles import RoleCode
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupModel, UserModel
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import AUTH, bearer, login, make_user, make_venue

ADMIN = f"{API_PREFIX}/admin"
PERSON = {"email": "new@x.com", "first_name": "New", "last_name": "Staff"}


class World:
    venue_a: VenueModel
    venue_b: VenueModel
    admin: dict[str, str]
    manager_a: dict[str, str]
    manager_a_user: UserModel
    staff_a: UserModel
    staff_b: UserModel
    front_desk_a: int
    front_desk_b: int
    master_a: int
    master_b: int
    invitee: dict[str, Any]  # PERSON, into Front desk at A


@pytest.fixture
async def world(client: AsyncClient, db_session: AsyncSession) -> World:
    w = World()
    w.venue_a = await make_venue(db_session, "A")
    w.venue_b = await make_venue(db_session, "B")
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    w.manager_a_user = await make_user(
        db_session, role=RoleCode.VENUE_MANAGER, venue_id=w.venue_a.id
    )
    w.staff_a = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=w.venue_a.id)
    w.staff_b = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=w.venue_b.id)
    w.admin = bearer(await login(client, admin.email))
    w.manager_a = bearer(await login(client, w.manager_a_user.email))
    w.front_desk_a = await group_repository.venue_group_id(
        db_session, w.venue_a.id, GroupKind.STAFF
    )
    w.front_desk_b = await group_repository.venue_group_id(
        db_session, w.venue_b.id, GroupKind.STAFF
    )
    w.master_a = await group_repository.venue_group_id(db_session, w.venue_a.id, GroupKind.MASTER)
    w.master_b = await group_repository.venue_group_id(db_session, w.venue_b.id, GroupKind.MASTER)
    w.invitee = {**PERSON, "group_id": w.front_desk_a}
    return w


def _invite_token(outbox: MemoryEmailSender) -> str:
    match = re.search(r"/accept-invite\?token=([\w-]+)", outbox.outbox[-1].body)
    assert match
    return match.group(1)


def _sans_request_id(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "request_id"}


async def test_player_cannot_reach_admin_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    player = await make_user(db_session)

    response = await client.get(f"{ADMIN}/users", headers=bearer(await login(client, player.email)))

    assert response.status_code == 403


async def test_manager_lists_only_own_venue(client: AsyncClient, world: World) -> None:
    response = await client.get(f"{ADMIN}/users", headers=world.manager_a, params={"limit": 100})

    ids = {u["id"] for u in response.json()["data"]}
    assert response.status_code == 200
    assert world.staff_a.id in ids
    assert world.staff_b.id not in ids
    venues = {g["venue_id"] for u in response.json()["data"] for g in u["groups"]}
    assert venues <= {world.venue_a.id, None}  # A's groups, and Customer


async def test_admin_lists_every_venue(client: AsyncClient, world: World) -> None:
    response = await client.get(f"{ADMIN}/users", headers=world.admin, params={"limit": 100})

    ids = {u["id"] for u in response.json()["data"]}
    assert {world.staff_a.id, world.staff_b.id} <= ids


async def test_manager_cannot_see_other_venue_user_by_id(client: AsyncClient, world: World) -> None:
    """404, byte-for-byte the same as a nonexistent id: a 403 would confirm
    the user exists (spec §6.2)."""
    other = await client.get(f"{ADMIN}/users/{world.staff_b.id}", headers=world.manager_a)
    missing = await client.get(f"{ADMIN}/users/999999", headers=world.manager_a)
    patched = await client.patch(
        f"{ADMIN}/users/{world.staff_b.id}", headers=world.manager_a, json={"is_active": False}
    )

    assert other.status_code == missing.status_code == patched.status_code == 404
    assert _sans_request_id(other.json()) == _sans_request_id(missing.json())


@pytest.mark.parametrize(
    "smuggled", [{"role_id": 1}, {"role": "SUPER_ADMIN"}, {"venue_id": -1}, {"kind": "MASTER"}]
)
async def test_manager_invite_rejects_privilege_fields(
    client: AsyncClient, world: World, smuggled: dict[str, Any]
) -> None:
    body = {**world.invitee, **smuggled}
    if "venue_id" in smuggled:
        body["venue_id"] = world.venue_b.id

    response = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=body)

    assert response.status_code == 422


async def test_manager_invites_into_own_staff_group(
    client: AsyncClient, world: World, outbox: MemoryEmailSender, db_session: AsyncSession
) -> None:
    response = await client.post(
        f"{ADMIN}/venue-staff", headers=world.manager_a, json=world.invitee
    )

    assert response.status_code == 201
    body = response.json()
    assert [(g["id"], g["venue_id"]) for g in body["groups"]] == [
        (world.front_desk_a, world.venue_a.id)
    ]
    assert body["is_active"] is False
    created = (
        await db_session.execute(select(UserModel).where(UserModel.id == body["id"]))
    ).scalar_one()
    await db_session.refresh(created, ["password_hash"])
    assert created.password_hash is None
    assert outbox.outbox[-1].to == "new@x.com"
    _invite_token(outbox)


async def test_staff_cannot_invite(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    staff = bearer(await login(client, world.staff_a.email))

    response = await client.post(f"{ADMIN}/venue-staff", headers=staff, json=world.invitee)

    assert response.status_code == 403


async def test_manager_cannot_invite_into_another_venues_group(
    client: AsyncClient, world: World
) -> None:
    """Indistinguishable from a group that does not exist."""
    foreign = await client.post(
        f"{ADMIN}/venue-staff",
        headers=world.manager_a,
        json={**PERSON, "group_id": world.front_desk_b},
    )
    missing = await client.post(
        f"{ADMIN}/venue-staff", headers=world.manager_a, json={**PERSON, "group_id": 999999}
    )

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json()["code"] == missing.json()["code"] == "UNKNOWN_GROUP"


async def test_manager_cannot_invite_a_master(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/venue-staff", headers=world.manager_a, json={**PERSON, "group_id": world.master_a}
    )

    assert response.status_code == 403


async def test_admin_invites_master_to_any_venue(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users", headers=world.admin, json={**PERSON, "group_id": world.master_b}
    )

    assert response.status_code == 201
    assert [g["kind"] for g in response.json()["groups"]] == ["MASTER"]
    assert response.json()["groups"][0]["venue_id"] == world.venue_b.id


async def test_customer_group_is_not_an_invitation(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    customers = await group_repository.customer_group_id(db_session)

    response = await client.post(
        f"{ADMIN}/users", headers=world.admin, json={**PERSON, "group_id": customers}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "NOT_A_STAFF_GROUP"


async def test_admin_invite_unknown_group_is_422(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users", headers=world.admin, json={**PERSON, "group_id": 999999}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_GROUP"


async def test_manager_cannot_use_admin_invite(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users", headers=world.manager_a, json={**PERSON, "group_id": world.master_a}
    )

    assert response.status_code == 403


async def test_invite_existing_email_is_409(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/venue-staff",
        headers=world.manager_a,
        json={**world.invitee, "email": world.staff_a.email.upper()},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_EXISTS"


async def test_manager_cannot_modify_another_manager(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    peer = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=world.venue_a.id)

    response = await client.patch(
        f"{ADMIN}/users/{peer.id}", headers=world.manager_a, json={"is_active": False}
    )

    assert response.status_code == 403


async def test_staff_holding_user_manage_cannot_touch_a_master(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    leads = UserGroupModel(kind=GroupKind.STAFF, venue_id=world.venue_a.id, name="Leads")
    db_session.add(leads)
    await db_session.flush()
    await db_session.execute(
        text(
            "INSERT INTO user_group_permissions (group_id, permission_id) "
            "SELECT :g, id FROM permissions WHERE code = 'USER:Manage'"
        ).bindparams(g=leads.id)
    )
    lead = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=world.venue_a.id)
    await group_repository.add_member(db_session, leads.id, lead.id, added_by=None)
    headers = bearer(await login(client, lead.email))

    on_master = await client.patch(
        f"{ADMIN}/users/{world.manager_a_user.id}", headers=headers, json={"is_active": False}
    )
    on_staff = await client.patch(
        f"{ADMIN}/users/{world.staff_a.id}", headers=headers, json={"first_name": "Renamed"}
    )

    assert on_master.status_code == 403
    assert on_staff.status_code == 200


async def test_venue_cannot_deactivate_someone_who_works_elsewhere(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await group_repository.add_member(
        db_session, world.front_desk_b, world.staff_a.id, added_by=None
    )

    by_manager = await client.patch(
        f"{ADMIN}/users/{world.staff_a.id}", headers=world.manager_a, json={"is_active": False}
    )
    by_owner = await client.patch(
        f"{ADMIN}/users/{world.staff_a.id}", headers=world.admin, json={"is_active": False}
    )

    assert by_manager.status_code == 403
    assert by_owner.status_code == 200


async def test_venue_sees_only_its_own_memberships(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await group_repository.add_member(
        db_session, world.front_desk_b, world.staff_a.id, added_by=None
    )

    from_a = await client.get(f"{ADMIN}/users/{world.staff_a.id}", headers=world.manager_a)
    from_owner = await client.get(f"{ADMIN}/users/{world.staff_a.id}", headers=world.admin)

    assert [g["id"] for g in from_a.json()["groups"]] == [world.front_desk_a]
    assert {g["id"] for g in from_owner.json()["groups"]} == {
        world.front_desk_a,
        world.front_desk_b,
    }


async def test_admin_searches_users_by_name_or_email(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    kim = await make_user(db_session, email="kim.tan@x.com")

    by_email = await client.get(f"{ADMIN}/users", headers=world.admin, params={"q": "KIM.TAN"})
    wildcard = await client.get(f"{ADMIN}/users", headers=world.admin, params={"q": "%"})

    assert [u["id"] for u in by_email.json()["data"]] == [kim.id]
    assert wildcard.json()["total"] == 0


async def test_cannot_modify_self_via_admin_route(client: AsyncClient, world: World) -> None:
    response = await client.patch(
        f"{ADMIN}/users/{world.manager_a_user.id}",
        headers=world.manager_a,
        json={"first_name": "Me"},
    )

    assert response.status_code == 403


async def test_admin_patch_forbids_role_and_venue(client: AsyncClient, world: World) -> None:
    response = await client.patch(
        f"{ADMIN}/users/{world.staff_a.id}", headers=world.admin, json={"venue_id": 1}
    )

    assert response.status_code == 422


async def test_deactivating_staff_kills_their_sessions(client: AsyncClient, world: World) -> None:
    staff_tokens = await login(client, world.staff_a.email)

    response = await client.patch(
        f"{ADMIN}/users/{world.staff_a.id}", headers=world.manager_a, json={"is_active": False}
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    me = await client.get(f"{API_PREFIX}/users/me", headers=bearer(staff_tokens))
    assert me.status_code == 401
    refresh = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": staff_tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_accept_invitation_activates_and_logs_in(
    client: AsyncClient, world: World, outbox: MemoryEmailSender
) -> None:
    await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=world.invitee)

    accepted = await client.post(
        f"{AUTH}/invitations/accept",
        json={"token": _invite_token(outbox), "password": "my-own-pass-1"},
    )

    assert accepted.status_code == 204
    tokens = await login(client, "new@x.com", "my-own-pass-1")
    me = (await client.get(f"{API_PREFIX}/users/me", headers=bearer(tokens))).json()
    assert [g["id"] for g in me["groups"]] == [world.front_desk_a]
    assert me["is_customer"] is False
    assert me["email_verified_at"] is not None


async def test_resend_invitation_kills_the_old_link(
    client: AsyncClient, world: World, outbox: MemoryEmailSender
) -> None:
    created = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=world.invitee)
    old = _invite_token(outbox)

    resent = await client.post(
        f"{ADMIN}/users/{created.json()['id']}/invitation", headers=world.manager_a
    )

    assert resent.status_code == 202
    assert _invite_token(outbox) != old
    dead = await client.post(
        f"{AUTH}/invitations/accept", json={"token": old, "password": "my-own-pass-1"}
    )
    assert dead.status_code == 400


async def test_revoked_invitation_link_is_dead(
    client: AsyncClient, world: World, outbox: MemoryEmailSender
) -> None:
    created = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=world.invitee)
    token = _invite_token(outbox)

    revoked = await client.delete(
        f"{ADMIN}/users/{created.json()['id']}/invitation", headers=world.manager_a
    )

    assert revoked.status_code == 204
    accept = await client.post(
        f"{AUTH}/invitations/accept", json={"token": token, "password": "my-own-pass-1"}
    )
    assert accept.status_code == 400


async def test_resend_after_accept_is_409(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users/{world.staff_a.id}/invitation", headers=world.manager_a
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_ACCEPTED"


async def test_deactivated_invitee_cannot_reactivate_via_old_link(
    client: AsyncClient, world: World, outbox: MemoryEmailSender
) -> None:
    """Accepting an invitation sets is_active = True. If deactivation left the
    link alive, the invitee could undo their own deactivation."""
    created = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=world.invitee)
    token = _invite_token(outbox)

    await client.patch(
        f"{ADMIN}/users/{created.json()['id']}",
        headers=world.manager_a,
        json={"is_active": False},
    )
    accept = await client.post(
        f"{AUTH}/invitations/accept", json={"token": token, "password": "my-own-pass-1"}
    )

    assert accept.status_code == 400
    me = await client.get(f"{ADMIN}/users/{created.json()['id']}", headers=world.manager_a)
    assert me.json()["is_active"] is False
