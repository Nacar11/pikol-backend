from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingGroupModel
from src.payments.adapters.mock import MockProvider
from src.payments.domain.port import ProviderError
from src.payments.persistence.models import PaymentModel
from tests.bookings.conftest import BOOKINGS, World, hold
from tests.bookings.helpers import next_hour


async def _payments(session: AsyncSession, booking_id: int) -> list[PaymentModel]:
    rows = await session.execute(
        select(PaymentModel)
        .where(PaymentModel.booking_group_id == booking_id)
        .order_by(PaymentModel.id)
    )
    return list(rows.scalars().all())


async def test_hold_returns_a_checkout(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour(), hours=2)

    payments = await _payments(db_session, booking["id"])
    assert booking["checkout_url"].endswith(
        f"/mock-maya/checkouts/{payments[0].provider_checkout_id}"
    )
    assert len(payments) == 1
    assert payments[0].status == "PENDING"
    assert payments[0].amount_centavos == booking["total_centavos"]
    stored = provider.describe(payments[0].provider_checkout_id)
    assert stored["requestReferenceNumber"] == booking["reference_code"]
    assert stored["totalAmount"] == {"value": "1030.00", "currency": "PHP"}  # ₱1,000 + 3%


async def test_hold_response_carries_names(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    assert booking["court_name"] == world.a.court.name
    assert booking["venue_name"] == world.a.venue.name


async def test_provider_outage_is_502_and_keeps_the_hold(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(**_: object) -> None:
        raise ProviderError("timeout")

    monkeypatch.setattr(provider, "create_checkout", down)

    response = await client.post(
        BOOKINGS,
        headers=world.player,
        json={"court_id": world.a.court.id, "starts_at": next_hour().isoformat()},
    )

    assert response.status_code == 502
    assert response.json()["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"
    held = (
        (
            await db_session.execute(
                select(BookingGroupModel).where(BookingGroupModel.court_id == world.a.court.id)
            )
        )
        .scalars()
        .all()
    )
    assert [g.booking_status for g in held] == ["PENDING"]


async def test_retry_creates_another_attempt(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    retry = await client.post(f"{BOOKINGS}/me/{booking['id']}/checkout", headers=world.player)

    assert retry.status_code == 200
    assert retry.json()["checkout_url"] != booking["checkout_url"]
    assert len(await _payments(db_session, booking["id"])) == 2


async def test_retry_on_lapsed_hold_is_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await db_session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == booking["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    retry = await client.post(f"{BOOKINGS}/me/{booking['id']}/checkout", headers=world.player)

    assert retry.status_code == 409
    assert retry.json()["code"] == "HOLD_NOT_LIVE"


async def test_retry_on_someone_elses_hold_is_404(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player2, world.a.court.id, next_hour())

    retry = await client.post(f"{BOOKINGS}/me/{booking['id']}/checkout", headers=world.player)

    assert retry.status_code == 404
