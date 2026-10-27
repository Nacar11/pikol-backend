from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentStatus
from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.persistence.models import CheckoutModel, RefundModel
from src.payments.domain.enums import PaymentOutcome, PaymentProviderName, RefundKind
from src.payments.persistence.models import PaymentModel
from src.utils.errors import constraint_of
from tests.auth.helpers import make_court, make_user, make_venue


async def _flush_fails(session: AsyncSession, row: Any) -> str | None:
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    return constraint_of(exc.value)


def _checkout(customer_id: int, venue_id: int, **overrides: Any) -> CheckoutModel:
    fields: dict[str, Any] = {
        "reference_code": "PKL-TEST1",
        "customer_id": customer_id,
        "venue_id": venue_id,
        "status": PaymentOutcome.PENDING,
        "price_centavos": 50000,
        "fee_centavos": 1500,
        "total_centavos": 51500,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    return CheckoutModel(**(fields | overrides))


def _group(court_id: int, user_id: int, **overrides: Any) -> BookingGroupModel:
    fields: dict[str, Any] = {
        "reference_code": "PKL-GRP01",
        "court_id": court_id,
        "player_id": user_id,
        "type": BookingType.PLAYER_BOOKING,
        "booking_status": BookingStatus.CONFIRMED,
        "payment_status": PaymentStatus.UNPAID,
        "price_centavos": 0,
        "total_centavos": 0,
        "created_by_user_id": user_id,
    }
    return BookingGroupModel(**(fields | overrides))


async def test_a_player_booking_needs_a_checkout(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    court = await make_court(db_session, venue.id)
    user = await make_user(db_session)

    name = await _flush_fails(db_session, _group(court.id, user.id, checkout_id=None))

    assert name == "ck_checkout_only_on_player_booking"


async def test_a_walk_in_has_no_checkout(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    court = await make_court(db_session, venue.id)
    user = await make_user(db_session)
    checkout = _checkout(user.id, venue.id)
    db_session.add(checkout)
    await db_session.flush()

    walk_in = _group(
        court.id, user.id, type=BookingType.WALK_IN, player_id=None, checkout_id=checkout.id
    )
    assert await _flush_fails(db_session, walk_in) == "ck_checkout_only_on_player_booking"


async def test_checkout_total_is_price_plus_fee(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    user = await make_user(db_session)

    name = await _flush_fails(db_session, _checkout(user.id, venue.id, total_centavos=50000))

    assert name == "ck_checkout_money"


async def test_a_payment_is_refunded_at_most_once(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    user = await make_user(db_session)
    checkout = _checkout(user.id, venue.id)
    db_session.add(checkout)
    await db_session.flush()
    payment = PaymentModel(
        checkout_id=checkout.id,
        provider=PaymentProviderName.MOCK,
        provider_checkout_id="mock-1",
        checkout_url="http://test/pay",
        amount_centavos=51500,
        status=PaymentOutcome.SUCCESSFUL,
    )
    db_session.add(payment)
    await db_session.flush()

    def refund() -> RefundModel:
        return RefundModel(
            payment_id=payment.id,
            kind=RefundKind.VOID,
            amount_centavos=51500,
            provider_reference="void-1",
        )

    db_session.add(refund())
    await db_session.flush()
    assert await _flush_fails(db_session, refund()) == "refunds_payment_id_key"
    assert len((await db_session.execute(select(RefundModel))).scalars().all()) >= 1


async def test_payment_outcome_has_the_five_statuses(db_session: AsyncSession) -> None:
    labels = (
        (await db_session.execute(select(text("unnest(enum_range(NULL::payment_outcome))::text"))))
        .scalars()
        .all()
    )

    assert labels == ["PENDING", "SUCCESSFUL", "FAILED", "CANCELLED", "REFUNDED"]
