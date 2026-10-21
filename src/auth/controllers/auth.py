from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.dto import (
    AcceptInvitationRequest,
    EmailRequest,
    LoginRequest,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    TokenRequest,
)
from src.auth.service import auth_service
from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender, send_quietly
from src.email.templates import password_reset_email, verification_email
from src.users.dto import UserOut
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

router = APIRouter(prefix=f"{API_PREFIX}/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> UserOut:
    """Players only — staff are invited (spec §6.4). The account stays
    inactive until the emailed link is followed."""
    user, token = await auth_service.register(session, body)
    await send_quietly(sender, verification_email(user.email, user.first_name, token))
    return UserOut.of(user)


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: TokenRequest, session: AsyncSession = Depends(get_session)) -> None:
    await auth_service.verify_email(session, body.token)


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    body: EmailRequest,
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> Response:
    """Always 202, so it cannot be used to discover which emails exist."""
    issued = await auth_service.resend_verification(session, body.email)
    if issued is not None:
        user, token = issued
        await send_quietly(sender, verification_email(user.email, user.first_name, token))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/login")
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenPair:
    return await auth_service.login(session, body.email, body.password)


@router.post("/refresh")
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenPair:
    return await auth_service.refresh(session, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    user: UserModel = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> None:
    await auth_service.logout(session, user)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    body: EmailRequest,
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> Response:
    """Always 202, so it cannot be used to discover which emails exist."""
    issued = await auth_service.forgot_password(session, body.email)
    if issued is not None:
        user, token = issued
        await send_quietly(sender, password_reset_email(user.email, user.first_name, token))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    body: PasswordResetRequest, session: AsyncSession = Depends(get_session)
) -> None:
    await auth_service.reset_password(session, body.token, body.new_password)


@router.post("/invitations/accept", status_code=status.HTTP_204_NO_CONTENT)
async def accept_invitation(
    body: AcceptInvitationRequest, session: AsyncSession = Depends(get_session)
) -> None:
    await auth_service.accept_invitation(session, body.token, body.password)
