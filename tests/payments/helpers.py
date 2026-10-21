from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingGroupModel
from src.payments.adapters.mock import MockProvider
from src.payments.persistence.models import PaymentModel
from src.utils.api import API_PREFIX

WEBHOOK = f"{API_PREFIX}/webhooks/maya"


async def checkout_id(session: AsyncSession, booking: dict[str, Any]) -> str:
    """The latest checkout opened for a booking."""
    return (
        await session.execute(
            select(PaymentModel.provider_checkout_id)
            .where(PaymentModel.booking_group_id == booking["id"])
            .order_by(PaymentModel.id.desc())
            .limit(1)
        )
    ).scalar_one()


async def pay(
    client: AsyncClient,
    session: AsyncSession,
    provider: MockProvider,
    booking: dict[str, Any],
    maya_status: str = "PAYMENT_SUCCESS",
) -> Response:
    """The payer finishes on Maya's page, then Maya notifies us."""
    payment_id = await checkout_id(session, booking)
    provider.settle(payment_id, maya_status)
    return await client.post(WEBHOOK, content=provider.webhook_body(payment_id))


async def group(session: AsyncSession, booking: dict[str, Any]) -> BookingGroupModel:
    found = (
        await session.execute(
            select(BookingGroupModel).where(BookingGroupModel.id == booking["id"])
        )
    ).scalar_one()
    await session.refresh(found)
    for slot in found.slots:
        await session.refresh(slot)
    return found
