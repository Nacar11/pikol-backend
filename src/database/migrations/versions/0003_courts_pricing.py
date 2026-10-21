"""Create courts and price_rules

Revision ID: 0003_courts_pricing
Revises: 0002_identity
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_courts_pricing"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def _audit() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
    ]


def upgrade() -> None:
    op.create_table(
        "courts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "venue_id",
            sa.Integer(),
            sa.ForeignKey("venues.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit(),
    )
    op.create_index("ix_courts_venue_id_sort_order", "courts", ["venue_id", "sort_order"])

    op.create_table(
        "price_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "court_id",
            sa.Integer(),
            sa.ForeignKey("courts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("day_of_week", sa.Integer()),
        sa.Column("hour_of_day", sa.Integer()),
        sa.Column("price_centavos", sa.Integer(), nullable=False),
        *_audit(),
    )
    # dbml Appendix A §4 and §5, verbatim.
    op.execute(
        "ALTER TABLE price_rules ADD CONSTRAINT uq_price_rule_specificity "
        "UNIQUE NULLS NOT DISTINCT (court_id, day_of_week, hour_of_day)"
    )
    op.execute(
        "ALTER TABLE price_rules ADD CONSTRAINT ck_price_rule_ranges "
        "CHECK ((day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6) "
        "AND (hour_of_day IS NULL OR hour_of_day BETWEEN 0 AND 23))"
    )
    op.execute(
        "ALTER TABLE price_rules ADD CONSTRAINT ck_price_rule_non_negative "
        "CHECK (price_centavos >= 0)"
    )


def downgrade() -> None:
    op.drop_table("price_rules")
    op.drop_table("courts")
