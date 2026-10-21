"""The payment provider port (ADR 0002).

A webhook is a NOTIFICATION, never evidence: `parse_webhook` extracts only a
payment id and nothing it says is believed. `fetch_payment` asks the
provider directly and is the only source of status, amount, and reference.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from src.payments.domain.enums import PaymentProviderName, ProviderPaymentStatus


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
    status: ProviderPaymentStatus
    amount_centavos: int
    reference: str
    raw: dict[str, Any]  # PII — stored, never logged


class PaymentProvider(Protocol):
    name: PaymentProviderName

    async def create_checkout(
        self, *, amount_centavos: int, reference: str, description: str, return_url: str
    ) -> CheckoutSession: ...

    def parse_webhook(self, payload: bytes) -> WebhookNotice: ...

    async def fetch_payment(self, payment_id: str) -> ProviderPayment: ...
