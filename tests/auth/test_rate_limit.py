from httpx import AsyncClient

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
