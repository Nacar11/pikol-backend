import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, PaymentMethod, PaymentStatus
from src.bookings.domain.rules import slot_starts
from src.bookings.persistence.models import SLOT_CONSTRAINT, BookingGroupModel
from src.bookings.persistence.repository import booking_repository
from src.bookings.service_support import snapshot
from src.config.settings import get_settings
from src.email.port import EmailSender, send_quietly
from src.email.templates import receipt_email
from src.payments.domain.enums import PaymentOutcome, PaymentProviderName
from src.payments.domain.port import (
    PaymentNotFound,
    PaymentProvider,
    ProviderError,
    ProviderPayment,
)
from src.payments.persistence.repository import payment_repository
from src.utils.errors import AppError, constraint_of
from src.utils.time import MANILA

# Payloads are PII: log the event id and the outcome, never the body (§7.5).
logger = logging.getLogger("pikol.payments")


def provider_unavailable() -> AppError:
    return AppError(
        502,
        "PAYMENT_PROVIDER_UNAVAILABLE",
        "We could not reach the payment provider. Your slot is held — try paying again.",
    )


class PaymentService:
    async def start_checkout(
        self, session: AsyncSession, group: BookingGroupModel, provider: PaymentProvider
    ) -> str:
        """Open a checkout for a hold that is ALREADY COMMITTED.

        The provider call sits outside the booking transaction on purpose:
        holding one open across a network round-trip is how connection pools
        die (spec §5.3). If it fails, the hold stands and expires on its own.
        """
        try:
            checkout = await provider.create_checkout(
                amount_centavos=group.total_centavos,
                reference=group.reference_code,
                description=f"Pikol court booking {group.reference_code}",
                return_url=(
                    f"{get_settings().frontend_base_url}/checkout/return?booking_id={group.id}"
                ),
            )
        except ProviderError:
            logger.warning("checkout creation failed: booking=%s", group.reference_code)
            raise provider_unavailable() from None
        await payment_repository.add_checkout(
            session,
            booking_group_id=group.id,
            provider=provider.name,
            checkout=checkout,
            amount_centavos=group.total_centavos,
        )
        await session.commit()
        return checkout.checkout_url

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

        Shared by the webhook and the return page's refresh, so both paths go
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
        event_id = f"{payment.payment_id}:{payment.status}"
        event = await payment_repository.record_event(
            session,
            provider=provider.name,
            event_id=event_id,
            payload=event_payload if event_payload is not None else payment.raw,
        )
        if event is None:
            logger.info("payment event already processed: %s", event_id)
            return

        confirmed = await self._apply(session, provider.name, payment)
        event.processed_at = datetime.now(UTC)
        await session.commit()
        logger.info(
            "payment event processed: %s outcome=%s",
            event_id,
            "confirmed" if confirmed else "recorded",
        )
        if confirmed is not None:
            await self._send_receipt(sender, confirmed)

    async def _apply(
        self, session: AsyncSession, provider: PaymentProviderName, payment: ProviderPayment
    ) -> BookingGroupModel | None:
        """Apply a VERIFIED provider payment. Returns the group if this
        payment confirmed it (so the caller sends a receipt)."""
        row = await payment_repository.by_checkout_id(session, provider, payment.payment_id)
        if row is None:
            logger.warning("payment for an unknown checkout: %s", payment.payment_id)
            return None
        group = (
            (
                await session.execute(
                    select(BookingGroupModel)
                    .where(BookingGroupModel.id == row.booking_group_id)
                    # Lock only the group row: the eager-loaded court and
                    # player are outer joins, which Postgres refuses to lock.
                    .with_for_update(of=BookingGroupModel)
                )
            )
            .unique()
            .scalar_one()
        )
        row.status = payment.status
        row.provider_payment_id = payment.payment_id
        row.raw_payload = payment.raw

        if payment.status != PaymentOutcome.SUCCESSFUL:
            if (
                payment.status == PaymentOutcome.FAILED
                and group.payment_status == PaymentStatus.UNPAID
            ):
                group.payment_status = PaymentStatus.FAILED  # still retryable
            return None

        if (
            payment.amount_centavos != group.total_centavos
            or payment.reference != group.reference_code
        ):
            self._flag(group, f"payment {payment.payment_id} does not match amount/reference")
            return None
        if group.payment_status == PaymentStatus.PAID:
            self._flag(group, f"second payment {payment.payment_id} for a paid booking")
            return None
        return await self._confirm_or_flag(session, group, payment)

    async def _confirm_or_flag(
        self, session: AsyncSession, group: BookingGroupModel, payment: ProviderPayment
    ) -> BookingGroupModel | None:
        """Money exists. House it if at all possible (spec §5.5, ADR 0002 §2).

        A live hold confirms. A LAPSED hold — the expected case under Maya's
        one-hour checkout — re-acquires its slots if they are still in the
        future and free. Anything else is paid but unhousable: the group is
        flagged for a human, never silently dropped.
        """
        now = datetime.now(UTC)
        b = snapshot(group)
        paid = {"payment_status": PaymentStatus.PAID, "payment_method": PaymentMethod.ONLINE}

        if b.is_live_hold(now):
            await booking_repository.set_status(session, group, BookingStatus.CONFIRMED, **paid)
            return group

        if group.booking_status in (BookingStatus.PENDING, BookingStatus.EXPIRED) and (
            b.starts_at > now
        ):
            try:
                async with session.begin_nested():
                    await booking_repository.expire_lapsed(
                        session, group.court_id, slot_starts(b.starts_at, b.hours), now
                    )
                    await booking_repository.set_status(
                        session, group, BookingStatus.CONFIRMED, **paid
                    )
                logger.info("late payment re-acquired its slots: %s", group.reference_code)
                return group
            except IntegrityError as exc:
                if constraint_of(exc) != SLOT_CONSTRAINT:
                    raise
            await session.refresh(group)

        if group.booking_status == BookingStatus.PENDING:
            await booking_repository.set_status(session, group, BookingStatus.EXPIRED)
        group.payment_status = PaymentStatus.PAID
        group.payment_method = PaymentMethod.ONLINE
        self._flag(group, f"paid by {payment.payment_id} but the slots could not be held")
        return None

    @staticmethod
    def _flag(group: BookingGroupModel, reason: str) -> None:
        """Into the admin resolution queue (spec §5.5). A flag, not a status."""
        group.needs_resolution = True
        group.notes = f"{group.notes}\n[system] {reason}" if group.notes else f"[system] {reason}"
        logger.warning("booking needs resolution: %s — %s", group.reference_code, reason)

    @staticmethod
    async def _send_receipt(sender: EmailSender, group: BookingGroupModel) -> None:
        player = group.player
        if player is None:
            return
        b = snapshot(group)
        await send_quietly(
            sender,
            receipt_email(
                to=player.email,
                first_name=player.first_name,
                reference=group.reference_code,
                court=group.court.name,
                starts_at_manila=b.starts_at.astimezone(MANILA),
                hours=b.hours,
                price_centavos=group.price_centavos,
                fee_centavos=group.fee_centavos,
                total_centavos=group.total_centavos,
            ),
        )


payment_service = PaymentService()
