from collections.abc import AsyncGenerator

import pytest
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.persistence.models import RefreshTokenModel
from src.database.session import get_session
from src.main import create_app
from src.users.domain.roles import RoleCode
from src.users.persistence.models import UserModel
from tests.auth.helpers import AUTH, PASSWORD, bearer, login, make_user


@pytest.fixture
async def probe(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """The app plus one route guarded only by get_current_user."""
    app = create_app()
    router = APIRouter()

    @router.get("/_whoami")
    async def whoami(user: UserModel = Depends(get_current_user)) -> dict[str, int]:
        return {"id": user.id}

    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _refresh(client: AsyncClient, token: str) -> int:
    response = await client.post(f"{AUTH}/refresh", json={"refresh_token": token})
    return response.status_code


async def test_login_returns_working_access_token(
    probe: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)

    tokens = await login(probe, user.email)
    response = await probe.get("/_whoami", headers=bearer(tokens))

    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 15 * 60
    assert response.status_code == 200
    assert response.json() == {"id": user.id}


async def test_missing_or_garbage_token_is_401(probe: AsyncClient) -> None:
    assert (await probe.get("/_whoami")).status_code == 401
    garbage = await probe.get("/_whoami", headers={"Authorization": "Bearer nope"})
    assert garbage.status_code == 401
    assert garbage.json()["code"] == "INVALID_TOKEN"


async def test_login_is_case_insensitive_on_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_user(db_session, email="jane@x.com")

    await login(client, "JANE@X.com")


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)

    wrong = await client.post(f"{AUTH}/login", json={"email": user.email, "password": "nope-nope"})
    unknown = await client.post(
        f"{AUTH}/login", json={"email": "nobody@x.com", "password": "nope-nope"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_unverified_player_gets_distinct_error_only_after_correct_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, verified=False, active=False)

    right = await client.post(f"{AUTH}/login", json={"email": user.email, "password": PASSWORD})
    wrong = await client.post(f"{AUTH}/login", json={"email": user.email, "password": "nope-nope"})

    assert right.status_code == 403
    assert right.json()["code"] == "EMAIL_NOT_VERIFIED"
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"


async def test_disabled_account_is_403(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session, active=False)

    response = await client.post(f"{AUTH}/login", json={"email": user.email, "password": PASSWORD})

    assert response.status_code == 403
    assert response.json()["code"] == "ACCOUNT_DISABLED"


async def test_invited_user_without_password_cannot_log_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(
        db_session, role=RoleCode.VENUE_STAFF, password=None, verified=False, active=False
    )

    response = await client.post(f"{AUTH}/login", json={"email": user.email, "password": PASSWORD})

    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_refresh_rotates_and_old_token_stops_working(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    tokens = await login(client, user.email)

    rotated = await client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != tokens["refresh_token"]
    assert await _refresh(client, rotated.json()["refresh_token"]) == 200


async def test_refresh_replay_revokes_the_whole_family(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Presenting a rotated-away token means it was stolen or replayed, and we
    cannot tell whether the thief or the victim holds the current one — so
    every session the user has dies (asima ADR 0002 §5)."""
    user = await make_user(db_session)
    phone = await login(client, user.email)
    laptop = await login(client, user.email)
    assert await _refresh(client, phone["refresh_token"]) == 200

    assert await _refresh(client, phone["refresh_token"]) == 401

    rows = (
        await db_session.execute(
            select(RefreshTokenModel).where(RefreshTokenModel.user_id == user.id)
        )
    ).scalars()
    assert all(row.revoked_at is not None for row in rows)
    assert await _refresh(client, laptop["refresh_token"]) == 401


async def test_token_types_are_not_interchangeable(
    probe: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    tokens = await login(probe, user.email)

    assert await _refresh(probe, tokens["access_token"]) == 401
    as_access = await probe.get(
        "/_whoami", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )
    assert as_access.status_code == 401


async def test_logout_revokes_all_refresh_tokens(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    phone = await login(client, user.email)
    laptop = await login(client, user.email)

    response = await client.post(f"{AUTH}/logout", headers=bearer(laptop))

    assert response.status_code == 204
    assert await _refresh(client, phone["refresh_token"]) == 401
    assert await _refresh(client, laptop["refresh_token"]) == 401


async def test_deactivated_user_rejected_on_next_request(
    probe: AsyncClient, db_session: AsyncSession
) -> None:
    """The user is loaded every request, so deactivation is immediate — the
    15-minute access token does not ride it out."""
    user = await make_user(db_session)
    tokens = await login(probe, user.email)

    user.is_active = False
    await db_session.flush()

    assert (await probe.get("/_whoami", headers=bearer(tokens))).status_code == 401
    assert await _refresh(probe, tokens["refresh_token"]) == 401
