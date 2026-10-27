from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import FRONT_DESK_CODES
from src.users.domain.roles import PERMISSIONS
from src.utils.api import API_PREFIX
from tests.auth.helpers import AUTH, PASSWORD, RoleCode, bearer, login, make_user, make_venue

ME = f"{API_PREFIX}/users/me"


async def test_me_requires_auth(client: AsyncClient) -> None:
    assert (await client.get(ME)).status_code == 401


async def test_get_me_returns_own_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)

    response = await client.get(ME, headers=bearer(await login(client, user.email)))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user.id
    assert body["is_customer"] is True
    assert [(g["name"], g["kind"]) for g in body["groups"]] == [("Customer", "CUSTOMER")]
    assert "role" not in body and "venue_id" not in body


async def test_patch_me_updates_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    auth = bearer(await login(client, user.email))

    response = await client.patch(ME, headers=auth, json={"first_name": "Janet", "mobile": "0917"})

    assert response.status_code == 200
    assert response.json()["first_name"] == "Janet"
    assert response.json()["mobile"] == "0917"


async def test_patch_me_forbids_email_and_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    auth = bearer(await login(client, user.email))

    for field in ({"email": "new@x.com"}, {"role": "SUPER_ADMIN"}, {"venue_id": 1}):
        assert (await client.patch(ME, headers=auth, json=field)).status_code == 422


async def test_permissions_super_admin_gets_every_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)

    response = await client.get(
        f"{ME}/permissions", headers=bearer(await login(client, admin.email))
    )

    assert response.json() == sorted(PERMISSIONS)


async def test_permissions_staff_matches_matrix(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    venue = await make_venue(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)

    response = await client.get(
        f"{ME}/permissions", headers=bearer(await login(client, staff.email))
    )

    assert response.json() == sorted(FRONT_DESK_CODES)


async def test_permissions_player_is_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    player = await make_user(db_session)

    response = await client.get(
        f"{ME}/permissions", headers=bearer(await login(client, player.email))
    )

    assert response.json() == []


async def test_change_password_requires_current_and_revokes_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    tokens = await login(client, user.email)

    wrong = await client.patch(
        f"{ME}/password",
        headers=bearer(tokens),
        json={"current_password": "not-it-at-all", "new_password": "brand-new-pass-2"},
    )
    right = await client.patch(
        f"{ME}/password",
        headers=bearer(tokens),
        json={"current_password": PASSWORD, "new_password": "brand-new-pass-2"},
    )

    assert wrong.status_code == 400
    assert wrong.json()["code"] == "WRONG_PASSWORD"
    assert right.status_code == 204
    refresh = await client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401
    await login(client, user.email, "brand-new-pass-2")
