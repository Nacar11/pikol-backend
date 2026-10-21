import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.payments.domain.enums import PaymentProviderName
from src.payments.persistence.models import WebhookEventModel
from src.payments.persistence.repository import payment_repository

MOCK = PaymentProviderName.MOCK


async def test_duplicate_event_id_rejected(db_session: AsyncSession) -> None:
    db_session.add(WebhookEventModel(provider=MOCK, provider_event_id="p1:PAID", payload={}))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                WebhookEventModel(provider=MOCK, provider_event_id="p1:PAID", payload={})
            )
            await db_session.flush()


async def test_record_event_is_the_idempotency_check(db_session: AsyncSession) -> None:
    first = await payment_repository.record_event(
        db_session, provider=MOCK, event_id="p1:PAID", payload={"id": "p1"}
    )
    second = await payment_repository.record_event(
        db_session, provider=MOCK, event_id="p1:PAID", payload={"id": "p1"}
    )
    other = await payment_repository.record_event(
        db_session, provider=MOCK, event_id="p1:FAILED", payload={"id": "p1"}
    )

    assert first is not None and other is not None
    assert second is None
    rows = (await db_session.execute(select(WebhookEventModel))).scalars().all()
    assert {r.provider_event_id for r in rows} >= {"p1:PAID", "p1:FAILED"}
