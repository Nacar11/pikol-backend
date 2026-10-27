"""An address the strict validator rejects can still belong to an account
(created before the validator, or by tooling). Looking it up must work."""

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import GroupKind
from src.utils.api import API_PREFIX
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue, venue_group_id

RESERVED = f"lookup-{uuid4().hex[:8]}@e2e.test"


async def test_adds_an_existing_account_with_a_reserved_domain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    venue = await make_venue(db_session)
    owner = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    await make_user(db_session, email=RESERVED)
    master = await venue_group_id(db_session, venue.id, GroupKind.MASTER)

    response = await client.post(
        f"{API_PREFIX}/admin/user-groups/{master}/members",
        headers=bearer(await login(client, owner.email)),
        json={"email": f"  {RESERVED.upper()} "},
    )

    assert response.status_code == 201
    assert response.json()["email"] == RESERVED


async def test_signs_in_with_a_reserved_domain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_user(db_session, email=RESERVED)

    assert "access_token" in await login(client, RESERVED)


async def test_registration_still_rejects_a_reserved_domain(client: AsyncClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/register",
        json={
            "email": "new@e2e.test",
            "password": "a-long-password-1",
            "first_name": "A",
            "last_name": "B",
        },
    )

    assert response.status_code == 422
