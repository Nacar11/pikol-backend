from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import VenueAccess, get_venue_access, require_permission
from src.database.session import get_session
from src.summaries.dto import CourtSummary, VenueSummary
from src.summaries.service import summary_service
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

# Included before the venues router: /venues/summary must not reach /venues/{venue_id}.
router = APIRouter(prefix=f"{API_PREFIX}/admin", tags=["admin: summaries"])

manage_venues = require_permission("VENUE:Manage")
manage_courts = require_permission("COURT:Manage")


@router.get("/venues/summary")
async def venues_summary(
    _: UserModel = Depends(manage_venues),
    access: VenueAccess = Depends(get_venue_access),
    session: AsyncSession = Depends(get_session),
) -> list[VenueSummary]:
    return await summary_service.venues(session, datetime.now(UTC), scope=access.scope)


@router.get("/venues/{venue_id}/courts/summary")
async def courts_summary(
    venue_id: int,
    _: UserModel = Depends(manage_courts),
    access: VenueAccess = Depends(get_venue_access),
    session: AsyncSession = Depends(get_session),
) -> list[CourtSummary]:
    return await summary_service.courts(session, venue_id, access, datetime.now(UTC))
