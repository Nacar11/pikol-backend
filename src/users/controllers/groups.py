from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.users.domain.groups import GroupKind
from src.users.dto import GroupCreate, GroupOut, GroupUpdate
from src.users.group_service import group_service
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

router = APIRouter(prefix=f"{API_PREFIX}/admin/user-groups", tags=["admin: user groups"])

# One resource behind both console pages (ADR 0003): Masters → User groups
# for the owner, Venue staff for a venue's masters.
manage_groups = require_permission("USER:Manage")


@router.get("")
async def list_groups(
    venue_id: int | None = Query(None, description="Owner only — narrows to one venue"),
    kind: GroupKind | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    caller: UserModel = Depends(manage_groups),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[GroupOut]:
    """The active venue's groups — master first, read-only for non-owners.
    The owner sees every venue's, and the Customer group."""
    groups, total = await group_repository.list_scoped(
        session, scope=scope, page=page, limit=limit, venue_id=venue_id, kind=kind
    )
    return Page.of(
        await group_service.out_many(session, caller, groups), total=total, page=page, limit=limit
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    caller: UserModel = Depends(manage_groups),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    group = await group_service.create(session, caller, body, scope=scope)
    return await group_service.out(session, caller, group)


@router.get("/{group_id}")
async def get_group(
    group_id: int,
    caller: UserModel = Depends(manage_groups),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    group = await group_service.get(session, group_id, scope=scope)
    return await group_service.out(session, caller, group)


@router.patch("/{group_id}")
async def rename_group(
    group_id: int,
    body: GroupUpdate,
    caller: UserModel = Depends(manage_groups),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    group = await group_service.rename(session, caller, group_id, body, scope=scope)
    return await group_service.out(session, caller, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    caller: UserModel = Depends(manage_groups),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    await group_service.delete(session, caller, group_id, scope=scope)
