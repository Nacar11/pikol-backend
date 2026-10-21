from datetime import UTC, datetime
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import hash_password
from src.users.domain.roles import RoleCode
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel

AUTH = f"{API_PREFIX}/auth"
PASSWORD = "correct-horse-1"
_counter = 0


async def make_venue(session: AsyncSession, name: str = "Venue") -> VenueModel:
    venue = VenueModel(name=name)
    session.add(venue)
    await session.flush()
    return venue


async def make_user(
    session: AsyncSession,
    *,
    role: RoleCode = RoleCode.PLAYER,
    venue_id: int | None = None,
    email: str | None = None,
    password: str | None = PASSWORD,
    verified: bool = True,
    active: bool = True,
    system_admin: bool = False,
) -> UserModel:
    global _counter
    _counter += 1
    user = UserModel(
        email=email or f"user{_counter}@x.com",
        password_hash=hash_password(password) if password is not None else None,
        first_name="Test",
        last_name=f"User{_counter}",
        role_id=await user_repository.role_id(session, role),
        venue_id=venue_id,
        is_active=active,
        email_verified_at=datetime.now(UTC) if verified else None,
        system_admin=system_admin,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user, ["role"])
    return user


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
