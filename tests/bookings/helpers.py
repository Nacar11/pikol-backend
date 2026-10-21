from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentStatus
from src.bookings.persistence.models import BookingGroupModel
from src.bookings.persistence.repository import booking_repository


def next_hour(days: int = 1, hour: int = 10) -> datetime:
    """A whole UTC hour `days` from now — comfortably inside every window."""
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return base.replace(hour=hour) + timedelta(days=days)


async def insert_booking(
    session: AsyncSession,
    *,
    court_id: int,
    created_by: int,
    starts_at: datetime,
    hours: int = 1,
    type: BookingType = BookingType.WALK_IN,
    status: BookingStatus = BookingStatus.CONFIRMED,
    player_id: int | None = None,
    expires_at: datetime | None = None,
    price: int = 50000,
) -> BookingGroupModel:
    fields: dict[str, Any] = {
        "court_id": court_id,
        "player_id": player_id,
        "type": type,
        "booking_status": status,
        "payment_status": PaymentStatus.UNPAID,
        "price_centavos": price * hours,
        "fee_centavos": 0,
        "total_centavos": price * hours,
        "expires_at": expires_at,
        "created_by_user_id": created_by,
    }
    slots = [(starts_at + timedelta(hours=h), price) for h in range(hours)]
    return await booking_repository.insert(session, fields=fields, slot_prices=slots)
