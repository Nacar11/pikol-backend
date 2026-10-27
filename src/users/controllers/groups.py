from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import VenueAccess, get_venue_access, get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.users.domain.groups import OWNER_ONLY, GroupKind
from src.users.dto import GroupCreate, GroupOut, GroupPermissionsUpdate, GroupUpdate, PermissionOut
from src.users.group_service import group_service
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

router = APIRouter(prefix=f"{API_PREFIX}/admin/user-groups", tags=["admin: user groups"])

permissions_router = APIRouter(
    prefix=f"{API_PREFIX}/admin/permissions", tags=["admin: user groups"]
)

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


@router.put("/{group_id}/permissions")
async def set_group_permissions(
    group_id: int,
    body: GroupPermissionsUpdate,
    caller: UserModel = Depends(manage_groups),
    access: VenueAccess = Depends(get_venue_access),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    """Replace the group's codes with the submitted checkbox state. A
    non-owner adds and removes only codes they hold at this venue; codes
    they do not hold stay as they were (ADR 0003)."""
    group = await group_service.set_permissions(session, caller, group_id, body, access=access)
    return await group_service.out(session, caller, group)


@permissions_router.get("")
async def list_permissions(
    _: UserModel = Depends(manage_groups),
    session: AsyncSession = Depends(get_session),
) -> list[PermissionOut]:
    """Every code, for the permission checkboxes. Owner-only codes are listed
    so the UI can show them disabled, never so they can be granted."""
    return [
        PermissionOut(
            code=p.code,
            resource=p.resource,
            action=p.action,
            description=p.description,
            owner_only=p.code in OWNER_ONLY,
        )
        for p in await group_repository.all_permissions(session)
    ]
