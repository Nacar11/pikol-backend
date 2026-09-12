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


async def test_readiness_touches_the_database(client: AsyncClient) -> None:
    """Liveness and readiness are different questions, and here the
    difference is load-bearing.

    Spec §9.3 says the UptimeRobot ping stops Supabase pausing after 7 idle
    days. Supabase pauses on *database* inactivity, so a probe that returns a
    dict literal issues no query and does not keep it alive: Render stays
    awake, Postgres pauses anyway, and the first real request after a quiet
    week fails. The keep-alive must point HERE."""
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
