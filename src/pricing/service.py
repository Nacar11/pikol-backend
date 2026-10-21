from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.courts.service import court_service
from src.parameters.service import parameter_service
from src.pricing.domain.pricing import PriceResolver, PriceRule
from src.pricing.dto import PriceRuleCreate, PriceRuleUpdate, ResolvedPrice
from src.pricing.persistence.models import SPECIFICITY_CONSTRAINT, PriceRuleModel
from src.pricing.persistence.repository import price_rule_repository
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.utils.time import manila_day_bounds


def rule_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Price rule not found.")


class PricingService:
    async def resolver_for(
        self, session: AsyncSession, court_id: int, *, scope: VenueScope
    ) -> PriceResolver:
        """One query for the court's rules, one parameter read — then every
        hour resolves in memory."""
        rules = await price_rule_repository.list_for_court(session, court_id, scope=scope)
        default = (await parameter_service.get(session)).default_slot_price_centavos
        return PriceResolver(
            (PriceRule(r.day_of_week, r.hour_of_day, r.price_centavos) for r in rules), default
        )

    async def list_rules(
        self, session: AsyncSession, court_id: int, *, scope: VenueScope
    ) -> list[PriceRuleModel]:
        await court_service.get(session, court_id, scope=scope)  # 404 if out of scope
        return await price_rule_repository.list_for_court(session, court_id, scope=scope)

    async def create_rule(
        self,
        session: AsyncSession,
        caller: UserModel,
        court_id: int,
        body: PriceRuleCreate,
        *,
        scope: VenueScope,
    ) -> PriceRuleModel:
        await court_service.get(session, court_id, scope=scope)
        rule = PriceRuleModel(
            court_id=court_id,
            **body.model_dump(),
            created_by_user_id=caller.id,
            updated_by_user_id=caller.id,
        )
        # The NULLS NOT DISTINCT constraint is the duplicate check — no
        # SELECT-then-INSERT race. The savepoint keeps the session usable.
        try:
            async with session.begin_nested():
                session.add(rule)
                await session.flush()
        except IntegrityError as exc:
            if SPECIFICITY_CONSTRAINT in str(exc.orig):
                raise AppError(
                    409,
                    "PRICE_RULE_EXISTS",
                    "This court already has a rule for that day and hour. Edit it instead.",
                ) from None
            raise
        await session.commit()
        return rule

    async def _get_rule(
        self, session: AsyncSession, rule_id: int, scope: VenueScope
    ) -> PriceRuleModel:
        rule = await price_rule_repository.get(session, rule_id, scope=scope)
        if rule is None:
            raise rule_not_found()
        return rule

    async def update_rule(
        self,
        session: AsyncSession,
        caller: UserModel,
        rule_id: int,
        body: PriceRuleUpdate,
        *,
        scope: VenueScope,
    ) -> PriceRuleModel:
        rule = await self._get_rule(session, rule_id, scope)
        rule.price_centavos = body.price_centavos
        rule.updated_by_user_id = caller.id
        await session.commit()
        await session.refresh(rule)
        return rule

    async def delete_rule(self, session: AsyncSession, rule_id: int, *, scope: VenueScope) -> None:
        await session.delete(await self._get_rule(session, rule_id, scope))
        await session.commit()

    async def day_prices(
        self, session: AsyncSession, court_id: int, day: date, *, scope: VenueScope
    ) -> list[ResolvedPrice]:
        """The 24 prices of one MANILA day — its first slot is 16:00Z the
        previous UTC day."""
        await court_service.get(session, court_id, scope=scope)
        resolver = await self.resolver_for(session, court_id, scope=scope)
        start, _ = manila_day_bounds(day)
        slots = [start + timedelta(hours=h) for h in range(24)]
        return [
            ResolvedPrice(starts_at=s, hour=h, price_centavos=resolver.price_at(s))
            for h, s in enumerate(slots)
        ]


pricing_service = PricingService()
