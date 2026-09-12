from httpx import ASGITransport, AsyncClient

from src.config.settings import Settings
from src.main import create_app


async def test_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers.get("x-request-id")


async def test_incoming_request_id_is_echoed(client: AsyncClient) -> None:
    """Propagating the caller's ID is what makes a frontend log line and a
    backend log line joinable."""
    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["x-request-id"] == "abc-123"


async def test_ids_differ_between_requests(client: AsyncClient) -> None:
    first = await client.get("/health")
    second = await client.get("/health")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_docs_are_hidden_when_not_debugging() -> None:
    """asima 404s /docs in production. The OpenAPI schema lists every admin
    route and its request shape — free reconnaissance."""
    production = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost:5432/pikol",
        debug=False,
    )
    transport = ASGITransport(app=create_app(production))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
