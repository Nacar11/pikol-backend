import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.payments.adapters.maya_wire import checkout_request, parse_notice, parse_payment
from src.payments.domain.enums import PaymentProviderName, RefundKind
from src.payments.domain.port import (
    CheckoutSession,
    PaymentNotFound,
    ProviderError,
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
        # Every void and refund made: payment id -> (kind, reference, amount).
        self.undone: dict[str, tuple[RefundKind, str, int]] = {}
        # Make void and refund fail, to rehearse "refund by hand" (scenario 11).
        self.fail_undo = False

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

    def exists(self, checkout_id: str) -> bool:
        return checkout_id in self._payments

    def describe(self, checkout_id: str) -> dict[str, Any]:
        return dict(self._payments[checkout_id])

    def return_url(self, checkout_id: str) -> str:
        return self._return_urls[checkout_id]

    async def void(self, payment_id: str) -> str:
        return self._undo(payment_id, RefundKind.VOID, "VOIDED")

    async def refund(self, payment_id: str, amount_centavos: int) -> str:
        return self._undo(payment_id, RefundKind.REFUND, "REFUNDED", amount_centavos)

    def _undo(
        self, payment_id: str, kind: RefundKind, maya_status: str, amount: int | None = None
    ) -> str:
        if payment_id not in self._payments:
            raise PaymentNotFound(payment_id)
        if self.fail_undo:
            raise ProviderError(f"mock {kind.lower()} refused")
        payment = parse_payment(self._payments[payment_id])
        reference = f"{kind.lower()}-{uuid4().hex[:8]}"
        self._payments[payment_id]["status"] = maya_status
        self.undone[payment_id] = (kind, reference, amount or payment.amount_centavos)
        return reference

    def settle(
        self, checkout_id: str, maya_status: str, *, paid_at: datetime | None = None
    ) -> None:
        """What the payer does on Maya's hosted page."""
        payment = self._payments[checkout_id]
        payment["status"] = maya_status
        if maya_status == "PAYMENT_SUCCESS":
            payment["paymentAt"] = (paid_at or datetime.now(UTC)).isoformat()

    def webhook_body(self, checkout_id: str) -> bytes:
        """What Maya would POST to us: the payment, with paymentStatus."""
        payment = self._payments[checkout_id]
        return json.dumps({**payment, "paymentStatus": payment["status"]}).encode()
