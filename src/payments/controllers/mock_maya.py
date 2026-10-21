"""The mock's stand-in for Maya's hosted checkout page.

Mounted ONLY when MOCK_CHECKOUT_ENABLED=true. Without these routes nobody
can mark a mock payment paid, so a deploy accidentally left on the mock
cannot give courts away. Unauthenticated on purpose: it plays a page on
Maya's domain, which our tokens never reach.
"""

import html
import json
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender
from src.payments.adapters.mock import MockProvider
from src.payments.domain.port import PaymentProvider
from src.payments.provider import get_payment_provider
from src.payments.service import payment_service
from src.utils.api import API_PREFIX
from src.utils.errors import AppError

router = APIRouter(prefix=f"{API_PREFIX}/mock-maya/checkouts", tags=["mock maya"])

Outcome = Literal["success", "failure", "cancel", "expire"]
MAYA_STATUS: dict[str, str] = {
    "success": "PAYMENT_SUCCESS",
    "failure": "PAYMENT_FAILED",
    "cancel": "PAYMENT_CANCELLED",
    "expire": "PAYMENT_EXPIRED",
}


def _mock(provider: PaymentProvider, checkout_id: str) -> MockProvider:
    if not isinstance(provider, MockProvider) or not provider.exists(checkout_id):
        raise AppError(404, "NOT_FOUND", "No such checkout.")
    return provider


@router.get("/{checkout_id}", response_class=HTMLResponse)
async def hosted_page(
    checkout_id: str, provider: PaymentProvider = Depends(get_payment_provider)
) -> HTMLResponse:
    payment = _mock(provider, checkout_id).describe(checkout_id)
    reference = html.escape(payment["requestReferenceNumber"])
    amount = html.escape(payment["totalAmount"]["value"])
    base = f"{API_PREFIX}/mock-maya/checkouts/{html.escape(checkout_id)}"
    buttons = "".join(
        f'<form method="post" action="{base}/{outcome}"><button>{label}</button></form>'
        for outcome, label in (("success", "Pay"), ("failure", "Fail"), ("cancel", "Cancel"))
    )
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Mock Maya checkout</title>"
        "<body style='font-family:system-ui;max-width:28rem;margin:3rem auto'>"
        "<p><strong>MOCK MAYA</strong> — no real money moves.</p>"
        f"<h1>₱{amount}</h1><p>Booking {reference}</p>{buttons}</body>"
    )


@router.post("/{checkout_id}/{outcome}")
async def settle(
    checkout_id: str,
    outcome: Outcome,
    deliver_webhook: bool = True,
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
    sender: EmailSender = Depends(get_email_sender),
) -> RedirectResponse:
    """What the payer does on Maya's page, then what Maya does next: notify
    us (unless ?deliver_webhook=false, to rehearse a lost webhook) and send
    the browser back to the return page."""
    mock = _mock(provider, checkout_id)
    mock.settle(checkout_id, MAYA_STATUS[outcome])
    if deliver_webhook:
        # The same path the real webhook takes: re-fetch, record once, apply.
        await payment_service.sync_payment(
            session,
            mock,
            checkout_id,
            sender,
            event_payload=json.loads(mock.webhook_body(checkout_id)),
        )
    return RedirectResponse(mock.return_url(checkout_id), status_code=303)
