import json
from typing import Any
from uuid import uuid4

from src.payments.adapters.maya_wire import checkout_request, parse_notice, parse_payment
from src.payments.domain.enums import PaymentProviderName
from src.payments.domain.port import (
    CheckoutSession,
    PaymentNotFound,
    ProviderPayment,
    WebhookNotice,
)
from src.utils.api import API_PREFIX


class MockProvider:
    """A stand-in for Maya that keeps payments in process memory AS MAYA'S
    JSON, and reads them back through maya_wire — so the mock exercises the
    same parsing and peso conversion the real adapter will use.

    Its hosted page and "simulate" routes live in
    src/payments/controllers/mock_maya.py and mount only when
    MOCK_CHECKOUT_ENABLED=true.
    """

    name = PaymentProviderName.MOCK

    def __init__(self, *, api_base_url: str) -> None:
        self._api_base_url = api_base_url
        self._payments: dict[str, dict[str, Any]] = {}
        self._return_urls: dict[str, str] = {}

    async def create_checkout(
        self, *, amount_centavos: int, reference: str, description: str, return_url: str
    ) -> CheckoutSession:
        request = checkout_request(
            amount_centavos=amount_centavos,
            reference=reference,
            description=description,
            return_url=return_url,
        )
        checkout_id = str(uuid4())
        self._payments[checkout_id] = {
            "id": checkout_id,
            "status": "PENDING_TOKEN",
            "totalAmount": request["totalAmount"],
            "requestReferenceNumber": reference,
        }
        self._return_urls[checkout_id] = return_url
        return CheckoutSession(
            checkout_id=checkout_id,
            checkout_url=f"{self._api_base_url}{API_PREFIX}/mock-maya/checkouts/{checkout_id}",
        )

    def parse_webhook(self, payload: bytes) -> WebhookNotice:
        return parse_notice(payload)

    async def fetch_payment(self, payment_id: str) -> ProviderPayment:
        if payment_id not in self._payments:
            raise PaymentNotFound(payment_id)
        return parse_payment(self._payments[payment_id])

    # ── the mock's own controls ──────────────────────────────────────────

    def exists(self, checkout_id: str) -> bool:
        return checkout_id in self._payments

    def describe(self, checkout_id: str) -> dict[str, Any]:
        return dict(self._payments[checkout_id])

    def return_url(self, checkout_id: str) -> str:
        return self._return_urls[checkout_id]

    def settle(self, checkout_id: str, maya_status: str) -> None:
        """What the payer does on Maya's hosted page."""
        self._payments[checkout_id]["status"] = maya_status

    def webhook_body(self, checkout_id: str) -> bytes:
        """What Maya would POST to us: the payment, with paymentStatus."""
        payment = self._payments[checkout_id]
        return json.dumps({**payment, "paymentStatus": payment["status"]}).encode()
