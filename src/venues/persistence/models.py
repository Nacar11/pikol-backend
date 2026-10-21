from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class VenueModel(Base):
    """A venue. Only the table exists in Phase 2 — users.venue_id needs a real
    FK for VenueScope to mean anything. Phase 3 adds the endpoints."""

    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # use_alter: venues and users reference each other.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", use_alter=True, name="fk_venues_created_by_user_id")
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", use_alter=True, name="fk_venues_updated_by_user_id")
    )
