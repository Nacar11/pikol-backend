import json

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.email.adapters import MemoryEmailSender
from src.payments.adapters.mock import MockProvider
from src.payments.persistence.models import PaymentModel, WebhookEventModel
from tests.bookings.conftest import BOOKINGS, World, hold
from tests.bookings.helpers import next_hour
from tests.payments.helpers import WEBHOOK, checkout_id, group, pay


async def _events(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(WebhookEventModel))).scalar_one()


async def test_success_confirms_and_sends_one_receipt(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour(), hours=2)

    response = await pay(client, db_session, provider, booking)

    g = await group(db_session, booking)
    assert response.status_code == 200
    assert (g.booking_status, g.payment_status, g.payment_method) == ("CONFIRMED", "PAID", "ONLINE")
    assert g.expires_at is None
    assert {s.booking_status for s in g.slots} == {"CONFIRMED"}
    assert g.needs_resolution is False
    receipts = [m for m in outbox.outbox if "receipt" in m.subject.lower()]
    assert len(receipts) == 1
    assert receipts[0].to == world.player_user.email
    assert booking["reference_code"] in receipts[0].body
    assert "₱1,030.00" in receipts[0].body


async def test_forged_success_confirms_nothing(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Maya does not sign webhooks. The body below LIES — the provider says
    the payment is still pending — so nothing may change (ADR 0002 §1)."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    forged = {
        "id": await checkout_id(db_session, booking),
        "paymentStatus": "PAYMENT_SUCCESS",
        "status": "PAYMENT_SUCCESS",
        "totalAmount": {"value": "515.00", "currency": "PHP"},
        "requestReferenceNumber": booking["reference_code"],
    }

    response = await client.post(WEBHOOK, content=json.dumps(forged))

    g = await group(db_session, booking)
    assert response.status_code == 200
    assert (g.booking_status, g.payment_status) == ("PENDING", "UNPAID")
    assert await _events(db_session) == 0


async def test_replay_is_free(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    first = await pay(client, db_session, provider, booking)
    body = provider.webhook_body(await checkout_id(db_session, booking))

    again = await client.post(WEBHOOK, content=body)

    assert first.status_code == again.status_code == 200
    assert await _events(db_session) == 1
    assert len([m for m in outbox.outbox if "receipt" in m.subject.lower()]) == 1


async def test_failure_marks_failed_and_a_retry_can_still_pay(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await pay(client, db_session, provider, booking, "PAYMENT_FAILED")
    failed = await group(db_session, booking)
    assert (failed.booking_status, failed.payment_status) == ("PENDING", "FAILED")

    retry = await client.post(f"{BOOKINGS}/me/{booking['id']}/checkout", headers=world.player)
    await pay(client, db_session, provider, booking)

    g = await group(db_session, booking)
    assert retry.status_code == 200
    assert (g.booking_status, g.payment_status) == ("CONFIRMED", "PAID")
    statuses = (
        (
            await db_session.execute(
                select(PaymentModel.status)
                .where(PaymentModel.booking_group_id == booking["id"])
                .order_by(PaymentModel.id)
            )
        )
        .scalars()
        .all()
    )
    assert statuses == ["FAILED", "PAID"]


async def test_amount_mismatch_is_flagged_not_confirmed(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await checkout_id(db_session, booking)
    provider._payments[payment_id]["totalAmount"]["value"] = "1.00"

    await pay(client, db_session, provider, booking)

    g = await group(db_session, booking)
    assert g.booking_status == "PENDING"
    assert g.needs_resolution is True


async def test_unknown_payment_is_ignored(client: AsyncClient, provider: MockProvider) -> None:
    response = await client.post(WEBHOOK, content=json.dumps({"id": "never-issued"}))

    assert response.status_code == 200


async def test_garbage_body_is_400(client: AsyncClient) -> None:
    assert (await client.post(WEBHOOK, content=b"not json")).status_code == 400


async def test_webhook_needs_no_token(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    assert (await pay(client, db_session, provider, booking)).status_code == 200


async def test_ip_allowlist(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, provider: MockProvider
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "maya_webhook_allowed_ips", ["13.229.160.234"])
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    body = json.dumps({"id": "never-issued"})

    stranger = await client.post(WEBHOOK, content=body, headers={"X-Forwarded-For": "6.6.6.6"})
    spoofed = await client.post(
        WEBHOOK, content=body, headers={"X-Forwarded-For": "13.229.160.234, 6.6.6.6"}
    )
    maya = await client.post(
        WEBHOOK, content=body, headers={"X-Forwarded-For": "1.2.3.4, 13.229.160.234"}
    )

    assert stranger.status_code == spoofed.status_code == 403
    assert maya.status_code == 200
