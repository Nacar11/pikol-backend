import re
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.email.adapters import MemoryEmailSender
from src.users.domain.roles import RoleCode
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import AUTH, bearer, login, make_user, make_venue

ADMIN = f"{API_PREFIX}/admin"
INVITEE = {"email": "new@x.com", "first_name": "New", "last_name": "Staff"}


class World:
    venue_a: VenueModel
    venue_b: VenueModel
    admin: dict[str, str]
    manager_a: dict[str, str]
    manager_a_user: UserModel
    staff_a: UserModel
    staff_b: UserModel


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
    assert all(u["venue_id"] == world.venue_a.id for u in response.json()["data"])


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


@pytest.mark.parametrize("smuggled", [{"role_id": 1}, {"role": "SUPER_ADMIN"}, {"venue_id": -1}])
async def test_manager_invite_rejects_privilege_fields(
    client: AsyncClient, world: World, smuggled: dict[str, Any]
) -> None:
    body = {**INVITEE, **smuggled}
    if "venue_id" in smuggled:
        body["venue_id"] = world.venue_b.id

    response = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=body)

    assert response.status_code == 422


async def test_manager_invite_pins_role_and_venue(
    client: AsyncClient, world: World, outbox: MemoryEmailSender, db_session: AsyncSession
) -> None:
    response = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=INVITEE)

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "VENUE_STAFF"
    assert body["venue_id"] == world.venue_a.id
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

    response = await client.post(f"{ADMIN}/venue-staff", headers=staff, json=INVITEE)

    assert response.status_code == 403


async def test_admin_without_venue_cannot_use_manager_route(
    client: AsyncClient, world: World
) -> None:
    response = await client.post(f"{ADMIN}/venue-staff", headers=world.admin, json=INVITEE)

    assert response.status_code == 403


async def test_admin_invites_manager_to_any_venue(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users",
        headers=world.admin,
        json={**INVITEE, "role": "VENUE_MANAGER", "venue_id": world.venue_b.id},
    )

    assert response.status_code == 201
    assert response.json()["role"] == "VENUE_MANAGER"
    assert response.json()["venue_id"] == world.venue_b.id


async def test_admin_cannot_invite_a_super_admin(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users",
        headers=world.admin,
        json={**INVITEE, "role": "SUPER_ADMIN", "venue_id": world.venue_a.id},
    )

    assert response.status_code == 422


async def test_admin_invite_unknown_venue_is_422(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users",
        headers=world.admin,
        json={**INVITEE, "role": "VENUE_STAFF", "venue_id": 999999},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_VENUE"


async def test_manager_cannot_use_admin_invite(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/users",
        headers=world.manager_a,
        json={**INVITEE, "role": "VENUE_MANAGER", "venue_id": world.venue_a.id},
    )

    assert response.status_code == 403


async def test_invite_existing_email_is_409(client: AsyncClient, world: World) -> None:
    response = await client.post(
        f"{ADMIN}/venue-staff",
        headers=world.manager_a,
        json={**INVITEE, "email": world.staff_a.email.upper()},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_TAKEN"


async def test_manager_cannot_modify_another_manager(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    peer = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=world.venue_a.id)

    response = await client.patch(
        f"{ADMIN}/users/{peer.id}", headers=world.manager_a, json={"is_active": False}
    )

    assert response.status_code == 403


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
    await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=INVITEE)

    accepted = await client.post(
        f"{AUTH}/invitations/accept",
        json={"token": _invite_token(outbox), "password": "my-own-pass-1"},
    )

    assert accepted.status_code == 204
    tokens = await login(client, "new@x.com", "my-own-pass-1")
    me = (await client.get(f"{API_PREFIX}/users/me", headers=bearer(tokens))).json()
    assert me["role"] == "VENUE_STAFF"
    assert me["venue_id"] == world.venue_a.id
    assert me["email_verified_at"] is not None


async def test_resend_invitation_kills_the_old_link(
    client: AsyncClient, world: World, outbox: MemoryEmailSender
) -> None:
    created = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=INVITEE)
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
    created = await client.post(f"{ADMIN}/venue-staff", headers=world.manager_a, json=INVITEE)
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
