from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.payments.domain.enums import PaymentOutcome, PaymentProviderName
from src.payments.domain.port import CheckoutSession
from src.payments.persistence.models import EVENT_CONSTRAINT, PaymentModel, WebhookEventModel
from src.utils.errors import constraint_of


class PaymentRepository:
    async def add_checkout(
        self,
        session: AsyncSession,
        *,
        checkout_id: int,
        provider: PaymentProviderName,
        checkout: CheckoutSession,
        amount_centavos: int,
    ) -> PaymentModel:
        payment = PaymentModel(
            checkout_id=checkout_id,
            provider=provider,
            provider_checkout_id=checkout.checkout_id,
            checkout_url=checkout.checkout_url,
            amount_centavos=amount_centavos,
            status=PaymentOutcome.PENDING,
        )
        session.add(payment)
        await session.flush()
        return payment

    async def latest_for_checkout(
        self, session: AsyncSession, checkout_id: int
    ) -> PaymentModel | None:
        result = await session.execute(
            select(PaymentModel)
            .where(PaymentModel.checkout_id == checkout_id)
            .order_by(PaymentModel.created_at.desc(), PaymentModel.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def by_checkout_id(
        self, session: AsyncSession, provider: PaymentProviderName, checkout_id: str
    ) -> PaymentModel | None:
        result = await session.execute(
            select(PaymentModel)
            .where(
                PaymentModel.provider == provider,
                PaymentModel.provider_checkout_id == checkout_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def record_event(
        self,
        session: AsyncSession,
        *,
        provider: PaymentProviderName,
        event_id: str,
        payload: dict[str, Any],
    ) -> WebhookEventModel | None:
        """Insert first. None means "already processed" — the unique index
        is the check, and the savepoint keeps the session usable after it."""
        event = WebhookEventModel(provider=provider, provider_event_id=event_id, payload=payload)
        try:
            async with session.begin_nested():
                session.add(event)
                await session.flush()
        except IntegrityError as exc:
            if constraint_of(exc) == EVENT_CONSTRAINT:
                return None
            raise
        return event


payment_repository = PaymentRepository()
