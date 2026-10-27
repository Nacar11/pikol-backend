from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.courts.persistence.models import CourtModel


class CourtRepository:
    # Courts belong to a venue: `scope` is keyword-only with no default, and
    # it filters on the court's own venue_id — never on a venue id the caller
    # put in the URL.

    async def list_for_venue(
        self,
        session: AsyncSession,
        venue_id: int,
        *,
        scope: VenueScope,
        page: int,
        limit: int,
        active_only: bool,
    ) -> tuple[list[CourtModel], int]:
        filters: list[Any] = [CourtModel.venue_id == venue_id, scope.where(CourtModel.venue_id)]
        if active_only:
            filters.append(CourtModel.is_active.is_(True))
        total = (await session.execute(select(func.count()).where(*filters))).scalar_one()
        rows = await session.execute(
            select(CourtModel)
            .where(*filters)
            .order_by(CourtModel.sort_order, CourtModel.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.scalars().all()), total

    async def list_all(self, session: AsyncSession, *, scope: VenueScope) -> list[CourtModel]:
        rows = await session.execute(
            select(CourtModel)
            .where(scope.where(CourtModel.venue_id))
            .order_by(CourtModel.sort_order, CourtModel.id)
        )
        return list(rows.scalars().all())

    async def get(
        self, session: AsyncSession, court_id: int, *, scope: VenueScope
    ) -> CourtModel | None:
        result = await session.execute(
            select(CourtModel).where(CourtModel.id == court_id, scope.where(CourtModel.venue_id))
        )
        return result.scalar_one_or_none()


court_repository = CourtRepository()
