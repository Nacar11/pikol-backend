from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import hash_password
from src.users.domain.groups import GroupKind
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel

if TYPE_CHECKING:
    from src.courts.persistence.models import CourtModel

AUTH = f"{API_PREFIX}/auth"


class RoleCode(StrEnum):
    """Test shorthand for the group a new user lands in (ADR 0003 retired
    stored roles): PLAYER → Customer, VENUE_MANAGER → the venue's master
    group, VENUE_STAFF → its Front desk, SUPER_ADMIN → no group."""

    SUPER_ADMIN = "SUPER_ADMIN"
    VENUE_MANAGER = "VENUE_MANAGER"
    VENUE_STAFF = "VENUE_STAFF"
    PLAYER = "PLAYER"


PASSWORD = "correct-horse-1"
_counter = 0


async def make_venue(session: AsyncSession, name: str = "Venue") -> VenueModel:
    venue = VenueModel(name=name)
    session.add(venue)
    await session.flush()
    await group_repository.seed_venue_groups(session, venue.id, created_by=None)
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
        is_active=active,
        email_verified_at=datetime.now(UTC) if verified else None,
        system_admin=system_admin,
    )
    session.add(user)
    await session.flush()
    # The group the role maps to (ADR 0003); SUPER_ADMIN is system_admin only.
    if role == RoleCode.PLAYER:
        group_id: int | None = await group_repository.customer_group_id(session)
    elif role in (RoleCode.VENUE_MANAGER, RoleCode.VENUE_STAFF) and venue_id is not None:
        kind = GroupKind.MASTER if role == RoleCode.VENUE_MANAGER else GroupKind.STAFF
        group_id = await group_repository.venue_group_id(session, venue_id, kind)
    else:
        group_id = None
    if group_id is not None:
        await group_repository.add_member(session, group_id, user.id, added_by=None)
    return user


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def make_court(
    session: AsyncSession, venue_id: int, name: str = "Court", *, sort_order: int = 0
) -> "CourtModel":
    from src.courts.persistence.models import CourtModel

    court = CourtModel(venue_id=venue_id, name=name, sort_order=sort_order)
    session.add(court)
    await session.flush()
    return court
