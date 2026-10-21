from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

import src.users.persistence.models  # noqa: F401  (audit FK target)
import src.venues.persistence.models  # noqa: F401  (venue_id FK target)
from src.database.base import Base


class CourtModel(Base):
    """No price column — pricing lives in price_rules over the parameter
    fallback (spec §4.4). No slot length — every slot is one hour."""

    __tablename__ = "courts"
    __table_args__ = (Index("ix_courts_venue_id_sort_order", "venue_id", "sort_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        ForeignKey("venues.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Column order in the availability grid. Without it the grid reorders
    # whenever row order changes.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
