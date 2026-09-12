from httpx import AsyncClient


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_is_not_under_the_api_prefix(client: AsyncClient) -> None:
    """Health is infrastructure, not API — load balancers should not need to
    know the API version to probe liveness."""
    response = await client.get("/api/v1/health")

    assert response.status_code == 404
