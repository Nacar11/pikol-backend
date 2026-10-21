from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dto import RegisterRequest
from src.auth.persistence.models import TokenPurpose, UserTokenModel
from src.auth.security import hash_opaque_token, hash_password, new_opaque_token
from src.users.domain.roles import RoleCode
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.utils.errors import AppError

EMAIL_CONSTRAINT = "users_email_lower_uq"

# Security windows, not business tuning — so constants, not parameters. The
# staff invitation lifetime IS a parameter (invitation_expiry_days).
VERIFICATION_TTL = timedelta(hours=24)
RESET_TTL = timedelta(minutes=60)


def email_taken() -> AppError:
    return AppError(409, "EMAIL_TAKEN", "An account with that email already exists.")


async def insert_user(session: AsyncSession, user: UserModel) -> UserModel:
    """Insert, turning the email-uniqueness violation into a 409.

    The index is the check — there is no SELECT-then-INSERT race to lose. The
    savepoint keeps the outer transaction usable after the violation.
    """
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:
        if EMAIL_CONSTRAINT in str(exc.orig):
            raise email_taken() from None
        raise
    await session.refresh(user, ["role"])
    return user


class AuthService:
    async def issue_token(
        self, session: AsyncSession, user: UserModel, purpose: TokenPurpose, ttl: timedelta
    ) -> str:
        """Issue a single-use email token; return the raw value for the email.

        Earlier unused tokens of the same purpose are deleted, so only the
        link in the most recent email works.
        """
        await session.execute(
            delete(UserTokenModel).where(
                UserTokenModel.user_id == user.id,
                UserTokenModel.purpose == purpose,
                UserTokenModel.used_at.is_(None),
            )
        )
        raw, hashed = new_opaque_token()
        session.add(
            UserTokenModel(
                user_id=user.id,
                purpose=purpose,
                token_hash=hashed,
                expires_at=datetime.now(UTC) + ttl,
            )
        )
        await session.flush()
        return raw

    async def consume_token(
        self, session: AsyncSession, raw: str, purpose: TokenPurpose
    ) -> UserModel:
        now = datetime.now(UTC)
        token = (
            await session.execute(
                select(UserTokenModel)
                .where(
                    UserTokenModel.token_hash == hash_opaque_token(raw),
                    UserTokenModel.purpose == purpose,
                    UserTokenModel.used_at.is_(None),
                    UserTokenModel.expires_at > now,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        user = None if token is None else await user_repository.get_by_id(session, token.user_id)
        if token is None or user is None:
            raise AppError(400, "INVALID_OR_EXPIRED_LINK", "This link is invalid or has expired.")
        token.used_at = now
        return user

    async def register(self, session: AsyncSession, body: RegisterRequest) -> tuple[UserModel, str]:
        """Create an inactive player and a verification token (spec §6.6)."""
        user = await insert_user(
            session,
            UserModel(
                email=body.email,
                password_hash=hash_password(body.password),
                first_name=body.first_name,
                last_name=body.last_name,
                mobile=body.mobile,
                role_id=await user_repository.role_id(session, RoleCode.PLAYER),
                is_active=False,
            ),
        )
        token = await self.issue_token(
            session, user, TokenPurpose.EMAIL_VERIFICATION, VERIFICATION_TTL
        )
        await session.commit()
        return user, token

    async def verify_email(self, session: AsyncSession, raw: str) -> None:
        user = await self.consume_token(session, raw, TokenPurpose.EMAIL_VERIFICATION)
        user.email_verified_at = datetime.now(UTC)
        user.is_active = True
        await session.commit()

    async def resend_verification(
        self, session: AsyncSession, email: str
    ) -> tuple[UserModel, str] | None:
        """A fresh token for an unverified account, or None — and the caller
        answers 202 either way."""
        user = await user_repository.get_by_email(session, email)
        if user is None or user.email_verified_at is not None:
            return None
        token = await self.issue_token(
            session, user, TokenPurpose.EMAIL_VERIFICATION, VERIFICATION_TTL
        )
        await session.commit()
        return user, token


auth_service = AuthService()
