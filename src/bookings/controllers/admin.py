from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.dto import ActionEventOut
from src.action_events.service import event_service
from src.auth.access import get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.bookings.domain.enums import BookingStatus, BookingType
from src.bookings.dto import (
    AdminBookingOut,
    DayOccupancy,
    MarkPaidRequest,
    OutcomeRequest,
    ResolveRequest,
    StaffCancelRequest,
    WalkInRequest,
)
from src.bookings.persistence.repository import booking_repository
from src.bookings.service import booking_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.errors import AppError
from src.utils.pagination import Page
from src.utils.time import manila_day_bounds

router = APIRouter(prefix=f"{API_PREFIX}/admin/bookings", tags=["admin: bookings"])

# Transitions are actions with one permission each, never a status PATCH
# (spec §7.3): illegal transitions have no URL at all.
read_any = require_permission("BOOKING:ReadAny")
create_walk_ins = require_permission("BOOKING:CreateWalkIn")
cancel_any = require_permission("BOOKING:CancelAny")
mark_paid_guard = require_permission("BOOKING:MarkPaid")
set_outcomes = require_permission("BOOKING:SetOutcome")


@router.get("")
async def list_bookings(
    venue_id: int | None = None,
    court_id: int | None = None,
    day: date | None = Query(None, alias="date", description="Manila-local day"),
    booking_status: BookingStatus | None = None,
    type: BookingType | None = None,
    ends_after: AwareDatetime | None = Query(
        None, description="Only groups with an hour ending after this instant"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    _: UserModel = Depends(read_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[AdminBookingOut]:
    groups, total = await booking_repository.list_scoped(
        session,
        scope=scope,
        page=page,
        limit=limit,
        venue_id=venue_id,
        court_id=court_id,
        between=manila_day_bounds(day) if day is not None else None,
        booking_status=booking_status,
        type=type,
        ends_after=ends_after,
    )
    now = datetime.now(UTC)
    return Page.of(
        [AdminBookingOut.of_admin(g, now) for g in groups], total=total, page=page, limit=limit
    )


@router.get("/unfulfilled")
async def unfulfilled(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    _: UserModel = Depends(read_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[AdminBookingOut]:
    """The resolution queue (spec §5.5): money with no court — a payment
    that landed after its slots went to someone else, or a double charge.
    Declared before /{booking_id} so "unfulfilled" is not read as an id."""
    groups, total = await booking_repository.list_scoped(
        session, scope=scope, page=page, limit=limit, needs_resolution=True
    )
    now = datetime.now(UTC)
    return Page.of(
        [AdminBookingOut.of_admin(g, now) for g in groups], total=total, page=page, limit=limit
    )


@router.get("/occupancy")
async def occupancy(
    start: date = Query(alias="from", description="First Manila day"),
    days: int = Query(14, ge=1, le=31),
    venue_id: int | None = Query(None, description="Required for the owner"),
    _: UserModel = Depends(read_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> list[DayOccupancy]:
    """Booked vs bookable court-hours per day, for the two-week strip.
    Declared before /{booking_id} so "occupancy" is not read as an id."""
    venue = venue_id if venue_id is not None else scope.venue_id
    if venue is None:
        raise AppError(422, "VENUE_REQUIRED", "Choose a venue with venue_id.")
    return await booking_service.daily_occupancy(session, venue, start, days, scope=scope)


@router.post("/walk-in", status_code=status.HTTP_201_CREATED)
async def create_walk_in(
    body: WalkInRequest,
    staff: UserModel = Depends(create_walk_ins),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    group = await booking_service.create_walk_in(session, staff, body, scope=scope)
    return AdminBookingOut.of_admin(group, datetime.now(UTC))


@router.get("/{booking_id}")
async def get_booking(
    booking_id: int,
    _: UserModel = Depends(read_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    group = await booking_service.get_scoped(session, booking_id, scope=scope)
    return AdminBookingOut.of_admin(group, datetime.now(UTC))


@router.get("/{booking_id}/events")
async def booking_events(
    booking_id: int,
    _: UserModel = Depends(read_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[ActionEventOut]:
    """What happened to this booking, newest first — its own events and its
    checkout's payment events, with derived "Play started" / "Hold ended"."""
    group = await booking_service.get_scoped(session, booking_id, scope=scope)
    events = await event_service.staff_timeline(session, group, datetime.now(UTC))
    return Page.of(events, total=len(events), page=1, limit=max(len(events), 1))


@router.post("/{booking_id}/cancel")
async def cancel_booking(
    booking_id: int,
    body: StaffCancelRequest,
    staff: UserModel = Depends(cancel_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    """No refund. A closure is reopened through /admin/closures instead."""
    group = await booking_service.cancel_by_staff(
        session, staff, booking_id, body.reason, scope=scope
    )
    return AdminBookingOut.of_admin(group, datetime.now(UTC))


@router.post("/{booking_id}/mark-paid")
async def mark_paid(
    booking_id: int,
    body: MarkPaidRequest,
    staff: UserModel = Depends(mark_paid_guard),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    group = await booking_service.mark_paid(
        session, staff, booking_id, body.payment_method, scope=scope
    )
    return AdminBookingOut.of_admin(group, datetime.now(UTC))


@router.post("/{booking_id}/outcome")
async def set_outcome(
    booking_id: int,
    body: OutcomeRequest,
    staff: UserModel = Depends(set_outcomes),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    group = await booking_service.set_outcome(session, staff, booking_id, body.outcome, scope=scope)
    return AdminBookingOut.of_admin(group, datetime.now(UTC))


@router.post("/{booking_id}/resolve")
async def resolve(
    booking_id: int,
    body: ResolveRequest,
    staff: UserModel = Depends(cancel_any),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> AdminBookingOut:
    group = await booking_service.resolve(session, staff, booking_id, body.resolution, scope=scope)
    return AdminBookingOut.of_admin(group, datetime.now(UTC))
