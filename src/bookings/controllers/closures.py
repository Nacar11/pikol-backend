from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.bookings.dto import AdminBookingOut, ClosureRequest, StaffCancelRequest
from src.bookings.service import booking_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

router = APIRouter(prefix=f"{API_PREFIX}/admin/closures", tags=["admin: closures"])

manage_closures = require_permission("CLOSURE:Manage")


@router.post("", status_code=status.HTTP_201_CREATED)
async def close_courts(
    body: ClosureRequest,
    staff: UserModel = Depends(manage_closures),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> list[AdminBookingOut]:
    """List closures with GET /admin/bookings?type=MAINTENANCE."""
    groups = await booking_service.close_courts(session, staff, body, scope=scope)
    now = datetime.now(UTC)
    return [AdminBookingOut.of_admin(g, now) for g in groups]


@router.post("/{closure_id}/cancel")
async def reopen(
    closure_id: int,
    body: StaffCancelRequest,
    staff: UserModel = Depends(manage_closures),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    group = await booking_service.reopen(session, staff, closure_id, body.reason, scope=scope)
    return AdminBookingOut.of_admin(group, datetime.now(UTC))
