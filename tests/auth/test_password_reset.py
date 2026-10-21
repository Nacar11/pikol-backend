import re

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.email.adapters import MemoryEmailSender
from src.users.domain.roles import RoleCode
from tests.auth.helpers import AUTH, PASSWORD, login, make_user, make_venue


def _reset_token(outbox: MemoryEmailSender) -> str:
    match = re.search(r"/reset-password\?token=([\w-]+)", outbox.outbox[-1].body)
    assert match
    return match.group(1)


async def test_forgot_password_is_silent_for_unknown_email(
    client: AsyncClient, outbox: MemoryEmailSender
) -> None:
    response = await client.post(f"{AUTH}/password/forgot", json={"email": "nobody@x.com"})

    assert response.status_code == 202
    assert outbox.outbox == []


async def test_forgot_password_ignores_pending_invitee(
    client: AsyncClient, db_session: AsyncSession, outbox: MemoryEmailSender
) -> None:
    """An invitee has no password. Letting them 'reset' one would be a back
    door around an invitation the manager has since revoked."""
    venue = await make_venue(db_session)
    invitee = await make_user(
        db_session,
        role=RoleCode.VENUE_STAFF,
        venue_id=venue.id,
        password=None,
        verified=False,
        active=False,
    )

    response = await client.post(f"{AUTH}/password/forgot", json={"email": invitee.email})

    assert response.status_code == 202
    assert outbox.outbox == []


async def test_reset_password_sets_new_password_and_is_single_use(
    client: AsyncClient, db_session: AsyncSession, outbox: MemoryEmailSender
) -> None:
    user = await make_user(db_session)
    old_session = await login(client, user.email)
    await client.post(f"{AUTH}/password/forgot", json={"email": user.email.upper()})
    token = _reset_token(outbox)
    assert f"{get_settings().frontend_base_url}/reset-password?token=" in outbox.outbox[-1].body

    first = await client.post(
        f"{AUTH}/password/reset", json={"token": token, "new_password": "brand-new-pass-2"}
    )
    second = await client.post(
        f"{AUTH}/password/reset", json={"token": token, "new_password": "another-pass-3"}
    )

    assert first.status_code == 204
    assert second.status_code == 400
    await login(client, user.email, "brand-new-pass-2")
    bad = await client.post(f"{AUTH}/login", json={"email": user.email, "password": PASSWORD})
    assert bad.status_code == 401
    refresh = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": old_session["refresh_token"]}
    )
    assert refresh.status_code == 401
