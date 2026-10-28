from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.dto import ActionEventOut
from src.action_events.service import event_service
from src.auth.access import require_customer
from src.auth.rate_limit import limiter
from src.checkouts.dto import CheckoutCreate, CheckoutOut
from src.checkouts.persistence.repository import checkout_repository
from src.checkouts.service import checkout_service
from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender
from src.payments.domain.port import PaymentProvider
from src.payments.persistence.repository import payment_repository
from src.payments.provider import get_payment_provider
from src.payments.service import payment_service
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

# Identity from the token, never the URL: /checkouts/me, not /checkouts/{customer}.
# The same routes serve the web demo and the mobile app.
router = APIRouter(prefix=f"{API_PREFIX}/checkouts/me", tags=["checkouts"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_checkout(
    body: CheckoutCreate,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    """Pay now: hold every picked hour at one venue, all or nothing."""
    checkout = await checkout_service.create(session, customer, body)
    return await checkout_service.out(session, checkout)


@router.get("")
async def my_checkouts(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> Page[CheckoutOut]:
    checkouts, total = await checkout_repository.list_for_customer(
        session, customer.id, page=page, limit=limit
    )
    data = [await checkout_service.out(session, c) for c in checkouts]
    return Page.of(data, total=total, page=page, limit=limit)


@router.get("/{checkout_id}")
async def my_checkout(
    checkout_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    """The hold page and the result page read this."""
    checkout = await checkout_service.get_mine(session, customer, checkout_id)
    return await checkout_service.out(session, checkout)


@router.get("/{checkout_id}/events")
async def my_checkout_events(
    checkout_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> Page[ActionEventOut]:
    """The tracker on the result page: every booking in this payment,
    without the venue's internal queue or its staff's names."""
    checkout = await checkout_service.get_mine(session, customer, checkout_id)
    events = await event_service.customer_timeline(session, checkout, datetime.now(UTC))
    return Page.of(events, total=len(events), page=1, limit=max(len(events), 1))


@router.post("/{checkout_id}/pay")
async def pay(
    checkout_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> CheckoutOut:
    """Continue to Maya. Opens Maya's session once; a repeat returns the same
    URL. The redirect back confirms nothing — the webhook does."""
    checkout = await checkout_service.pay(session, customer, checkout_id, provider)
    return await checkout_service.out(session, checkout)


@router.post("/{checkout_id}/cancel")
async def cancel(
    checkout_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    """Cancel and release hours, while the hold is live. Paid is final."""
    checkout = await checkout_service.cancel(session, customer, checkout_id)
    return await checkout_service.out(session, checkout)


@router.post("/{checkout_id}/refresh-payment")
@limiter.limit("10/minute")
async def refresh_payment(
    request: Request,
    checkout_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
    sender: EmailSender = Depends(get_email_sender),
) -> CheckoutOut:
    """ "Check again": ask Maya about this checkout's attempt and apply what it
    says, through the webhook's own idempotent path. Closes the gap when
    every webhook landed on a sleeping backend (ADR 0002)."""
    checkout = await checkout_service.get_mine(session, customer, checkout_id)
    payment = await payment_repository.latest_for_checkout(session, checkout.id)
    if payment is not None:
        await payment_service.sync_payment(session, provider, payment.provider_checkout_id, sender)
        checkout = await checkout_service.reload(session, checkout)
    return await checkout_service.out(session, checkout)
