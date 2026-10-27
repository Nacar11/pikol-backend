from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.main import create_app
from src.payments.adapters.mock import MockProvider
from src.utils.api import API_PREFIX
from tests.bookings.conftest import World, hold
from tests.bookings.helpers import next_hour
from tests.payments.helpers import open_maya

MOCK = f"{API_PREFIX}/mock-maya/checkouts"


async def test_hosted_page_shows_the_amount(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])

    page = await client.get(f"{MOCK}/{payment_id}")

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "515.00" in page.text
    assert booking["checkout"]["reference_code"] in page.text


async def test_unknown_checkout_is_404(client: AsyncClient) -> None:
    assert (await client.get(f"{MOCK}/nope")).status_code == 404


async def test_mock_routes_absent_when_disabled(provider: MockProvider) -> None:
    """A deploy left on the mock must not be able to mark anything paid."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost:5432/pikol",
        jwt_secret="x" * 32,
        mock_checkout_enabled=False,
    )
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post(f"{MOCK}/anything/success")).status_code == 404
