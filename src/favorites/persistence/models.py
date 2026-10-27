from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class FavoriteVenueModel(Base):
    """A customer's favorite venue — for the mobile app's Favorites tab."""

    __tablename__ = "favorite_venues"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    venue_id: Mapped[int] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
