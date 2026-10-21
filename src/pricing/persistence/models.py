from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

import src.courts.persistence.models  # noqa: F401  (court_id FK target)
from src.database.base import Base

SPECIFICITY_CONSTRAINT = "uq_price_rule_specificity"


class PriceRuleModel(Base):
    """One wildcard price rule. NULL day/hour means "any" (spec §4.4)."""

    __tablename__ = "price_rules"
    __table_args__ = (
        # NULLS NOT DISTINCT is not optional (dbml Appendix A §4): Postgres
        # treats NULLs as distinct by default, so without it every wildcard
        # row is trivially unique and two flat rates could coexist.
        UniqueConstraint(
            "court_id",
            "day_of_week",
            "hour_of_day",
            name=SPECIFICITY_CONSTRAINT,
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6)"
            " AND (hour_of_day IS NULL OR hour_of_day BETWEEN 0 AND 23)",
            name="ck_price_rule_ranges",
        ),
        CheckConstraint("price_centavos >= 0", name="ck_price_rule_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    court_id: Mapped[int] = mapped_column(
        ForeignKey("courts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int | None] = mapped_column(Integer)
    hour_of_day: Mapped[int | None] = mapped_column(Integer)
    price_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
