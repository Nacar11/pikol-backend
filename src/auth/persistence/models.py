import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class TokenPurpose(enum.StrEnum):
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"
    INVITATION = "INVITATION"


# postgresql.ENUM with create_type=False — the migration owns creation. See
# the comment on ParameterModel.value_type for why it is not sqlalchemy.Enum.
token_purpose = ENUM(*[p.value for p in TokenPurpose], name="token_purpose", create_type=False)


class RefreshTokenModel(Base):
    """The revocation ledger (asima ADR 0002).

    Stores no secret material — only the jti claim, its owner, and expiry. A
    leak of this table cannot be used to authenticate as anyone.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id_revoked_at", "user_id", "revoked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jti: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserTokenModel(Base):
    """Email verification, password reset, and staff invitation tokens.

    Single-use and hashed at rest: the raw token exists only inside the email.
    """

    __tablename__ = "user_tokens"
    __table_args__ = (Index("ix_user_tokens_user_id_purpose", "user_id", "purpose"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(token_purpose, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
