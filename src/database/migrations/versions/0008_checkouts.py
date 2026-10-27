"""Checkouts, refunds and the five payment outcomes (ADR 0004)

Revision ID: 0008_checkouts
Revises: 0007_drop_roles

A checkout is the unit of payment: one per "Pay now", one venue, several
booking groups. Existing player bookings each get a checkout of their own so
the new CHECK holds for old rows too. Every value below is a frozen literal.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_checkouts"
down_revision = "0007_drop_roles"
branch_labels = None
depends_on = None

payment_outcome = postgresql.ENUM(
    "PENDING",
    "SUCCESSFUL",
    "FAILED",
    "CANCELLED",
    "REFUNDED",
    name="payment_outcome",
    create_type=False,
)
refund_kind = postgresql.ENUM("VOID", "REFUND", name="refund_kind", create_type=False)
provider_payment_status = postgresql.ENUM(
    "PENDING",
    "PAID",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
    name="provider_payment_status",
    create_type=False,
)

LEAD_BEFORE = (
    "Earliest a player may book before a slot starts. Must be >= "
    "hold_duration_minutes, or a hold can outlive the slot it holds."
)
LEAD_AFTER = (
    "Earliest a player may book before a slot starts. May be 0; an hour whose "
    "start has passed can never be held."
)


def upgrade() -> None:
    bind = op.get_bind()
    payment_outcome.create(bind, checkfirst=True)
    refund_kind.create(bind, checkfirst=True)

    op.create_table(
        "checkouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference_code", sa.String(12), nullable=False, unique=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "venue_id",
            sa.Integer(),
            sa.ForeignKey("venues.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", payment_outcome, nullable=False),
        sa.Column("price_centavos", sa.Integer(), nullable=False),
        sa.Column("fee_centavos", sa.Integer(), nullable=False),
        sa.Column("total_centavos", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "price_centavos >= 0 AND fee_centavos >= 0 "
            "AND total_centavos = price_centavos + fee_centavos",
            name="ck_checkout_money",
        ),
    )
    op.create_index("ix_checkouts_customer_id_status", "checkouts", ["customer_id", "status"])
    op.create_index("ix_checkouts_status_expires_at", "checkouts", ["status", "expires_at"])

    op.add_column(
        "booking_groups",
        sa.Column("checkout_id", sa.Integer(), sa.ForeignKey("checkouts.id", ondelete="RESTRICT")),
    )
    op.create_index("ix_booking_groups_checkout_id", "booking_groups", ["checkout_id"])

    # One checkout per existing player booking, under the group's own code.
    op.execute(
        """
        INSERT INTO checkouts (reference_code, customer_id, venue_id, status,
                               price_centavos, fee_centavos, total_centavos,
                               expires_at, created_at)
        SELECT bg.reference_code, bg.player_id, c.venue_id,
               CASE
                 WHEN bg.payment_status = 'PAID' AND NOT bg.needs_resolution
                   THEN 'SUCCESSFUL'
                 WHEN bg.booking_status = 'PENDING' THEN 'PENDING'
                 WHEN bg.payment_status = 'FAILED' THEN 'FAILED'
                 ELSE 'CANCELLED'
               END::payment_outcome,
               bg.price_centavos, bg.fee_centavos, bg.total_centavos,
               COALESCE(bg.expires_at, bg.created_at), bg.created_at
          FROM booking_groups bg
          JOIN courts c ON c.id = bg.court_id
         WHERE bg.type = 'PLAYER_BOOKING'
        """
    )
    op.execute(
        """
        UPDATE booking_groups bg SET checkout_id = ck.id
          FROM checkouts ck
         WHERE ck.reference_code = bg.reference_code AND bg.type = 'PLAYER_BOOKING'
        """
    )
    op.create_check_constraint(
        "ck_checkout_only_on_player_booking",
        "booking_groups",
        "(type = 'PLAYER_BOOKING') = (checkout_id IS NOT NULL)",
    )

    op.add_column(
        "payments",
        sa.Column("checkout_id", sa.Integer(), sa.ForeignKey("checkouts.id", ondelete="CASCADE")),
    )
    op.add_column("payments", sa.Column("provider_status", sa.String(32)))
    op.add_column("payments", sa.Column("checkout_url", sa.Text(), server_default=""))
    op.execute(
        "UPDATE payments p SET checkout_id = bg.checkout_id "
        "FROM booking_groups bg WHERE bg.id = p.booking_group_id"
    )
    op.alter_column("payments", "checkout_id", nullable=False)
    op.alter_column("payments", "checkout_url", nullable=False, server_default=None)
    op.create_index("ix_payments_checkout_id", "payments", ["checkout_id"])
    op.drop_index("ix_payments_booking_group_id", table_name="payments")
    op.drop_column("payments", "booking_group_id")
    op.execute(
        "ALTER TABLE payments ALTER COLUMN status TYPE payment_outcome USING ("
        "CASE status::text WHEN 'PAID' THEN 'SUCCESSFUL' WHEN 'EXPIRED' THEN 'CANCELLED' "
        "ELSE status::text END)::payment_outcome"
    )
    provider_payment_status.drop(bind, checkfirst=True)

    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("kind", refund_kind, nullable=False),
        sa.Column("amount_centavos", sa.Integer(), nullable=False),
        sa.Column("provider_reference", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # Only defaults move: a value the owner already tuned is theirs.
    op.execute(
        "UPDATE parameters SET value = '5' WHERE key = 'hold_duration_minutes' AND value = '15'"
    )
    op.execute(
        "UPDATE parameters SET value = '14' WHERE key = 'booking_horizon_days' AND value = '30'"
    )
    op.execute(
        sa.text("UPDATE parameters SET description = :d WHERE key = 'min_lead_minutes'").bindparams(
            d=LEAD_AFTER
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(
        sa.text("UPDATE parameters SET description = :d WHERE key = 'min_lead_minutes'").bindparams(
            d=LEAD_BEFORE
        )
    )
    op.drop_table("refunds")

    provider_payment_status.create(bind, checkfirst=True)
    op.execute(
        "ALTER TABLE payments ALTER COLUMN status TYPE provider_payment_status USING ("
        "CASE status::text WHEN 'SUCCESSFUL' THEN 'PAID' WHEN 'REFUNDED' THEN 'PAID' "
        "ELSE status::text END)::provider_payment_status"
    )
    op.add_column(
        "payments",
        sa.Column(
            "booking_group_id",
            sa.Integer(),
            sa.ForeignKey("booking_groups.id", ondelete="CASCADE"),
        ),
    )
    op.execute(
        "UPDATE payments p SET booking_group_id = "
        "(SELECT MIN(bg.id) FROM booking_groups bg WHERE bg.checkout_id = p.checkout_id)"
    )
    op.alter_column("payments", "booking_group_id", nullable=False)
    op.create_index("ix_payments_booking_group_id", "payments", ["booking_group_id"])
    op.drop_index("ix_payments_checkout_id", table_name="payments")
    op.drop_column("payments", "checkout_id")
    op.drop_column("payments", "checkout_url")
    op.drop_column("payments", "provider_status")

    op.drop_constraint("ck_checkout_only_on_player_booking", "booking_groups", type_="check")
    op.drop_index("ix_booking_groups_checkout_id", table_name="booking_groups")
    op.drop_column("booking_groups", "checkout_id")
    op.drop_table("checkouts")
    refund_kind.drop(bind, checkfirst=True)
    payment_outcome.drop(bind, checkfirst=True)
