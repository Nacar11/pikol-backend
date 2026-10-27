from fastapi import APIRouter, Depends, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_access
from src.auth.dependencies import get_current_user
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.users.domain.groups import GroupKind
from src.users.domain.roles import PERMISSIONS
from src.users.dto import MeOut, MyVenueOut, PasswordChangeRequest, ProfileUpdate
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel
from src.users.service import user_service
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel

# Identity comes from the token, never the URL — there is no /users/{id}
# here, so a caller cannot even name someone else (spec §3.5).
router = APIRouter(prefix=f"{API_PREFIX}/users/me", tags=["me"])


async def _me(session: AsyncSession, user: UserModel) -> MeOut:
    # Your own account: every one of your groups, at every venue.
    out = await user_service.out(session, user, scope=VenueScope.everything())
    return MeOut(
        **out.model_dump(), is_customer=await group_repository.is_customer(session, user.id)
    )


@router.get("")
async def get_me(
    user: UserModel = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> MeOut:
    return await _me(session, user)


@router.patch("")
async def update_me(
    body: ProfileUpdate,
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    return await _me(session, await user_service.update_profile(session, user, body))


@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revokes every refresh token; the client logs in again."""
    await user_service.change_password(session, user, body)


@router.get("/permissions")
async def my_permissions(
    x_venue_id: int | None = Header(None, alias="X-Venue-ID"),
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """What the frontend gates UI on — never a role parsed client-side.

    With X-Venue-ID: the codes held AT that venue, exactly what the guards
    check. Without it: the union across every venue, for coarse checks such
    as "is this person staff anywhere" — the server never authorises on it.
    """
    if user.system_admin:
        return sorted(PERMISSIONS)
    if x_venue_id is None:
        return sorted(await group_repository.staff_codes(session, user.id, venue_id=None))
    access = await get_venue_access(x_venue_id, user, session)
    return sorted(access.permissions)


@router.get("/venues")
async def my_venues(
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MyVenueOut]:
    """The venues the caller may act at, for the console's venue switcher:
    every venue for the owner, otherwise those holding one of their staff
    groups. A customer-only account gets an empty list."""
    if user.system_admin:
        venues = (await session.execute(select(VenueModel).order_by(VenueModel.name))).scalars()
        return [MyVenueOut(venue_id=v.id, name=v.name, is_master=True, groups=[]) for v in venues]
    by_venue: dict[int, MyVenueOut] = {}
    memberships = await group_repository.staff_memberships(session, user.id)
    names = dict(
        (
            await session.execute(
                select(VenueModel.id, VenueModel.name).where(
                    VenueModel.id.in_([v for v, _, _ in memberships])
                )
            )
        )
        .tuples()
        .all()
    )
    for venue_id, kind, group in memberships:
        entry = by_venue.setdefault(
            venue_id,
            MyVenueOut(venue_id=venue_id, name=names[venue_id], is_master=False, groups=[]),
        )
        entry.groups.append(group)
        entry.is_master = entry.is_master or kind == GroupKind.MASTER
    return sorted(by_venue.values(), key=lambda v: v.name)
