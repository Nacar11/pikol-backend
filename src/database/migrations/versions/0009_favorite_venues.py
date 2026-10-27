"""Favorite venues, for the mobile app's Favorites tab

Revision ID: 0009_favorite_venues
Revises: 0008_checkouts
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_favorite_venues"
down_revision = "0008_checkouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "favorite_venues",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "venue_id",
            sa.Integer(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("favorite_venues")
