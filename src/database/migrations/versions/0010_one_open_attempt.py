"""One open payment attempt per checkout

Revision ID: 0010_one_open_attempt
Revises: 0009_favorite_venues

Partial, because checkouts backfilled from the old retry route may hold a
resolved attempt beside a later one. Older duplicate PENDING attempts — only
possible from that route — are closed first; their holds ended long ago.
"""

from alembic import op

revision = "0010_one_open_attempt"
down_revision = "0009_favorite_venues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE payments p SET status = 'CANCELLED'
         WHERE p.status = 'PENDING'
           AND EXISTS (SELECT 1 FROM payments newer
                        WHERE newer.checkout_id = p.checkout_id
                          AND newer.status = 'PENDING'
                          AND newer.id > p.id)
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_payments_one_open_attempt "
        "ON payments (checkout_id) WHERE status = 'PENDING'"
    )


def downgrade() -> None:
    op.drop_index("uq_payments_one_open_attempt", table_name="payments")
