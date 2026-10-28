"""Action events: what happened to a booking, and when

Revision ID: 0011_action_events
Revises: 0010_one_open_attempt

Append-only history, written by the services in the transaction of each
change. Existing bookings get only what is known exactly — their creation and
their cancellation; nothing else is invented.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_action_events"
down_revision = "0010_one_open_attempt"
branch_labels = None
depends_on = None

action_event_type = postgresql.ENUM(
    "HOLD_PLACED",
    "WALK_IN_BOOKED",
    "CLOSURE_CREATED",
    "PAYMENT_STARTED",
    "PAYMENT_SUCCEEDED",
    "PAYMENT_FAILED",
    "PAYMENT_CANCELLED",
    "CONFIRMED",
    "MARKED_PAID",
    "COMPLETED",
    "NO_SHOW",
    "CANCELLED",
    "EXPIRED",
    "REFUNDED",
    "VOIDED",
    "FLAGGED",
    "RESOLVED",
    "REOPENED",
    name="action_event_type",
    create_type=False,
)
actor_kind = postgresql.ENUM("STAFF", "CUSTOMER", "SYSTEM", name="actor_kind", create_type=False)

# Creation and cancellation, in ONE statement (asyncpg runs one at a time).
# Kept here, not imported: a migration must not
# change when the app's code does.
BACKFILL_SQL = """
INSERT INTO action_events
       (booking_group_id, checkout_id, type, actor_kind, actor_user_id, occurred_at, data)
SELECT bg.id, bg.checkout_id,
       CASE bg.type WHEN 'PLAYER_BOOKING' THEN 'HOLD_PLACED'
                    WHEN 'WALK_IN' THEN 'WALK_IN_BOOKED'
                    ELSE 'CLOSURE_CREATED' END::action_event_type,
       CASE WHEN bg.created_by_user_id = bg.player_id THEN 'CUSTOMER'
            ELSE 'STAFF' END::actor_kind,
       bg.created_by_user_id, bg.created_at,
       CASE WHEN bg.type = 'MAINTENANCE' AND bg.notes IS NOT NULL
                 THEN jsonb_build_object('reason', bg.notes)
            WHEN bg.type = 'WALK_IN' AND bg.notes IS NOT NULL
                 THEN jsonb_build_object('notes', bg.notes)
            ELSE '{}'::jsonb END
  FROM booking_groups bg
UNION ALL
SELECT bg.id, bg.checkout_id,
       CASE WHEN bg.type = 'MAINTENANCE' THEN 'REOPENED'
            ELSE 'CANCELLED' END::action_event_type,
       CASE WHEN bg.cancelled_by_user_id = bg.player_id THEN 'CUSTOMER'
            ELSE 'STAFF' END::actor_kind,
       bg.cancelled_by_user_id, bg.cancelled_at,
       jsonb_build_object('reason', bg.cancellation_reason)
  FROM booking_groups bg
 WHERE bg.cancelled_at IS NOT NULL
"""


def upgrade() -> None:
    bind = op.get_bind()
    action_event_type.create(bind, checkfirst=True)
    actor_kind.create(bind, checkfirst=True)
    op.create_table(
        "action_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "booking_group_id",
            sa.Integer(),
            sa.ForeignKey("booking_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "checkout_id",
            sa.Integer(),
            sa.ForeignKey("checkouts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("type", action_event_type, nullable=False),
        sa.Column("actor_kind", actor_kind, nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "booking_group_id IS NOT NULL OR checkout_id IS NOT NULL",
            name="ck_action_event_has_subject",
        ),
        sa.CheckConstraint(
            "(actor_kind = 'SYSTEM') = (actor_user_id IS NULL)", name="ck_action_event_actor"
        ),
    )
    op.create_index(
        "ix_action_events_group_time", "action_events", ["booking_group_id", "occurred_at"]
    )
    op.create_index(
        "ix_action_events_checkout_time", "action_events", ["checkout_id", "occurred_at"]
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_table("action_events")
    bind = op.get_bind()
    actor_kind.drop(bind, checkfirst=True)
    action_event_type.drop(bind, checkfirst=True)
