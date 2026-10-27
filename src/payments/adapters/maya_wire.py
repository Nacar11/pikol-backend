"""Maya Checkout's JSON shapes — the ONLY place pesos exist (ADR 0002 §3).

Shared by MockProvider (today) and MayaProvider (once sandbox keys exist),
so the conversion and parsing code is exercised long before real money
moves. Field names follow Maya's docs; the real adapter's first task is to
confirm them against sandbox responses.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from src.payments.domain.enums import PaymentOutcome
from src.payments.domain.port import ProviderError, ProviderPayment, WebhookNotice

logger = logging.getLogger("pikol.payments")

CURRENCY = "PHP"
CENTAVO = Decimal("0.01")

# ADR 0004's table. Anything else stays PENDING: logged and re-checked later.
MAYA_STATUSES = {
    "PENDING_TOKEN": PaymentOutcome.PENDING,
    "PENDING_PAYMENT": PaymentOutcome.PENDING,
    "FOR_AUTHENTICATION": PaymentOutcome.PENDING,
    "AUTHENTICATING": PaymentOutcome.PENDING,
    "AUTH_SUCCESS": PaymentOutcome.PENDING,
    "PAYMENT_PROCESSING": PaymentOutcome.PENDING,
    "PAYMENT_SUCCESS": PaymentOutcome.SUCCESSFUL,
    "PAYMENT_FAILED": PaymentOutcome.FAILED,
    "AUTH_FAILED": PaymentOutcome.FAILED,
    # Maya's expiry arrives at minute 60; our hold cancelled it at minute 5.
    "PAYMENT_CANCELLED": PaymentOutcome.CANCELLED,
    "PAYMENT_EXPIRED": PaymentOutcome.CANCELLED,
    # Confirms a void or refund we asked for.
    "VOIDED": PaymentOutcome.REFUNDED,
    "REFUNDED": PaymentOutcome.REFUNDED,
}


def to_pesos(centavos: int) -> Decimal:
    return (Decimal(centavos) / 100).quantize(CENTAVO)


def from_pesos(value: str | int | Decimal) -> int:
    """Pesos → integer centavos. Refuses floats (precision already lost) and
    anything finer than a centavo (not a real amount)."""
    if isinstance(value, float):
        raise TypeError("amounts must arrive as strings or Decimals, never floats")
    try:
        pesos = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {value!r}") from exc
    centavos = pesos * 100
    if centavos != centavos.to_integral_value():
        raise ValueError(f"amount {value!r} is finer than one centavo")
    return int(centavos)


def checkout_request(
    *, amount_centavos: int, reference: str, description: str, return_url: str
) -> dict[str, Any]:
    """The Create Checkout body. All three redirects land on our return page,
    which confirms nothing by itself (spec §5.3)."""
    amount = {"value": str(to_pesos(amount_centavos)), "currency": CURRENCY}
    return {
        "totalAmount": amount,
        "requestReferenceNumber": reference,
        "items": [{"name": description, "quantity": 1, "totalAmount": amount}],
        "redirectUrl": {"success": return_url, "failure": return_url, "cancel": return_url},
    }


def parse_payment(raw: dict[str, Any]) -> ProviderPayment:
    """A Retrieve Payment response → ProviderPayment. Accepts the flat
    (`amount`, `status`) and nested (`totalAmount.value`, `paymentStatus`)
    spellings until the sandbox settles which one Maya sends."""
    try:
        payment_id = str(raw["id"])
        maya_status = str(raw.get("status") or raw["paymentStatus"])
        if "totalAmount" in raw:
            amount, currency = raw["totalAmount"]["value"], raw["totalAmount"].get("currency")
        else:
            amount, currency = raw["amount"], raw.get("currency", CURRENCY)
        reference = str(raw["requestReferenceNumber"])
        paid_at = datetime.fromisoformat(raw["paymentAt"]) if raw.get("paymentAt") else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"unexpected payment shape: missing {exc}") from None
    if currency != CURRENCY:
        raise ProviderError(f"unexpected currency {currency!r}")
    try:
        amount_centavos = from_pesos(amount)
    except (TypeError, ValueError) as exc:
        raise ProviderError(str(exc)) from None
    status = MAYA_STATUSES.get(maya_status)
    if status is None:
        logger.warning("unknown Maya status %s for payment %s", maya_status, payment_id)
    return ProviderPayment(
        payment_id=payment_id,
        status=status or PaymentOutcome.PENDING,
        provider_status=maya_status,
        amount_centavos=amount_centavos,
        reference=reference,
        paid_at=paid_at,
        raw=raw,
    )


def parse_notice(body: bytes) -> WebhookNotice:
    """Read the payment id from a webhook body — and nothing else."""
    try:
        data = json.loads(body)
    except ValueError:
        raise ProviderError("webhook body is not JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
        raise ProviderError("webhook body has no payment id")
    return WebhookNotice(payment_id=data["id"])
