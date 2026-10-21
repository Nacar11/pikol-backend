from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dto import RegisterRequest, TokenPair
from src.auth.persistence.models import RefreshTokenModel, TokenPurpose, UserTokenModel
from src.auth.security import (
    decode,
    encode_access,
    encode_refresh,
    hash_opaque_token,
    hash_password,
    invalid_token,
    new_opaque_token,
    verify_password,
)
from src.config.settings import get_settings
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

    async def issue_pair(self, session: AsyncSession, user: UserModel) -> TokenPair:
        """Mint an access + refresh pair and record the refresh jti."""
        jti = uuid4()
        refresh_token, expires_at = encode_refresh(user.id, jti)
        session.add(RefreshTokenModel(user_id=user.id, jti=jti, expires_at=expires_at))
        await session.flush()
        return TokenPair(
            access_token=encode_access(user.id),
            refresh_token=refresh_token,
            expires_in=get_settings().access_token_minutes * 60,
        )

    async def revoke_all(self, session: AsyncSession, user_id: int) -> None:
        await session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.user_id == user_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    async def login(self, session: AsyncSession, email: str, password: str) -> TokenPair:
        """Check order matters: account state is revealed only AFTER a correct
        password, so the distinct errors cannot be used to probe accounts."""
        user = await user_repository.get_by_email(session, email)
        hashed = None
        if user is not None:
            await session.refresh(user, ["password_hash"])
            hashed = user.password_hash
        # Always verify — against a dummy hash when there is no user — so an
        # unknown email costs the same time as a wrong password.
        if not verify_password(password, hashed) or user is None:
            raise AppError(401, "INVALID_CREDENTIALS", "Incorrect email or password.")
        if user.email_verified_at is None:
            raise AppError(403, "EMAIL_NOT_VERIFIED", "Verify your email before logging in.")
        if not user.is_active:
            raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")

        user.last_login_at = datetime.now(UTC)
        pair = await self.issue_pair(session, user)
        await session.commit()
        return pair

    async def refresh(self, session: AsyncSession, raw: str) -> TokenPair:
        """Rotate: revoke the presented jti and issue a new pair (asima ADR 0002).

        The conditional UPDATE is the rotation AND the race guard — two
        concurrent refreshes of one token cannot both win it.
        """
        claims = decode(raw, "refresh")
        try:
            jti = UUID(claims["jti"])
        except (KeyError, ValueError):
            raise invalid_token() from None
        now = datetime.now(UTC)

        user_id = (
            await session.execute(
                update(RefreshTokenModel)
                .where(
                    RefreshTokenModel.jti == jti,
                    RefreshTokenModel.revoked_at.is_(None),
                    RefreshTokenModel.expires_at > now,
                )
                .values(revoked_at=now)
                .returning(RefreshTokenModel.user_id)
            )
        ).scalar_one_or_none()

        if user_id is None:
            row = (
                await session.execute(select(RefreshTokenModel).where(RefreshTokenModel.jti == jti))
            ).scalar_one_or_none()
            if row is not None and row.revoked_at is not None:
                # Replay of a rotated-away token: kill the whole family.
                await self.revoke_all(session, row.user_id)
                await session.commit()
            raise invalid_token()

        user = await user_repository.get_by_id(session, user_id)
        if user is None or not user.is_active:
            await session.commit()  # keep the rotation's revocation
            raise invalid_token()

        pair = await self.issue_pair(session, user)
        await session.commit()
        return pair

    async def logout(self, session: AsyncSession, user: UserModel) -> None:
        """Revokes every refresh token — logout ends all devices."""
        await self.revoke_all(session, user.id)
        await session.commit()

    async def forgot_password(
        self, session: AsyncSession, email: str
    ) -> tuple[UserModel, str] | None:
        """A reset token, or None — the caller answers 202 either way.

        Only for accounts that HAVE a password: an invitee has none, and
        letting them set one here would bypass a revoked invitation.
        """
        user = await user_repository.get_by_email(session, email)
        if user is None:
            return None
        await session.refresh(user, ["password_hash"])
        if user.password_hash is None:
            return None
        token = await self.issue_token(session, user, TokenPurpose.PASSWORD_RESET, RESET_TTL)
        await session.commit()
        return user, token

    async def reset_password(self, session: AsyncSession, raw: str, new_password: str) -> None:
        user = await self.consume_token(session, raw, TokenPurpose.PASSWORD_RESET)
        user.password_hash = hash_password(new_password)
        # Whoever held the old password may hold a session too.
        await self.revoke_all(session, user.id)
        await session.commit()


auth_service = AuthService()
