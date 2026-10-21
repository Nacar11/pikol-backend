"""Create identity tables, venues, and the seeded role-permission matrix

Revision ID: 0002_identity
Revises: 0001_parameters
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.users.domain.roles import PERMISSIONS, ROLE_PERMISSIONS, RoleCode

revision = "0002_identity"
down_revision = "0001_parameters"
branch_labels = None
depends_on = None

ROLE_NAMES = {
    RoleCode.SUPER_ADMIN: ("Super admin", "All venues. The owner."),
    RoleCode.VENUE_MANAGER: ("Venue manager", "One venue. May invite staff for it."),
    RoleCode.VENUE_STAFF: ("Venue staff", "One venue. Counter operations."),
    RoleCode.PLAYER: ("Player", "Self-service only."),
}

token_purpose = postgresql.ENUM(
    "EMAIL_VERIFICATION",
    "PASSWORD_RESET",
    "INVITATION",
    name="token_purpose",
    create_type=False,
)


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    token_purpose.create(op.get_bind(), checkfirst=True)

    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255)),
        *_timestamps(),
    )
    permissions = op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("resource", sa.String(50), nullable=False, index=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("description", sa.String(255)),
        *_timestamps(),
    )
    role_permissions = op.create_table(
        "role_permissions",
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "permission_id",
            sa.Integer(),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )

    # venues first, without its users FKs — the two tables reference each
    # other, so those are added once users exists.
    op.create_table(
        "venues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("address", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.Column("created_by_user_id", sa.Integer()),
        sa.Column("updated_by_user_id", sa.Integer()),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("mobile", sa.String(32)),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "venue_id", sa.Integer(), sa.ForeignKey("venues.id", ondelete="RESTRICT"), index=True
        ),
        sa.Column("system_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    # dbml Appendix A §1, verbatim.
    op.execute(
        "CREATE UNIQUE INDEX users_email_lower_uq ON users (LOWER(email)) WHERE deleted_at IS NULL"
    )

    op.create_foreign_key(
        "fk_venues_created_by_user_id", "venues", "users", ["created_by_user_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_venues_updated_by_user_id", "venues", "users", ["updated_by_user_id"], ["id"]
    )
    # Phase 1 left this column without a constraint because users did not exist.
    op.create_foreign_key(
        "parameters_updated_by_user_id_fkey",
        "parameters",
        "users",
        ["updated_by_user_id"],
        ["id"],
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("jti", sa.Uuid(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_refresh_tokens_user_id_revoked_at", "refresh_tokens", ["user_id", "revoked_at"]
    )

    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("purpose", token_purpose, nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_user_tokens_user_id_purpose", "user_tokens", ["user_id", "purpose"])

    # Seed from the Python constants so code and database cannot disagree.
    role_ids = {code: i for i, code in enumerate(RoleCode, start=1)}
    op.bulk_insert(
        roles,
        [
            {"id": role_ids[code], "code": code.value, "name": name, "description": description}
            for code, (name, description) in ROLE_NAMES.items()
        ],
    )
    permission_ids = {code: i for i, code in enumerate(PERMISSIONS, start=1)}
    op.bulk_insert(
        permissions,
        [
            {
                "id": permission_ids[code],
                "code": code,
                "resource": code.split(":")[0],
                "action": code.split(":")[1],
            }
            for code in PERMISSIONS
        ],
    )
    op.bulk_insert(
        role_permissions,
        [
            {"role_id": role_ids[role], "permission_id": permission_ids[code]}
            for role, codes in ROLE_PERMISSIONS.items()
            for code in sorted(codes)
        ],
    )
    # Explicit ids were inserted, so move the sequences past them.
    op.execute("SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles))")
    op.execute("SELECT setval('permissions_id_seq', (SELECT MAX(id) FROM permissions))")


def downgrade() -> None:
    op.drop_table("user_tokens")
    op.drop_table("refresh_tokens")
    op.drop_constraint("parameters_updated_by_user_id_fkey", "parameters", type_="foreignkey")
    op.drop_constraint("fk_venues_updated_by_user_id", "venues", type_="foreignkey")
    op.drop_constraint("fk_venues_created_by_user_id", "venues", type_="foreignkey")
    op.drop_table("users")
    op.drop_table("venues")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    token_purpose.drop(op.get_bind(), checkfirst=True)
