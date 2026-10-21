from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.scope import VenueScope
from src.courts.dto import CourtOut
from src.courts.persistence.repository import court_repository
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page
from src.venues.dto import VenueOut
from src.venues.persistence.repository import venue_repository
from src.venues.service import venue_service

# Any authenticated user — players included — may browse. Not public: the
# catalog sits behind login like everything else (spec §7.1).
router = APIRouter(prefix=f"{API_PREFIX}/venues", tags=["catalog"])

# The catalog spans every venue on purpose, and only ever shows active ones.
CATALOG = VenueScope.everything()


@router.get("")
async def list_venues(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    _: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Page[VenueOut]:
    venues, total = await venue_repository.list(
        session, scope=CATALOG, page=page, limit=limit, active_only=True
    )
    return Page.of([VenueOut.of(v) for v in venues], total=total, page=page, limit=limit)


@router.get("/{venue_id}/courts")
async def list_courts(
    venue_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=100),
    _: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Page[CourtOut]:
    # 404 for an inactive venue, not an empty list: a closed venue is not
    # bookable and must not look like one with no courts yet.
    await venue_service.get(session, venue_id, scope=CATALOG, active_only=True)
    courts, total = await court_repository.list_for_venue(
        session, venue_id, scope=CATALOG, page=page, limit=limit, active_only=True
    )
    return Page.of([CourtOut.of(c) for c in courts], total=total, page=page, limit=limit)
