from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.courts.persistence.models import CourtModel
from src.pricing.persistence.models import PriceRuleModel

# 0 = (day, hour) … 3 = (NULL, NULL): the resolution order of spec §4.4.
_SPECIFICITY = case(
    (PriceRuleModel.day_of_week.is_not(None) & PriceRuleModel.hour_of_day.is_not(None), 0),
    (PriceRuleModel.hour_of_day.is_not(None), 1),
    (PriceRuleModel.day_of_week.is_not(None), 2),
    else_=3,
)


class PriceRuleRepository:
    # Rules are venue-owned through their court; `scope` is keyword-only
    # with no default and is checked against the court's venue.

    async def list_for_court(
        self, session: AsyncSession, court_id: int, *, scope: VenueScope
    ) -> list[PriceRuleModel]:
        rows = await session.execute(
            select(PriceRuleModel)
            .join(CourtModel, CourtModel.id == PriceRuleModel.court_id)
            .where(PriceRuleModel.court_id == court_id, scope.where(CourtModel.venue_id))
            .order_by(
                _SPECIFICITY,
                PriceRuleModel.day_of_week,
                PriceRuleModel.hour_of_day,
                PriceRuleModel.id,
            )
        )
        return list(rows.scalars().all())

    async def get(
        self, session: AsyncSession, rule_id: int, *, scope: VenueScope
    ) -> PriceRuleModel | None:
        result = await session.execute(
            select(PriceRuleModel)
            .join(CourtModel, CourtModel.id == PriceRuleModel.court_id)
            .where(PriceRuleModel.id == rule_id, scope.where(CourtModel.venue_id))
        )
        return result.scalar_one_or_none()

    async def list_for_courts(
        self, session: AsyncSession, court_ids: list[int], *, scope: VenueScope
    ) -> list[PriceRuleModel]:
        """Every rule of a set of courts in ONE query — the availability grid
        resolves all its cells against this (spec §7.2)."""
        rows = await session.execute(
            select(PriceRuleModel)
            .join(CourtModel, CourtModel.id == PriceRuleModel.court_id)
            .where(PriceRuleModel.court_id.in_(court_ids), scope.where(CourtModel.venue_id))
        )
        return list(rows.scalars().all())


price_rule_repository = PriceRuleRepository()
