from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_player
from src.bookings.dto import BookingOut, HoldRequest
from src.bookings.service import booking_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

router = APIRouter(prefix=f"{API_PREFIX}/bookings", tags=["bookings"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_hold(
    body: HoldRequest,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> BookingOut:
    """Hold consecutive hours for hold_duration_minutes. Prices and fee are
    frozen now. Payment arrives in Phase 5; until then the hold expires."""
    group = await booking_service.create_hold(session, player, body)
    return BookingOut.of(group, datetime.now(UTC))
