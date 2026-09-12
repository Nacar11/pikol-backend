"""Create parameters table and seed defaults

Revision ID: 0001_parameters
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_parameters"
down_revision = None
branch_labels = None
depends_on = None

SEED = [
    (
        "default_slot_price_centavos",
        "50000",
        "int",
        "Fallback slot price when no price rule matches (PHP 500.00).",
    ),
    (
        "convenience_fee_percent",
        "3.00",
        "decimal",
        "Flat fee added to online bookings. Walk-ins pay none.",
    ),
    (
        "hold_duration_minutes",
        "15",
        "int",
        "How long a PENDING hold blocks a slot before it may be expired.",
    ),
    ("booking_horizon_days", "30", "int", "How far ahead a player may book."),
    (
        "max_active_holds_per_player",
        "3",
        "int",
        "Cap on unexpired holds per player. Denial-of-inventory guard.",
    ),
    ("staff_backdate_days", "7", "int", "How far back staff may record a walk-in booking."),
    (
        "cancellation_cutoff_minutes",
        "0",
        "int",
        "How close to start time a player may still cancel.",
    ),
    (
        "min_lead_minutes",
        "20",
        "int",
        "Earliest a player may book before a slot starts. Must be >= "
        "hold_duration_minutes, or a hold can outlive the slot it holds.",
    ),
    ("invitation_expiry_days", "7", "int", "Lifetime of a staff invitation token."),
    (
        "webhook_payload_retention_days",
        "90",
        "int",
        "Age at which raw provider payloads are purged (RA 10173).",
    ),
]


# create_type=False so the type is made exactly once, by the explicit
# .create() below. Left at the default, SQLAlchemy also tries to emit it
# while building the table and the migration fails with "type already
# exists". Every enum in this project follows this shape.
parameter_value_type = postgresql.ENUM(
    "int",
    "decimal",
    "bool",
    "string",
    name="parameter_value_type",
    create_type=False,
)


def upgrade() -> None:
    parameter_value_type.create(op.get_bind(), checkfirst=True)

    parameters = op.create_table(
        "parameters",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_type", parameter_value_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
    )
    op.bulk_insert(
        parameters,
        [{"key": k, "value": v, "value_type": t, "description": d} for k, v, t, d in SEED],
    )


def downgrade() -> None:
    op.drop_table("parameters")
    parameter_value_type.drop(op.get_bind(), checkfirst=True)
