from datetime import datetime

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.persistence.models import CheckoutModel
from src.payments.adapters.mock import MockProvider
from src.payments.persistence.models import PaymentModel
from src.utils.api import API_PREFIX
from tests.bookings.conftest import CHECKOUTS

WEBHOOK = f"{API_PREFIX}/webhooks/maya"


async def open_maya(client: AsyncClient, headers: dict[str, str], checkout_id: int) -> str:
    """Continue to Maya; returns the mock payment id."""
    response = await client.post(f"{CHECKOUTS}/{checkout_id}/pay", headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["checkout_url"]).rsplit("/", 1)[1]


async def payment_id_of(session: AsyncSession, checkout_id: int) -> str | None:
    return (
        await session.execute(
            select(PaymentModel.provider_checkout_id).where(PaymentModel.checkout_id == checkout_id)
        )
    ).scalar_one_or_none()


async def pay(
    client: AsyncClient,
    session: AsyncSession,
    provider: MockProvider,
    headers: dict[str, str],
    checkout_id: int,
    maya_status: str = "PAYMENT_SUCCESS",
    *,
    paid_at: datetime | None = None,
) -> Response:
    """The customer finishes on Maya's page, then Maya notifies us. Opens
    Maya first if the customer never pressed Continue — so call it before
    lapsing a hold, or the /pay is refused."""
    payment_id = await payment_id_of(session, checkout_id)
    if payment_id is None:
        payment_id = await open_maya(client, headers, checkout_id)
    provider.settle(payment_id, maya_status, paid_at=paid_at)
    return await client.post(WEBHOOK, content=provider.webhook_body(payment_id))


async def checkout(session: AsyncSession, checkout_id: int) -> CheckoutModel:
    found = await session.get(CheckoutModel, checkout_id, populate_existing=True)
    assert found is not None
    return found


async def groups(session: AsyncSession, checkout_id: int) -> list[BookingGroupModel]:
    """The checkout's booking groups, freshly read with their slots."""
    rows = (
        (
            await session.execute(
                select(BookingGroupModel)
                .where(BookingGroupModel.checkout_id == checkout_id)
                .order_by(BookingGroupModel.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        for slot in row.slots:
            await session.refresh(slot)
    return list(rows)
