from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.courts.dto import CourtCreate, CourtOut, CourtUpdate
from src.courts.persistence.repository import court_repository
from src.courts.service import court_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page
from src.venues.service import venue_service

router = APIRouter(prefix=f"{API_PREFIX}/admin", tags=["admin: courts"])

manage_courts = require_permission("COURT:Manage")


@router.post("/venues/{venue_id}/courts", status_code=status.HTTP_201_CREATED)
async def create_court(
    venue_id: int,
    body: CourtCreate,
    caller: UserModel = Depends(manage_courts),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> CourtOut:
    return CourtOut.of(await court_service.create(session, caller, venue_id, body, scope=scope))


@router.get("/venues/{venue_id}/courts")
async def list_courts(
    venue_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=100),
    _: UserModel = Depends(manage_courts),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[CourtOut]:
    """Includes inactive courts — this is the management view."""
    await venue_service.get(session, venue_id, scope=scope, active_only=False)
    courts, total = await court_repository.list_for_venue(
        session, venue_id, scope=scope, page=page, limit=limit, active_only=False
    )
    return Page.of([CourtOut.of(c) for c in courts], total=total, page=page, limit=limit)


@router.get("/courts/{court_id}")
async def get_court(
    court_id: int,
    _: UserModel = Depends(manage_courts),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> CourtOut:
    return CourtOut.of(await court_service.get(session, court_id, scope=scope))


@router.patch("/courts/{court_id}")
async def update_court(
    court_id: int,
    body: CourtUpdate,
    caller: UserModel = Depends(manage_courts),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> CourtOut:
    return CourtOut.of(await court_service.update(session, caller, court_id, body, scope=scope))
