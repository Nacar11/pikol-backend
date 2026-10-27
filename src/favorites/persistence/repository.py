from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.favorites.persistence.models import FavoriteVenueModel
from src.venues.persistence.models import VenueModel


class FavoriteRepository:
    """`user_id` comes from the token and IS the scope."""

    async def list_active(self, session: AsyncSession, user_id: int) -> list[VenueModel]:
        rows = await session.execute(
            select(VenueModel)
            .join(FavoriteVenueModel, FavoriteVenueModel.venue_id == VenueModel.id)
            .where(FavoriteVenueModel.user_id == user_id, VenueModel.is_active.is_(True))
            .order_by(FavoriteVenueModel.created_at.desc(), VenueModel.id.desc())
        )
        return list(rows.scalars().all())

    async def add(self, session: AsyncSession, user_id: int, venue_id: int) -> None:
        await session.execute(
            insert(FavoriteVenueModel)
            .values(user_id=user_id, venue_id=venue_id)
            .on_conflict_do_nothing()
        )

    async def remove(self, session: AsyncSession, user_id: int, venue_id: int) -> None:
        await session.execute(
            delete(FavoriteVenueModel).where(
                FavoriteVenueModel.user_id == user_id, FavoriteVenueModel.venue_id == venue_id
            )
        )


favorite_repository = FavoriteRepository()
