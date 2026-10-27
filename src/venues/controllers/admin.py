from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page
from src.venues.dto import VenueCreate, VenueOut, VenueUpdate
from src.venues.persistence.repository import venue_repository
from src.venues.service import venue_service

router = APIRouter(prefix=f"{API_PREFIX}/admin/venues", tags=["admin: venues"])

manage_venues = require_permission("VENUE:Manage")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_venue(
    body: VenueCreate,
    caller: UserModel = Depends(manage_venues),
    session: AsyncSession = Depends(get_session),
) -> VenueOut:
    return VenueOut.of(await venue_service.create(session, caller, body))


@router.get("")
async def list_venues(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: UserModel = Depends(manage_venues),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[VenueOut]:
    venues, total = await venue_repository.list(
        session, scope=scope, page=page, limit=limit, active_only=False
    )
    return Page.of([VenueOut.of(v) for v in venues], total=total, page=page, limit=limit)


@router.get("/{venue_id}")
async def get_venue(
    venue_id: int,
    _: UserModel = Depends(manage_venues),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> VenueOut:
    return VenueOut.of(await venue_service.get(session, venue_id, scope=scope, active_only=False))


@router.patch("/{venue_id}")
async def update_venue(
    venue_id: int,
    body: VenueUpdate,
    caller: UserModel = Depends(manage_venues),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> VenueOut:
    """Deactivate with is_active=false. Venues are never deleted — bookings
    reference their courts."""
    return VenueOut.of(await venue_service.update(session, caller, venue_id, body, scope=scope))
