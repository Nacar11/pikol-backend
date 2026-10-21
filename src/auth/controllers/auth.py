from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dto import EmailRequest, RegisterRequest, TokenRequest
from src.auth.service import auth_service
from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender, send_quietly
from src.email.templates import verification_email
from src.users.dto import UserOut
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
