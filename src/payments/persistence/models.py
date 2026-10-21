from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

import src.bookings.persistence.models  # noqa: F401  (booking_group_id FK target)
from src.database.base import Base
from src.payments.domain.enums import PaymentProviderName, ProviderPaymentStatus

EVENT_CONSTRAINT = "webhook_events_provider_event_id_key"

payment_provider = ENUM(*PaymentProviderName, name="payment_provider", create_type=False)
provider_payment_status = ENUM(
    *ProviderPaymentStatus, name="provider_payment_status", create_type=False
)


class PaymentModel(Base):
    """One provider ATTEMPT — a group accumulates several when a player
    retries. booking_groups.payment_status holds the verdict."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_group_id: Mapped[int] = mapped_column(
        ForeignKey("booking_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[PaymentProviderName] = mapped_column(payment_provider, nullable=False)
    provider_checkout_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # For Maya, equals provider_checkout_id — the checkoutId IS the paymentId.
    provider_payment_id: Mapped[str | None] = mapped_column(String(128))
    amount_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ProviderPaymentStatus] = mapped_column(provider_payment_status, nullable=False)
    # PII (payer name, email, mobile). Purged after webhook_payload_retention_days.
    # NEVER logged.
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WebhookEventModel(Base):
    """Idempotency comes from the schema: the handler INSERTS FIRST, and the
    unique violation IS the already-processed check. No branch to get wrong,
    no race between two concurrent redeliveries."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[PaymentProviderName] = mapped_column(payment_provider, nullable=False)
    # Maya events carry no id: "{payment_id}:{status}" (ADR 0002).
    provider_event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # UNTRUSTED (Maya does not sign webhooks) and PII. NEVER logged.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
