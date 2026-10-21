import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.cli import create_super_admin
from src.users.domain.roles import PERMISSIONS
from src.utils.api import API_PREFIX
from src.utils.errors import AppError
from tests.auth.helpers import bearer, login


async def test_create_super_admin_has_every_permission(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_super_admin(
        db_session,
        email="owner@x.com",
        password="owner-pass-123",
        first_name="The",
        last_name="Owner",
    )

    tokens = await login(client, "owner@x.com", "owner-pass-123")
    response = await client.get(f"{API_PREFIX}/users/me/permissions", headers=bearer(tokens))

    assert user.system_admin is True
    assert response.json() == sorted(PERMISSIONS)


async def test_create_super_admin_refuses_existing_email(db_session: AsyncSession) -> None:
    kwargs = {"password": "owner-pass-123", "first_name": "The", "last_name": "Owner"}
    await create_super_admin(db_session, email="owner@x.com", **kwargs)

    with pytest.raises(AppError) as exc:
        await create_super_admin(db_session, email="OWNER@x.com", **kwargs)

    assert exc.value.code == "EMAIL_TAKEN"
