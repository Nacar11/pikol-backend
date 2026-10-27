from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import get_venue_scope, require_permission
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.pricing.dto import PriceRuleCreate, PriceRuleOut, PriceRuleUpdate, ResolvedPrice
from src.pricing.service import pricing_service
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

router = APIRouter(prefix=f"{API_PREFIX}/admin", tags=["admin: pricing"])

manage_prices = require_permission("PRICE:Manage")


@router.get("/courts/{court_id}/price-rules")
async def list_rules(
    court_id: int,
    _: UserModel = Depends(manage_prices),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> list[PriceRuleOut]:
    """Most specific first — the order they are tried in."""
    rules = await pricing_service.list_rules(session, court_id, scope=scope)
    return [PriceRuleOut.of(r) for r in rules]


@router.post("/courts/{court_id}/price-rules", status_code=status.HTTP_201_CREATED)
async def create_rule(
    court_id: int,
    body: PriceRuleCreate,
    caller: UserModel = Depends(manage_prices),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> PriceRuleOut:
    rule = await pricing_service.create_rule(session, caller, court_id, body, scope=scope)
    return PriceRuleOut.of(rule)


@router.patch("/price-rules/{rule_id}")
async def update_rule(
    rule_id: int,
    body: PriceRuleUpdate,
    caller: UserModel = Depends(manage_prices),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> PriceRuleOut:
    """Changes future bookings only — a booking's price is frozen at creation."""
    rule = await pricing_service.update_rule(session, caller, rule_id, body, scope=scope)
    return PriceRuleOut.of(rule)


@router.delete("/price-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: int,
    _: UserModel = Depends(manage_prices),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    await pricing_service.delete_rule(session, rule_id, scope=scope)


@router.get("/courts/{court_id}/prices")
async def day_prices(
    court_id: int,
    day: date = Query(..., alias="date", description="Manila-local calendar day, YYYY-MM-DD"),
    _: UserModel = Depends(manage_prices),
    scope: VenueScope = Depends(get_venue_scope),
    session: AsyncSession = Depends(get_session),
) -> list[ResolvedPrice]:
    """What each of the day's 24 slots would cost — the pricing screen's preview."""
    return await pricing_service.day_prices(session, court_id, day, scope=scope)
