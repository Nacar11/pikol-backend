from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.main import create_app
from src.payments.adapters.mock import MockProvider
from src.utils.api import API_PREFIX
from tests.bookings.conftest import BOOKINGS, World, hold
from tests.bookings.helpers import next_hour
from tests.payments.helpers import checkout_id, group

MOCK = f"{API_PREFIX}/mock-maya/checkouts"


async def test_hosted_page_shows_the_amount(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    page = await client.get(f"{MOCK}/{await checkout_id(db_session, booking)}")

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "515.00" in page.text
    assert booking["reference_code"] in page.text


async def test_paying_on_the_mock_page_confirms_the_booking(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    paid = await client.post(f"{MOCK}/{await checkout_id(db_session, booking)}/success")

    g = await group(db_session, booking)
    assert paid.status_code == 303
    assert paid.headers["location"].endswith(f"/checkout/return?booking_id={booking['id']}")
    assert (g.booking_status, g.payment_status) == ("CONFIRMED", "PAID")


async def test_lost_webhook_is_recovered_by_refresh(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Every webhook attempt landed on a sleeping backend. The return page's
    refresh asks Maya and confirms the booking anyway (ADR 0002)."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await client.post(
        f"{MOCK}/{await checkout_id(db_session, booking)}/success",
        params={"deliver_webhook": "false"},
    )
    assert (await group(db_session, booking)).booking_status == "PENDING"

    refreshed = await client.post(
        f"{BOOKINGS}/me/{booking['id']}/refresh-payment", headers=world.player
    )

    assert refreshed.status_code == 200
    assert refreshed.json()["booking_status"] == "CONFIRMED"
    assert refreshed.json()["payment_status"] == "PAID"


async def test_refresh_before_paying_changes_nothing(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    refreshed = await client.post(
        f"{BOOKINGS}/me/{booking['id']}/refresh-payment", headers=world.player
    )

    assert refreshed.json()["booking_status"] == "PENDING"


async def test_cancel_on_the_mock_page_leaves_the_hold(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await client.post(f"{MOCK}/{await checkout_id(db_session, booking)}/cancel")

    g = await group(db_session, booking)
    assert (g.booking_status, g.payment_status) == ("PENDING", "UNPAID")


async def test_another_players_refresh_is_404(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player2, world.a.court.id, next_hour())

    response = await client.post(
        f"{BOOKINGS}/me/{booking['id']}/refresh-payment", headers=world.player
    )

    assert response.status_code == 404


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
