import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.payments.adapters.mock import MockProvider
from tests.bookings.conftest import World, hold
from tests.bookings.helpers import next_hour
from tests.payments.helpers import WEBHOOK, pay


async def test_unknown_payment_is_ignored(client: AsyncClient, provider: MockProvider) -> None:
    response = await client.post(WEBHOOK, content=json.dumps({"id": "never-issued"}))

    assert response.status_code == 200


async def test_garbage_body_is_400(client: AsyncClient) -> None:
    assert (await client.post(WEBHOOK, content=b"not json")).status_code == 400


async def test_webhook_needs_no_token(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    response = await pay(client, db_session, provider, world.player, booking["checkout_id"])

    assert response.status_code == 200


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
