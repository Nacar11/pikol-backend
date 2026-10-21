"""Create payments and webhook_events

Revision ID: 0005_payments
Revises: 0004_bookings
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_payments"
down_revision = "0004_bookings"
branch_labels = None
depends_on = None

payment_provider = postgresql.ENUM("MOCK", "MAYA", name="payment_provider", create_type=False)
provider_payment_status = postgresql.ENUM(
    "PENDING",
    "PAID",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
    name="provider_payment_status",
    create_type=False,
)


def upgrade() -> None:
    payment_provider.create(op.get_bind(), checkfirst=True)
    provider_payment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "booking_group_id",
            sa.Integer(),
            sa.ForeignKey("booking_groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", payment_provider, nullable=False),
        sa.Column("provider_checkout_id", sa.String(128), nullable=False, unique=True),
        sa.Column("provider_payment_id", sa.String(128)),
        sa.Column("amount_centavos", sa.Integer(), nullable=False),
        sa.Column("status", provider_payment_status, nullable=False),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", payment_provider, nullable=False),
        sa.Column("provider_event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("payments")
    provider_payment_status.drop(op.get_bind(), checkfirst=True)
    payment_provider.drop(op.get_bind(), checkfirst=True)
