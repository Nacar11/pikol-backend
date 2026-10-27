from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import forbidden, get_current_user
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.users.domain.groups import GroupKind
from src.users.domain.roles import PERMISSIONS
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel
from src.utils.errors import AppError


@dataclass(frozen=True)
class VenueAccess:
    """What the caller may do, and where — resolved once per request.

    Permissions are those of the caller's groups at the ACTIVE venue only,
    never summed across venues (ADR 0003): a master at A who is front desk
    at B holds front-desk codes while acting at B.
    """

    scope: VenueScope
    permissions: frozenset[str]
    is_master: bool


def venue_required() -> AppError:
    return AppError(
        400, "VENUE_REQUIRED", "You work at several venues. Choose one with X-Venue-ID."
    )


async def get_venue_access(
    x_venue_id: int | None = Header(None, alias="X-Venue-ID"),
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> VenueAccess:
    if user.system_admin:
        # The owner sees every venue and filters with ?venue_id=; the header
        # does not narrow the owner's scope.
        return VenueAccess(
            scope=VenueScope(all_venues=True, venue_id=None),
            permissions=frozenset(PERMISSIONS),
            is_master=True,
        )
    memberships = await group_repository.staff_memberships(session, user.id)
    venues = {venue_id for venue_id, _, _ in memberships}
    if x_venue_id is not None:
        # Venues are public through the catalog, so a 403 here confirms
        # nothing a 404 would hide.
        if x_venue_id not in venues:
            raise forbidden()
        venue_id = x_venue_id
    elif len(venues) == 1:
        (venue_id,) = venues
    elif venues:
        raise venue_required()
    else:
        raise forbidden()  # staff nowhere — a customer has no venue scope
    return VenueAccess(
        scope=VenueScope(all_venues=False, venue_id=venue_id),
        permissions=await group_repository.staff_codes(session, user.id, venue_id=venue_id),
        is_master=any(v == venue_id and k == GroupKind.MASTER for v, k, _ in memberships),
    )


def require_permission(code: str) -> Callable[..., Awaitable[UserModel]]:
    """Dependency factory: the caller must hold `code` AT THE ACTIVE VENUE,
    or be system_admin (which get_venue_access grants every code)."""

    async def guard(
        user: UserModel = Depends(get_current_user),
        access: VenueAccess = Depends(get_venue_access),
    ) -> UserModel:
        if code not in access.permissions:
            raise forbidden()
        return user

    return guard


async def get_venue_scope(access: VenueAccess = Depends(get_venue_access)) -> VenueScope:
    return access.scope


async def require_customer(
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserModel:
    """Booking and paying as yourself — the mobile app's routes. Granted by the
    Customer group, independent of any staff group (ADR 0003)."""
    if not await group_repository.is_customer(session, user.id):
        raise forbidden()
    return user
