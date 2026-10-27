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
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, deferred, mapped_column

import src.venues.persistence.models  # noqa: F401  (user_groups.venue_id FK target)
from src.database.base import Base
from src.users.domain.groups import GroupKind


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


# postgresql.ENUM with create_type=False — migration 0006 owns creation.
group_kind = ENUM(*GroupKind, name="group_kind", create_type=False)

user_group_permissions = Table(
    "user_group_permissions",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class UserGroupModel(Base):
    """A venue's MASTER or STAFF group, or the one venue-less CUSTOMER group
    (ADR 0003). Its CHECK and partial unique indexes live in migration 0006
    (dbml Appendix A §10–13)."""

    __tablename__ = "user_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[GroupKind] = mapped_column(group_kind, nullable=False)
    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("venues.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class UserGroupMemberModel(Base):
    __tablename__ = "user_group_members"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserModel(Base):
    """Who someone is. What they may do lives in user_group_members: Customer
    for booking as yourself, a venue's groups for staff work (ADR 0003)."""

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
    # The unconditional bypass axis, orthogonal to groups (asima ADR 0001).
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


# No unique on the column: uniqueness is LOWER(email) among non-deleted rows
# (dbml Appendix A §1). A plain UNIQUE would treat Jane@x.com and jane@x.com as
# two people and would permanently block re-registering a deleted address.
Index(
    "users_email_lower_uq",
    func.lower(UserModel.email),
    unique=True,
    postgresql_where=UserModel.deleted_at.is_(None),
)
