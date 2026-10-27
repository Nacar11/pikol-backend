"""Drop the retired roles now that groups carry every permission

Revision ID: 0007_drop_roles
Revises: 0006_user_groups

ADR 0003. The CONTRACT step: nothing reads roles, role_permissions,
users.role_id or users.venue_id any more. The downgrade rebuilds them from
group memberships, preferring master, then staff, then customer.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_drop_roles"
down_revision = "0006_user_groups"
branch_labels = None
depends_on = None

# Frozen: the role matrix as 0002 seeded it, for the downgrade to restore.
_ROLES = (
    (1, "SUPER_ADMIN", "Super admin", "All venues. The owner."),
    (2, "VENUE_MANAGER", "Venue manager", "One venue. May invite staff for it."),
    (3, "VENUE_STAFF", "Venue staff", "One venue. Counter operations."),
    (4, "PLAYER", "Player", "Self-service only."),
)
_BOOKING_OPS = (
    "BOOKING:ReadAny",
    "BOOKING:CreateWalkIn",
    "BOOKING:MarkPaid",
    "BOOKING:SetOutcome",
    "BOOKING:CancelAny",
)
_ROLE_CODES = {
    "VENUE_MANAGER": ("COURT:Manage", "PRICE:Manage", "CLOSURE:Manage", "USER:Manage")
    + _BOOKING_OPS,
    "VENUE_STAFF": ("CLOSURE:Manage",) + _BOOKING_OPS,
}


def upgrade() -> None:
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_index("ix_users_venue_id", table_name="users")
    op.drop_column("users", "role_id")
    op.drop_column("users", "venue_id")
    op.drop_table("role_permissions")
    op.drop_table("roles")


def downgrade() -> None:
    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
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
    op.bulk_insert(
        roles,
        [{"id": i, "code": c, "name": n, "description": d} for i, c, n, d in _ROLES],
    )
    op.execute("SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles))")
    for role, codes in _ROLE_CODES.items():
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r CROSS JOIN permissions p "
                "WHERE r.code = :role AND p.code = ANY(:codes)"
            ).bindparams(role=role, codes=list(codes))
        )

    op.add_column("users", sa.Column("role_id", sa.Integer()))
    op.add_column("users", sa.Column("venue_id", sa.Integer()))
    # One membership per user decides the old single role: master, then
    # staff, then customer. A person in several venues keeps the first.
    op.execute(
        """
        UPDATE users u SET role_id = r.id, venue_id = pick.venue_id
        FROM (
            SELECT DISTINCT ON (m.user_id) m.user_id, g.venue_id,
                   CASE g.kind WHEN 'MASTER' THEN 'VENUE_MANAGER'
                               WHEN 'STAFF' THEN 'VENUE_STAFF'
                               ELSE 'PLAYER' END AS code
            FROM user_group_members m JOIN user_groups g ON g.id = m.group_id
            ORDER BY m.user_id,
                     CASE g.kind WHEN 'MASTER' THEN 0 WHEN 'STAFF' THEN 1 ELSE 2 END,
                     g.venue_id
        ) pick
        JOIN roles r ON r.code = pick.code
        WHERE u.id = pick.user_id
        """
    )
    op.execute(
        "UPDATE users SET role_id = (SELECT id FROM roles WHERE code = 'SUPER_ADMIN'), "
        "venue_id = NULL WHERE system_admin"
    )
    op.execute(
        "UPDATE users SET role_id = (SELECT id FROM roles WHERE code = 'PLAYER') "
        "WHERE role_id IS NULL"
    )
    op.alter_column("users", "role_id", nullable=False)
    op.create_foreign_key(
        "users_role_id_fkey", "users", "roles", ["role_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_foreign_key(
        "users_venue_id_fkey", "users", "venues", ["venue_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_users_role_id", "users", ["role_id"])
    op.create_index("ix_users_venue_id", "users", ["venue_id"])
