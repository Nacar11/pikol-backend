import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, PaymentMethod, PaymentStatus
from src.bookings.persistence.models import SLOT_CONSTRAINT, BookingGroupModel
from src.bookings.persistence.repository import booking_repository
from src.checkouts.domain.rules import is_live, undo_kind
from src.checkouts.persistence.models import CheckoutModel, RefundModel
from src.checkouts.persistence.repository import checkout_repository
from src.config.settings import get_settings
from src.email.port import EmailSender, send_quietly
from src.email.templates import ReceiptLine, receipt_email
from src.payments.domain.enums import PaymentOutcome, PaymentProviderName, RefundKind
from src.payments.domain.port import (
    PaymentNotFound,
    PaymentProvider,
    ProviderError,
    ProviderPayment,
)
from src.payments.persistence.models import PaymentModel
from src.payments.persistence.repository import payment_repository
from src.utils.errors import AppError, constraint_of
from src.utils.time import MANILA

# Payloads are PII: log the event id and the outcome, never the body (§7.5).
logger = logging.getLogger("pikol.payments")


FAILED_REASON = "Payment failed"
BACKED_OUT_REASON = "Backed out of payment"
# Outcomes after which a payment row never moves again.
SETTLED = frozenset({PaymentOutcome.SUCCESSFUL, PaymentOutcome.REFUNDED})


@dataclass(frozen=True)
class Effect:
    """What to do once the event's transaction has committed: a receipt for
    a booked checkout, or a void/refund for a payment that lost an hour."""

    receipt: CheckoutModel | None = None
    undo: tuple[int, int] | None = None  # (payments.id, checkouts.id)


def provider_unavailable() -> AppError:
    return AppError(
        502,
        "PAYMENT_PROVIDER_UNAVAILABLE",
        "We could not reach the payment provider. Your hours are still held — try again.",
    )


