from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.bookings.dto import Availability
from src.bookings.service import booking_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

router = APIRouter(prefix=API_PREFIX, tags=["availability"])


@router.get("/availability")
async def availability(
    venue_id: int,
    day: date = Query(..., alias="date", description="Manila-local calendar day, YYYY-MM-DD"),
    _: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Availability:
    """Every active court × 24 hours, with status and resolved price. One
    component renders it for players and staff alike (spec §8.1)."""
    return await booking_service.availability(session, venue_id, day)
