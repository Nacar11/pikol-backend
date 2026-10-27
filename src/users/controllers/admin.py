from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_scope, require_permission
from src.auth.dependencies import forbidden, require_super_admin
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender, send_quietly
from src.email.templates import invitation_email
from src.users.domain.roles import RoleCode
from src.users.dto import AdminInviteRequest, AdminUserUpdate, StaffInviteRequest, UserOut
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.users.service import Invitation, user_service
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

router = APIRouter(prefix=f"{API_PREFIX}/admin", tags=["admin: users"])

manage_users = require_permission("USER:Manage")


async def _send_invitation(sender: EmailSender, invitation: Invitation) -> None:
    user = invitation.user
    await send_quietly(
        sender,
        invitation_email(user.email, user.first_name, invitation.token, invitation.expiry_days),
    )


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def invite_any(
    body: AdminInviteRequest,
    _: UserModel = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> UserOut:
    """SUPER_ADMIN only: role and venue are parameters here because an
    admin choosing them is the legitimate case (spec §6.4)."""
    invitation = await user_service.invite(
        session,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        mobile=body.mobile,
        role=body.role,
        venue_id=body.venue_id,
    )
    await _send_invitation(sender, invitation)
    return UserOut.of(invitation.user)


@router.post("/venue-staff", status_code=status.HTTP_201_CREATED)
async def invite_staff(
    body: StaffInviteRequest,
    caller: UserModel = Depends(manage_users),
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> UserOut:
    """A manager inviting staff to THEIR OWN venue. Both privilege fields
    come from the server, never the body (spec §6.4)."""
    if caller.venue_id is None:
        raise forbidden()
    invitation = await user_service.invite(
        session,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        mobile=body.mobile,
        role=RoleCode.VENUE_STAFF,  # fixed by the endpoint
        venue_id=caller.venue_id,  # from the token
    )
    await _send_invitation(sender, invitation)
    return UserOut.of(invitation.user)


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: UserModel = Depends(manage_users),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> Page[UserOut]:
    users, total = await user_repository.list(session, scope=scope, page=page, limit=limit)
    return Page.of([UserOut.of(u) for u in users], total=total, page=page, limit=limit)


@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    _: UserModel = Depends(manage_users),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    return UserOut.of(await user_service.get_for_admin(session, user_id, scope=scope))


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    caller: UserModel = Depends(manage_users),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await user_service.update_by_admin(session, caller, user_id, body, scope=scope)
    return UserOut.of(user)


@router.post("/users/{user_id}/invitation", status_code=status.HTTP_202_ACCEPTED)
async def resend_invitation(
    user_id: int,
    caller: UserModel = Depends(manage_users),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> Response:
    invitation = await user_service.resend_invitation(session, caller, user_id, scope=scope)
    await _send_invitation(sender, invitation)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.delete("/users/{user_id}/invitation", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    user_id: int,
    caller: UserModel = Depends(manage_users),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    await user_service.revoke_invitation(session, caller, user_id, scope=scope)
