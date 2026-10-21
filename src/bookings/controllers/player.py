from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_player
from src.auth.rate_limit import limiter
from src.bookings.dto import BookingOut, CheckoutBookingOut, HoldRequest, PlayerCancelRequest
from src.bookings.persistence.repository import booking_repository
from src.bookings.service import booking_service
from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender
from src.payments.domain.port import PaymentProvider
from src.payments.persistence.repository import payment_repository
from src.payments.provider import get_payment_provider
from src.payments.service import payment_service
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

router = APIRouter(prefix=f"{API_PREFIX}/bookings", tags=["bookings"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_hold(
    body: HoldRequest,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> CheckoutBookingOut:
    """Hold consecutive hours for hold_duration_minutes, then open a
    checkout. Prices and fee are frozen now. The hold commits BEFORE the
    provider is called."""
    group = await booking_service.create_hold(session, player, body)
    url = await payment_service.start_checkout(session, group, provider)
    return CheckoutBookingOut(
        **BookingOut.of(group, datetime.now(UTC)).model_dump(), checkout_url=url
    )


# Identity from the token, never the URL: /bookings/me, not /bookings/{player}.


@router.get("/me")
async def my_bookings(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> Page[BookingOut]:
    groups, total = await booking_repository.list_for_player(
        session, player.id, page=page, limit=limit
    )
    now = datetime.now(UTC)
    return Page.of([BookingOut.of(g, now) for g in groups], total=total, page=page, limit=limit)


@router.get("/me/{booking_id}")
async def my_booking(
    booking_id: int,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> BookingOut:
    """The checkout return page polls this (spec §8.2)."""
    group = await booking_service.get_for_player(session, player, booking_id)
    return BookingOut.of(group, datetime.now(UTC))


@router.post("/me/{booking_id}/cancel")
async def cancel_my_booking(
    booking_id: int,
    body: PlayerCancelRequest,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> BookingOut:
    group = await booking_service.cancel_by_player(session, player, booking_id, body.reason)
    return BookingOut.of(group, datetime.now(UTC))


@router.post("/me/{booking_id}/checkout")
async def retry_checkout(
    booking_id: int,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> CheckoutBookingOut:
    """A fresh checkout for a live hold — after a failed payment, a provider
    outage, or a closed tab. Each attempt is its own payments row."""
    group = await booking_service.checkout_target(session, player, booking_id)
    url = await payment_service.start_checkout(session, group, provider)
    return CheckoutBookingOut(
        **BookingOut.of(group, datetime.now(UTC)).model_dump(), checkout_url=url
    )


@router.post("/me/{booking_id}/refresh-payment")
@limiter.limit("10/minute")
async def refresh_payment(
    request: Request,
    booking_id: int,
    player: UserModel = Depends(require_player),
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
    sender: EmailSender = Depends(get_email_sender),
) -> BookingOut:
    """Ask the provider about this booking's latest checkout and apply what
    it says — through the same idempotent path as the webhook. Closes the
    gap when every webhook attempt landed on a sleeping backend (ADR 0002).
    The return page calls it while it polls."""
    group = await booking_service.get_for_player(session, player, booking_id)
    latest = await payment_repository.latest_for_group(session, group.id)
    if latest is not None:
        await payment_service.sync_payment(session, provider, latest.provider_checkout_id, sender)
        group = await booking_service.get_for_player(session, player, booking_id)
        await session.refresh(group)
    return BookingOut.of(group, datetime.now(UTC))
