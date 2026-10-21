"""Create booking_groups and booking_slots

Revision ID: 0004_bookings
Revises: 0003_courts_pricing
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_bookings"
down_revision = "0003_courts_pricing"
branch_labels = None
depends_on = None

booking_type = postgresql.ENUM(
    "PLAYER_BOOKING", "WALK_IN", "MAINTENANCE", name="booking_type", create_type=False
)
booking_status = postgresql.ENUM(
    "PENDING",
    "CONFIRMED",
    "COMPLETED",
    "NO_SHOW",
    "CANCELLED",
    "EXPIRED",
    name="booking_status",
    create_type=False,
)
payment_status = postgresql.ENUM(
    "UNPAID", "PAID", "FAILED", name="payment_status", create_type=False
)
payment_method = postgresql.ENUM(
    "ONLINE", "CASH", "MANUAL_GCASH", name="payment_method", create_type=False
)
ENUMS = (booking_type, booking_status, payment_status, payment_method)


def upgrade() -> None:
    for enum in ENUMS:
        enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "booking_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference_code", sa.String(12), nullable=False, unique=True),
        sa.Column(
            "court_id",
            sa.Integer(),
            sa.ForeignKey("courts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("type", booking_type, nullable=False),
        sa.Column("booking_status", booking_status, nullable=False),
        sa.Column("payment_status", payment_status, nullable=False),
        sa.Column("price_centavos", sa.Integer(), nullable=False),
        sa.Column("fee_centavos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_centavos", sa.Integer(), nullable=False),
        sa.Column("payment_method", payment_method),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "needs_resolution", sa.Boolean(), nullable=False, server_default=sa.false(), index=True
        ),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("cancellation_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_booking_groups_player_id_booking_status",
        "booking_groups",
        ["player_id", "booking_status"],
    )
    op.create_index(
        "ix_booking_groups_court_id_booking_status",
        "booking_groups",
        ["court_id", "booking_status"],
    )
    op.create_index(
        "ix_booking_groups_booking_status_expires_at",
        "booking_groups",
        ["booking_status", "expires_at"],
    )

    op.create_table(
        "booking_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "booking_group_id",
            sa.Integer(),
            sa.ForeignKey("booking_groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "court_id",
            sa.Integer(),
            sa.ForeignKey("courts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("price_centavos", sa.Integer(), nullable=False),
        sa.Column("booking_status", booking_status, nullable=False),
    )

    # dbml Appendix A, verbatim. §2 is the constraint the system rests on.
    op.execute(
        "CREATE UNIQUE INDEX uq_court_slot_active ON booking_slots (court_id, starts_at) "
        "WHERE booking_status NOT IN ('CANCELLED', 'EXPIRED')"
    )
    op.execute(
        "ALTER TABLE booking_slots ADD CONSTRAINT ck_slot_on_the_hour "
        "CHECK (EXTRACT(MINUTE FROM starts_at) = 0 AND EXTRACT(SECOND FROM starts_at) = 0)"
    )
    op.execute(
        "ALTER TABLE booking_slots ADD CONSTRAINT ck_slot_price_non_negative "
        "CHECK (price_centavos >= 0)"
    )
    op.execute(
        "ALTER TABLE booking_groups ADD CONSTRAINT ck_group_money_non_negative "
        "CHECK (price_centavos >= 0 AND fee_centavos >= 0 AND total_centavos >= 0)"
    )
    op.execute(
        "ALTER TABLE booking_groups ADD CONSTRAINT ck_pending_has_expiry "
        "CHECK ((booking_status = 'PENDING') = (expires_at IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE booking_groups ADD CONSTRAINT ck_cancellation_is_documented "
        "CHECK (cancelled_at IS NULL "
        "OR (cancelled_by_user_id IS NOT NULL AND cancellation_reason IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE booking_groups ADD CONSTRAINT ck_player_only_on_player_booking "
        "CHECK ((type = 'PLAYER_BOOKING') = (player_id IS NOT NULL))"
    )


def downgrade() -> None:
    op.drop_table("booking_slots")
    op.drop_table("booking_groups")
    for enum in reversed(ENUMS):
        enum.drop(op.get_bind(), checkfirst=True)
