from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from src.main import create_app
from src.utils.errors import AppError


async def test_app_error_renders_code_and_request_id() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/_boom")
    async def boom() -> None:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Verify your email first.")

    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_boom")

    assert response.status_code == 403
    body = response.json()
    assert set(body) == {"detail", "code", "request_id"}
    assert body["code"] == "EMAIL_NOT_VERIFIED"
    assert body["request_id"] == response.headers["X-Request-ID"]
