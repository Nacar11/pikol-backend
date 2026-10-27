"""The payment provider port (ADR 0002).

A webhook is a NOTIFICATION, never evidence: `parse_webhook` extracts only a
payment id and nothing it says is believed. `fetch_payment` asks the
provider directly and is the only source of status, amount, and reference.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from src.payments.domain.enums import PaymentOutcome, PaymentProviderName


class ProviderError(Exception):
    """The provider could not be reached or answered nonsense."""


class PaymentNotFound(ProviderError):
    """The provider has no such payment — e.g. a forged webhook id."""


@dataclass(frozen=True)
class CheckoutSession:
    checkout_id: str  # for Maya this is also the payment id
    checkout_url: str


@dataclass(frozen=True)
class WebhookNotice:
    payment_id: str  # the ONLY field read from a webhook body


@dataclass(frozen=True)
class ProviderPayment:
    payment_id: str
    status: PaymentOutcome
    provider_status: str  # Maya's own wording, e.g. PAYMENT_EXPIRED
    amount_centavos: int
    reference: str
    paid_at: datetime | None  # decides void (same Manila day) or refund
    raw: dict[str, Any]  # PII — stored, never logged


class PaymentProvider(Protocol):
    name: PaymentProviderName

    async def create_checkout(
        self, *, amount_centavos: int, reference: str, description: str, return_url: str
    ) -> CheckoutSession: ...

    def parse_webhook(self, payload: bytes) -> WebhookNotice: ...

    async def fetch_payment(self, payment_id: str) -> ProviderPayment: ...

    async def void(self, payment_id: str) -> str:
        """Undo a payment in full on its own Manila day. Returns the
        provider's reference; raises ProviderError when it cannot."""
        ...

    async def refund(self, payment_id: str, amount_centavos: int) -> str:
        """Undo a payment from the next day on. Returns the provider's
        reference; raises ProviderError when it cannot."""
        ...
