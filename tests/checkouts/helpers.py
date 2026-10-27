from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.persistence.models import CheckoutModel
from src.utils.api import API_PREFIX
from tests.bookings.conftest import CHECKOUTS, World, manila_date


async def lapse(session: AsyncSession, checkout_id: int, minutes: int = 20) -> None:
    """End a hold `minutes` ago, on the checkout and every one of its groups."""
    past = datetime.now(UTC) - timedelta(minutes=minutes)
    await session.execute(
        update(CheckoutModel).where(CheckoutModel.id == checkout_id).values(expires_at=past)
    )
    await session.execute(
        update(BookingGroupModel)
        .where(
            BookingGroupModel.checkout_id == checkout_id, BookingGroupModel.expires_at.is_not(None)
        )
        .values(expires_at=past)
    )


async def action(
    client: AsyncClient, headers: dict[str, str], checkout_id: int, name: str
) -> Response:
    return await client.post(f"{CHECKOUTS}/{checkout_id}/{name}", headers=headers)


async def cell(client: AsyncClient, world: World, court_id: int, starts_at: datetime) -> str:
    """One court-hour's status on the customer's grid."""
    response = await client.get(
        f"{API_PREFIX}/availability",
        headers=world.player,
        params={"venue_id": world.a.venue.id, "date": manila_date(starts_at).isoformat()},
    )
    court = next(c for c in response.json()["courts"] if c["court_id"] == court_id)
    slot = next(s for s in court["slots"] if datetime.fromisoformat(s["starts_at"]) == starts_at)
    return str(slot["status"])


def item(court_id: int, starts_at: datetime, hours: int = 1) -> dict[str, Any]:
    return {"court_id": court_id, "starts_at": starts_at.isoformat(), "hours": hours}
