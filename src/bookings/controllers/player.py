from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_player
from src.bookings.dto import BookingOut, HoldRequest, PlayerCancelRequest
from src.bookings.persistence.repository import booking_repository
from src.bookings.service import booking_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

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


# Identity from the token, never the URL: /bookings/me, not /bookings/{player}.


@router.get("/me")
async def my_bookings(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> Page[BookingOut]:
    groups, total = await booking_repository.list_for_player(
        session, player.id, page=page, limit=limit
    )
    now = datetime.now(UTC)
    return Page.of([BookingOut.of(g, now) for g in groups], total=total, page=page, limit=limit)


@router.get("/me/{booking_id}")
async def my_booking(
    booking_id: int,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> BookingOut:
    """The checkout return page polls this (spec §8.2)."""
    group = await booking_service.get_for_player(session, player, booking_id)
    return BookingOut.of(group, datetime.now(UTC))


@router.post("/me/{booking_id}/cancel")
async def cancel_my_booking(
    booking_id: int,
    body: PlayerCancelRequest,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> BookingOut:
    group = await booking_service.cancel_by_player(session, player, booking_id, body.reason)
    return BookingOut.of(group, datetime.now(UTC))
