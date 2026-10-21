import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.courts.persistence.models import CourtModel
from src.pricing.persistence.models import PriceRuleModel
from tests.auth.helpers import make_venue


async def _court(session: AsyncSession) -> CourtModel:
    venue = await make_venue(session)
    court = CourtModel(venue_id=venue.id, name="Court 1")
    session.add(court)
    await session.flush()
    return court


async def _rule(session: AsyncSession, court: CourtModel, **kwargs: int | None) -> None:
    async with session.begin_nested():
        session.add(PriceRuleModel(court_id=court.id, price_centavos=70000, **kwargs))
        await session.flush()


async def test_duplicate_wildcard_rule_rejected(db_session: AsyncSession) -> None:
    """Without NULLS NOT DISTINCT two flat rates would coexist and resolve by
    arbitrary row order."""
    court = await _court(db_session)
    await _rule(db_session, court)

    with pytest.raises(IntegrityError) as exc:
        await _rule(db_session, court)

    assert "uq_price_rule_specificity" in str(exc.value)


async def test_duplicate_partial_wildcard_rejected(db_session: AsyncSession) -> None:
    court = await _court(db_session)
    await _rule(db_session, court, hour_of_day=18)

    with pytest.raises(IntegrityError):
        await _rule(db_session, court, hour_of_day=18)


async def test_same_specificity_allowed_on_different_courts(db_session: AsyncSession) -> None:
    await _rule(db_session, await _court(db_session))
    await _rule(db_session, await _court(db_session))


async def test_hour_out_of_range_rejected(db_session: AsyncSession) -> None:
    court = await _court(db_session)

    with pytest.raises(IntegrityError) as exc:
        await _rule(db_session, court, hour_of_day=24)

    assert "ck_price_rule_ranges" in str(exc.value)


async def test_negative_price_rejected(db_session: AsyncSession) -> None:
    court = await _court(db_session)

    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            db_session.add(PriceRuleModel(court_id=court.id, price_centavos=-1))
            await db_session.flush()

    assert "ck_price_rule_non_negative" in str(exc.value)