class PaymentService:
    async def start_checkout(
        self, session: AsyncSession, checkout: CheckoutModel, provider: PaymentProvider
    ) -> str:
        """Open Maya for a checkout whose hold is ALREADY COMMITTED, once.

        A second call returns the first session's URL: Maya cannot close a
        session early, so a second one could also be paid. The provider call
        sits outside the hold's transaction on purpose — holding one open
        across a network round-trip is how connection pools die.
        """
        existing = await payment_repository.latest_for_checkout(session, checkout.id)
        if existing is not None:
            return existing.checkout_url
        try:
            opened = await provider.create_checkout(
                amount_centavos=checkout.total_centavos,
                reference=checkout.reference_code,
                description=f"Pikol court booking {checkout.reference_code}",
                return_url=f"{get_settings().frontend_base_url}/book/result/{checkout.id}",
            )
        except ProviderError:
            logger.warning("checkout creation failed: checkout=%s", checkout.reference_code)
            raise provider_unavailable() from None
        await payment_repository.add_checkout(
            session,
            checkout_id=checkout.id,
            provider=provider.name,
            checkout=opened,
            amount_centavos=checkout.total_centavos,
        )
        await session.commit()
        return opened.checkout_url

    async def sync_payment(
        self,
        session: AsyncSession,
        provider: PaymentProvider,
        payment_id: str,
        sender: EmailSender,
        *,
        event_payload: dict[str, object] | None = None,
    ) -> None:
        """Ask the provider what happened to `payment_id` and apply it once.

        Shared by the webhook and the result page's refresh, so both go
        through the same idempotency check and the same transitions.
        """
        try:
            payment = await provider.fetch_payment(payment_id)
        except PaymentNotFound:
            logger.warning("payment not found at provider: %s", payment_id)
            return  # forged or foreign — nothing to apply, nothing to retry
        except ProviderError:
            raise provider_unavailable() from None

        if payment.status == PaymentOutcome.PENDING:
            return  # nothing has happened yet
        event_id = f"{payment.payment_id}:{payment.provider_status}"
        event = await payment_repository.record_event(
            session,
            provider=provider.name,
            event_id=event_id,
            payload=event_payload if event_payload is not None else payment.raw,
        )
        if event is None:
            logger.info("payment event already processed: %s", event_id)
            return

        effect = await self._apply(session, provider.name, payment)
        event.processed_at = datetime.now(UTC)
        await session.commit()
        logger.info("payment event processed: %s", event_id)
        if effect.receipt is not None:
            await self._send_receipt(sender, effect.receipt)
        if effect.undo is not None:
            await self._undo(session, provider, payment, *effect.undo)

    async def _apply(
        self, session: AsyncSession, provider: PaymentProviderName, payment: ProviderPayment
    ) -> Effect:
        """Apply a VERIFIED provider payment to its checkout (ADR 0004)."""
        row = await payment_repository.by_checkout_id(session, provider, payment.payment_id)
        if row is None:
            logger.warning("payment for an unknown checkout: %s", payment.payment_id)
            return Effect()
        checkout = await checkout_repository.get_for_update(session, row.checkout_id)
        groups = await self._lock_groups(session, checkout)
        row.provider_status = payment.provider_status
        row.provider_payment_id = payment.payment_id
        row.raw_payload = payment.raw
        now = datetime.now(UTC)

        match payment.status:
            case PaymentOutcome.FAILED | PaymentOutcome.CANCELLED:
                await self._release(session, checkout, row, groups, payment.status, now)
                return Effect()
            case PaymentOutcome.REFUNDED:
                if await payment_repository.refund_for(session, row.id) is None:
                    self._flag(groups, f"{payment.payment_id} was refunded, but not by us")
                    return Effect()
                row.status = PaymentOutcome.REFUNDED
                await checkout_repository.set_status(session, checkout, PaymentOutcome.REFUNDED)
                return Effect()
        return await self._paid(session, checkout, row, groups, payment, now)

    async def _release(
        self,
        session: AsyncSession,
        checkout: CheckoutModel,
        row: PaymentModel,
        groups: list[BookingGroupModel],
        outcome: PaymentOutcome,
        now: datetime,
    ) -> None:
        """Declined or backed out: every hour reopens at once (decisions 5,
        10). A hold that already ended has nothing left to release."""
        if row.status not in SETTLED:
            row.status = outcome
        if checkout.status != PaymentOutcome.PENDING:
            return
        live = is_live(checkout.status, checkout.expires_at, now)
        await checkout_repository.set_status(session, checkout, outcome)
        reason = FAILED_REASON if outcome == PaymentOutcome.FAILED else BACKED_OUT_REASON
        for group in groups:
            if group.booking_status != BookingStatus.PENDING:
                continue
            if live or outcome == PaymentOutcome.FAILED:
                await booking_repository.set_status(
                    session,
                    group,
                    BookingStatus.CANCELLED,
                    cancelled_at=now,
                    cancelled_by_user_id=checkout.customer_id,
                    cancellation_reason=reason,
                )
            else:
                await booking_repository.set_status(session, group, BookingStatus.EXPIRED)

    async def _paid(
        self,
        session: AsyncSession,
        checkout: CheckoutModel,
        row: PaymentModel,
        groups: list[BookingGroupModel],
        payment: ProviderPayment,
        now: datetime,
    ) -> Effect:
        """Money exists. House it if at all possible; otherwise undo it in
        full and book nothing (decisions 7, 8). Never drop it silently."""
        if (
            payment.amount_centavos != checkout.total_centavos
            or payment.reference != checkout.reference_code
        ):
            self._flag(groups, f"payment {payment.payment_id} does not match amount/reference")
            return Effect()
        if checkout.status not in (PaymentOutcome.PENDING, PaymentOutcome.CANCELLED):
            self._flag(groups, f"payment {payment.payment_id} after a final status")
            return Effect()
        row.status = PaymentOutcome.SUCCESSFUL

        if is_live(checkout.status, checkout.expires_at, now):
            for group in groups:
                await booking_repository.set_status(
                    session, group, BookingStatus.CONFIRMED, **self._paid_fields()
                )
            await checkout_repository.set_status(session, checkout, PaymentOutcome.SUCCESSFUL)
            return Effect(receipt=checkout)

        # The hold ended (or the customer cancelled) before Maya said paid.
        # Every hour must still be in the future and free: re-acquire them
        # all in one savepoint, or none.
        if all(slot.starts_at > now for group in groups for slot in group.slots):
            try:
                async with session.begin_nested():
                    for group in groups:
                        await booking_repository.expire_lapsed(
                            session, group.court_id, [s.starts_at for s in group.slots], now
                        )
                        await booking_repository.set_status(
                            session,
                            group,
                            BookingStatus.CONFIRMED,
                            cancelled_at=None,
                            cancelled_by_user_id=None,
                            cancellation_reason=None,
                            **self._paid_fields(),
                        )
                await checkout_repository.set_status(session, checkout, PaymentOutcome.SUCCESSFUL)
                logger.info("late payment re-acquired its hours: %s", checkout.reference_code)
                return Effect(receipt=checkout)
            except IntegrityError as exc:
                if constraint_of(exc) != SLOT_CONSTRAINT:
                    raise
            for group in groups:
                await session.refresh(group)

        # Simple refund flow, step 1: commit the flag BEFORE calling Maya, so
        # a crash between here and the refund leaves it in the staff queue.
        for group in groups:
            if group.booking_status == BookingStatus.PENDING:
                await booking_repository.set_status(session, group, BookingStatus.EXPIRED)
        if checkout.status == PaymentOutcome.PENDING:
            await checkout_repository.set_status(session, checkout, PaymentOutcome.CANCELLED)
        self._flag(groups, f"refund pending for {payment.payment_id}")
        return Effect(undo=(row.id, checkout.id))

    async def _undo(
        self,
        session: AsyncSession,
        provider: PaymentProvider,
        payment: ProviderPayment,
        payment_row_id: int,
        checkout_id: int,
    ) -> None:
        """Simple refund flow, step 2: void on the payment's Manila day, else
        refund — always the whole amount. A failure stays flagged for staff."""
        now = datetime.now(UTC)
        kind = undo_kind(payment.paid_at or now, now)
        try:
            if kind == RefundKind.VOID:
                reference = await provider.void(payment.payment_id)
            else:
                reference = await provider.refund(payment.payment_id, payment.amount_centavos)
        except ProviderError:
            reference = None
        checkout = await checkout_repository.get_for_update(session, checkout_id)
        groups = await self._lock_groups(session, checkout)
        if reference is None:
            self._flag(groups, f"{kind} failed — refund by hand (payment {payment.payment_id})")
            await session.commit()
            logger.warning("%s failed: %s", kind.lower(), payment.payment_id)
            return
        session.add(
            RefundModel(
                payment_id=payment_row_id,
                kind=kind,
                amount_centavos=payment.amount_centavos,
                provider_reference=reference,
            )
        )
        row = await session.get(PaymentModel, payment_row_id, populate_existing=True)
        assert row is not None
        row.status = PaymentOutcome.REFUNDED
        await checkout_repository.set_status(session, checkout, PaymentOutcome.REFUNDED)
        for group in groups:
            self._note(
                group, f"{kind} {reference} — paid after the hold ended and an hour was taken"
            )
            group.needs_resolution = False
        await session.commit()
        logger.info("%s done: %s", kind.lower(), payment.payment_id)

    @staticmethod
    async def _lock_groups(
        session: AsyncSession, checkout: CheckoutModel
    ) -> list[BookingGroupModel]:
        # Lock only the group rows: the eager-loaded court and player are
        # outer joins, which Postgres refuses to lock.
        result = await session.execute(
            select(BookingGroupModel)
            .where(BookingGroupModel.checkout_id == checkout.id)
            .order_by(BookingGroupModel.id)
            .with_for_update(of=BookingGroupModel)
            .execution_options(populate_existing=True)
        )
        return list(result.unique().scalars().all())

    @staticmethod
    def _paid_fields() -> dict[str, object]:
        return {"payment_status": PaymentStatus.PAID, "payment_method": PaymentMethod.ONLINE}

    @classmethod
    def _flag(cls, groups: list[BookingGroupModel], reason: str) -> None:
        """Into the admin resolution queue (spec §5.5). A flag, not a status."""
        for group in groups:
            group.needs_resolution = True
            cls._note(group, reason)
        logger.warning("checkout needs resolution: %s", reason)

    @staticmethod
    def _note(group: BookingGroupModel, line: str) -> None:
        group.notes = f"{group.notes}\n[system] {line}" if group.notes else f"[system] {line}"

    @staticmethod
    async def _send_receipt(sender: EmailSender, checkout: CheckoutModel) -> None:
        customer = checkout.customer
        await send_quietly(
            sender,
            receipt_email(
                to=customer.email,
                first_name=customer.first_name,
                reference=checkout.reference_code,
                venue=checkout.venue.name,
                lines=[
                    ReceiptLine(
                        court=group.court.name,
                        starts_at_manila=group.slots[0].starts_at.astimezone(MANILA),
                        hours=len(group.slots),
                        price_centavos=group.price_centavos,
                    )
                    for group in checkout.bookings
                ],
                fee_centavos=checkout.fee_centavos,
                total_centavos=checkout.total_centavos,
            ),
        )


payment_service = PaymentService()
