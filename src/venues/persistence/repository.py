from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.venues.persistence.models import VenueModel


class VenueRepository:
    # Venues ARE venue-owned data: `scope` is keyword-only with no default.

    async def list(
        self,
        session: AsyncSession,
        *,
        scope: VenueScope,
        page: int,
        limit: int,
        active_only: bool,
    ) -> tuple[list[VenueModel], int]:
        filters: list[Any] = [scope.where(VenueModel.id)]
        if active_only:
            filters.append(VenueModel.is_active.is_(True))
        total = (await session.execute(select(func.count()).where(*filters))).scalar_one()
        rows = await session.execute(
            select(VenueModel)
            .where(*filters)
            .order_by(VenueModel.name, VenueModel.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.scalars().all()), total

    async def get(
        self, session: AsyncSession, venue_id: int, *, scope: VenueScope, active_only: bool
    ) -> VenueModel | None:
        filters: list[Any] = [VenueModel.id == venue_id, scope.where(VenueModel.id)]
        if active_only:
            filters.append(VenueModel.is_active.is_(True))
        return (await session.execute(select(VenueModel).where(*filters))).scalar_one_or_none()


venue_repository = VenueRepository()
