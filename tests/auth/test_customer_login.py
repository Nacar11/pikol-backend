"""POST /auth/customer-login — the customer demo's own door. Only Customer-group
members get a session; everyone else is refused before any token exists."""

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.persistence.models import RefreshTokenModel
from src.users.persistence.group_repository import group_repository
from tests.auth.helpers import AUTH, PASSWORD, RoleCode, make_user, make_venue

URL = f"{AUTH}/customer-login"


async def _post(client: AsyncClient, email: str, password: str = PASSWORD) -> tuple[int, str]:
    response = await client.post(URL, json={"email": email, "password": password})
    return response.status_code, response.json().get("code", "")


async def _refresh_rows(session: AsyncSession, user_id: int) -> int:
    count = await session.scalar(select(func.count()).where(RefreshTokenModel.user_id == user_id))
    return count or 0


async def test_customer_gets_a_token_pair(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)

    response = await client.post(URL, json={"email": user.email, "password": PASSWORD})

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert user.last_login_at is not None


async def test_staff_who_is_also_a_customer_gets_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    venue = await make_venue(db_session)
    user = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    customers = await group_repository.customer_group_id(db_session)
    await group_repository.add_member(db_session, customers, user.id, added_by=None)

    assert (await _post(client, user.email))[0] == 200


async def test_staff_only_is_refused_with_no_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    venue = await make_venue(db_session)
    user = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=venue.id)

    assert await _post(client, user.email) == (403, "NOT_A_CUSTOMER")
    assert await _refresh_rows(db_session, user.id) == 0
    assert user.last_login_at is None


async def test_super_admin_outside_the_customer_group_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)

    assert await _post(client, user.email) == (403, "NOT_A_CUSTOMER")
    assert await _refresh_rows(db_session, user.id) == 0


async def test_credentials_are_checked_before_customer_membership(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A wrong password must not reveal that the account is staff."""
    venue = await make_venue(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    unverified = await make_user(db_session, verified=False)

    assert await _post(client, staff.email, "nope-nope") == (401, "INVALID_CREDENTIALS")
    assert await _post(client, unverified.email) == (403, "EMAIL_NOT_VERIFIED")
