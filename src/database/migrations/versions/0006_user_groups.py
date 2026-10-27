"""Add venue user groups beside the retired roles, and backfill them

Revision ID: 0006_user_groups
Revises: 0005_payments

ADR 0003. This is the EXPAND step: roles, role_permissions, users.role_id and
users.venue_id stay until 0007 drops them, so every commit in between runs.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_user_groups"
down_revision = "0005_payments"
branch_labels = None
depends_on = None

# Frozen copies of src/users/domain/groups.py as written: a migration must not
# change meaning when the live defaults are edited later.
CUSTOMER_GROUP_NAME = "Customer"
MASTER_GROUP_NAME = "Venue master"
FRONT_DESK_GROUP_NAME = "Front desk"
_BOOKING_OPS = frozenset(
    {
        "BOOKING:ReadAny",
        "BOOKING:CreateWalkIn",
        "BOOKING:MarkPaid",
        "BOOKING:SetOutcome",
        "BOOKING:CancelAny",
    }
)
MASTER_DEFAULT_CODES = frozenset(
    {"COURT:Manage", "PRICE:Manage", "CLOSURE:Manage", "USER:Manage"} | _BOOKING_OPS
)
FRONT_DESK_CODES = frozenset({"CLOSURE:Manage"} | _BOOKING_OPS)

group_kind = postgresql.ENUM("CUSTOMER", "MASTER", "STAFF", name="group_kind", create_type=False)


def _grant(kind: str, codes: frozenset[str]) -> None:
    """Give every group of `kind` the seeded codes."""
    op.execute(
        sa.text(
            "INSERT INTO user_group_permissions (group_id, permission_id) "
            "SELECT g.id, p.id FROM user_groups g CROSS JOIN permissions p "
            "WHERE g.kind = CAST(:kind AS group_kind) AND p.code = ANY(:codes)"
        ).bindparams(kind=kind, codes=sorted(codes))
    )


def _enrol(role: str, kind: str) -> None:
    """Move every user of `role` into their venue's group of `kind`."""
    op.execute(
        sa.text(
            "INSERT INTO user_group_members (group_id, user_id) "
            "SELECT g.id, u.id FROM users u "
            "JOIN roles r ON r.id = u.role_id "
            "JOIN user_groups g ON g.venue_id = u.venue_id AND g.kind = CAST(:kind AS group_kind) "
            "WHERE r.code = :role"
        ).bindparams(role=role, kind=kind)
    )


def upgrade() -> None:
    group_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "user_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", group_kind, nullable=False),
        sa.Column(
            "venue_id", sa.Integer(), sa.ForeignKey("venues.id", ondelete="RESTRICT"), index=True
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
    )
    op.create_table(
        "user_group_permissions",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("user_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            sa.Integer(),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )
    op.create_table(
        "user_group_members",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("user_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
        sa.Column("added_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # dbml Appendix A §10–13, verbatim.
    op.execute(
        "ALTER TABLE user_groups ADD CONSTRAINT ck_group_venue_matches_kind "
        "CHECK ((kind = 'CUSTOMER') = (venue_id IS NULL))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_user_groups_one_customer ON user_groups (kind) "
        "WHERE kind = 'CUSTOMER'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_user_groups_one_master_per_venue ON user_groups (venue_id) "
        "WHERE kind = 'MASTER'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_user_groups_venue_name ON user_groups (venue_id, LOWER(name)) "
        "WHERE venue_id IS NOT NULL"
    )

    # Seeds (dbml Appendix B): the one Customer group, then every existing
    # venue's master and front desk.
    op.execute(
        sa.text("INSERT INTO user_groups (kind, name) VALUES ('CUSTOMER', :name)").bindparams(
            name=CUSTOMER_GROUP_NAME
        )
    )
    for kind, name in (("MASTER", MASTER_GROUP_NAME), ("STAFF", FRONT_DESK_GROUP_NAME)):
        op.execute(
            sa.text(
                "INSERT INTO user_groups (kind, venue_id, name) "
                "SELECT CAST(:kind AS group_kind), v.id, :name FROM venues v"
            ).bindparams(kind=kind, name=name)
        )
    _grant("MASTER", MASTER_DEFAULT_CODES)
    _grant("STAFF", FRONT_DESK_CODES)
    backfill_members()


def backfill_members() -> None:
    """Move every user into the group their retired role maps to. SUPER_ADMIN
    maps to nothing: it is users.system_admin, never a group. Separate so
    the test can run it against seeded rows."""
    op.execute(
        "INSERT INTO user_group_members (group_id, user_id) "
        "SELECT g.id, u.id FROM users u JOIN roles r ON r.id = u.role_id "
        "CROSS JOIN user_groups g WHERE r.code = 'PLAYER' AND g.kind = 'CUSTOMER'"
    )
    _enrol("VENUE_MANAGER", "MASTER")
    _enrol("VENUE_STAFF", "STAFF")


def downgrade() -> None:
    # The role columns were never touched, so dropping the new tables is a
    # complete reversal.
    op.drop_table("user_group_members")
    op.drop_table("user_group_permissions")
    op.drop_table("user_groups")
    group_kind.drop(op.get_bind(), checkfirst=True)
