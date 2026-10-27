import re
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.persistence.models import UserTokenModel
from src.config.settings import get_settings
from src.email.adapters import MemoryEmailSender
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

AUTH = f"{API_PREFIX}/auth"
PLAYER = {
    "email": "jane@x.com",
    "password": "correct-horse-1",
    "first_name": "Jane",
    "last_name": "Doe",
}


def _token_from(outbox: MemoryEmailSender, path: str) -> str:
    match = re.search(rf"{re.escape(path)}\?token=([\w-]+)", outbox.outbox[-1].body)
    assert match, outbox.outbox[-1].body
    return match.group(1)


async def test_register_creates_inactive_player_and_emails_link(
    client: AsyncClient, outbox: MemoryEmailSender
) -> None:
    response = await client.post(f"{AUTH}/register", json=PLAYER)

    assert response.status_code == 201
    body = response.json()
    assert [g["kind"] for g in body["groups"]] == ["CUSTOMER"]
    assert body["is_active"] is False
    assert body["email_verified_at"] is None
    assert "password" not in body and "password_hash" not in body
    assert len(outbox.outbox) == 1
    assert outbox.outbox[0].to == "jane@x.com"
    assert f"{get_settings().frontend_base_url}/verify-email?token=" in outbox.outbox[0].body


async def test_register_rejects_case_variant_of_existing_email(client: AsyncClient) -> None:
    await client.post(f"{AUTH}/register", json=PLAYER)

    response = await client.post(f"{AUTH}/register", json={**PLAYER, "email": "JANE@x.com"})

    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_TAKEN"


async def test_register_forbids_role_field(client: AsyncClient) -> None:
    """Self-registration is players only (spec §1): a role in the body is
    rejected outright, not ignored."""
    response = await client.post(f"{AUTH}/register", json={**PLAYER, "role": "SUPER_ADMIN"})

    assert response.status_code == 422


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/register", json={**PLAYER, "password": "short"})

    assert response.status_code == 422


async def test_verify_email_activates_and_is_single_use(
    client: AsyncClient, outbox: MemoryEmailSender, db_session: AsyncSession
) -> None:
    await client.post(f"{AUTH}/register", json=PLAYER)
    token = _token_from(outbox, "/verify-email")

    first = await client.post(f"{AUTH}/verify-email", json={"token": token})
    second = await client.post(f"{AUTH}/verify-email", json={"token": token})

    assert first.status_code == 204
    user = (
        await db_session.execute(select(UserModel).where(UserModel.email == "jane@x.com"))
    ).scalar_one()
    assert user.is_active is True
    assert user.email_verified_at is not None
    assert second.status_code == 400
    assert second.json()["code"] == "INVALID_OR_EXPIRED_LINK"


async def test_expired_verification_link_rejected(
    client: AsyncClient, outbox: MemoryEmailSender, db_session: AsyncSession
) -> None:
    await client.post(f"{AUTH}/register", json=PLAYER)
    token = _token_from(outbox, "/verify-email")
    await db_session.execute(
        update(UserTokenModel).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    response = await client.post(f"{AUTH}/verify-email", json={"token": token})

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_OR_EXPIRED_LINK"


async def test_resend_invalidates_previous_link(
    client: AsyncClient, outbox: MemoryEmailSender
) -> None:
    await client.post(f"{AUTH}/register", json=PLAYER)
    old = _token_from(outbox, "/verify-email")

    resend = await client.post(f"{AUTH}/verify-email/resend", json={"email": "JANE@x.com"})
    new = _token_from(outbox, "/verify-email")

    assert resend.status_code == 202
    assert new != old
    assert (await client.post(f"{AUTH}/verify-email", json={"token": old})).status_code == 400
    assert (await client.post(f"{AUTH}/verify-email", json={"token": new})).status_code == 204


async def test_resend_hides_unknown_email(client: AsyncClient, outbox: MemoryEmailSender) -> None:
    """202 either way, so the endpoint cannot be used to test which emails
    have accounts."""
    response = await client.post(f"{AUTH}/verify-email/resend", json={"email": "nobody@x.com"})

    assert response.status_code == 202
    assert outbox.outbox == []
