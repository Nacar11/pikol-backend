import pytest
from httpx import AsyncClient

from src.config.settings import get_settings
from tests.auth.helpers import AUTH


async def test_login_is_rate_limited(client: AsyncClient) -> None:
    attempt = {"email": "nobody@x.com", "password": "guess-guess"}

    statuses = [(await client.post(f"{AUTH}/login", json=attempt)).status_code for _ in range(11)]

    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


async def test_rate_limit_uses_the_error_envelope(client: AsyncClient) -> None:
    for _ in range(5):
        await client.post(f"{AUTH}/password/forgot", json={"email": "a@x.com"})

    response = await client.post(f"{AUTH}/password/forgot", json={"email": "a@x.com"})

    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


async def test_spoofed_forwarded_for_does_not_reset_the_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind one proxy the client is the LAST X-Forwarded-For entry; a fresh
    fake entry in front of it must not buy a fresh bucket."""
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    attempt = {"email": "nobody@x.com", "password": "guess-guess"}

    statuses = [
        (
            await client.post(
                f"{AUTH}/login",
                json=attempt,
                headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
            )
        ).status_code
        for i in range(11)
    ]

    assert statuses[10] == 429


async def test_separate_clients_behind_the_proxy_get_separate_limits(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    attempt = {"email": "nobody@x.com", "password": "guess-guess"}
    for _ in range(10):
        await client.post(f"{AUTH}/login", json=attempt, headers={"X-Forwarded-For": "203.0.113.7"})

    other = await client.post(
        f"{AUTH}/login", json=attempt, headers={"X-Forwarded-For": "198.51.100.9"}
    )

    assert other.status_code == 401
