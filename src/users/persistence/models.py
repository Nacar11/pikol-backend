from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    false,
    func,
)
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

import src.venues.persistence.models  # noqa: F401  (users.venue_id FK target)
from src.database.base import Base

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PermissionModel(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    resource: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserModel(Base):
    """A player is role = PLAYER, not a separate identity system (spec §6.1)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Deferred: an ordinary read never fetches the hash, so it cannot leak
    # into a response or a log by accident. NULL until an invited staff
    # member sets their own.
    password_hash: Mapped[str | None] = deferred(mapped_column(String(255)))
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    mobile: Mapped[str | None] = mapped_column(String(32))
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # NULL for SUPER_ADMIN and PLAYER. The entire basis of VenueScope.
    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("venues.id", ondelete="RESTRICT"), index=True
    )
    # The unconditional bypass axis, orthogonal to role (asima ADR 0001).
    system_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), index=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[RoleModel] = relationship(lazy="joined")


# No unique on the column: uniqueness is LOWER(email) among non-deleted rows
# (dbml Appendix A §1). A plain UNIQUE would treat Jane@x.com and jane@x.com as
# two people and would permanently block re-registering a deleted address.
Index(
    "users_email_lower_uq",
    func.lower(UserModel.email),
    unique=True,
    postgresql_where=UserModel.deleted_at.is_(None),
)
