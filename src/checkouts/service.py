from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentStatus
from src.bookings.domain.errors import BookingError
from src.bookings.domain.rules import check_player_window, slot_starts
from src.bookings.persistence.repository import booking_repository
from src.bookings.service import CATALOG, booking_service
from src.checkouts.domain.rules import CheckoutItem, allocate_fee, check_items
from src.checkouts.dto import CheckoutCreate, CheckoutOut
from src.checkouts.persistence.models import CheckoutModel
from src.checkouts.persistence.repository import checkout_repository
from src.courts.persistence.models import CourtModel
from src.parameters.service import parameter_service
from src.payments.domain.enums import PaymentOutcome
from src.payments.persistence.repository import payment_repository
from src.pricing.domain.pricing import convenience_fee_centavos
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.utils.time import MANILA


def checkout_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Booking not found.")


class CheckoutService:
    async def create(
        self, session: AsyncSession, customer: UserModel, body: CheckoutCreate
    ) -> CheckoutModel:
        """ "Pay now": hold every picked hour for hold_duration_minutes, all or
        nothing, and freeze the prices. Maya is not called here — the hold
        page comes first (design call 4)."""
        now = datetime.now(UTC)
        params = await parameter_service.get(session)
        items = [CheckoutItem(i.court_id, i.starts_at, i.hours) for i in body.items]
        courts = {
            c: await booking_service.bookable_court(session, c, scope=CATALOG)
            for c in dict.fromkeys(i.court_id for i in items)
        }
        if len({c.venue_id for c in courts.values()}) > 1:
            raise BookingError(
                "MIXED_VENUES",
                "One payment covers one venue. Book the other separately.",
                "invalid",
            )
        check_items(items)
        for item in items:
            check_player_window(
                item.starts_at,
                item.hours,
                now,
                min_lead_minutes=params.min_lead_minutes,
                horizon_days=params.booking_horizon_days,
            )
        if (
            await checkout_repository.count_live(session, customer.id, now)
            >= params.max_active_holds_per_player
        ):
            raise AppError(
                409,
                "HOLD_LIMIT",
                f"You already have {params.max_active_holds_per_player} unpaid holds. "
                f"Pay for or cancel one first.",
            )

        priced = [
            await booking_service.priced_slots(session, i.court_id, i.starts_at, i.hours)
            for i in items
        ]
        prices = [sum(p for _, p in slots) for slots in priced]
        price = sum(prices)
        fee = convenience_fee_centavos(price, params.convenience_fee_percent)
        expires_at = now + timedelta(minutes=params.hold_duration_minutes)

        try:
            # One savepoint around every item, so a collision on the last
            # court also undoes the first: nothing is held unless all is.
            async with session.begin_nested():
                checkout = await checkout_repository.insert(
                    session,
                    fields={
                        "customer_id": customer.id,
                        "venue_id": next(iter(courts.values())).venue_id,
                        "status": PaymentOutcome.PENDING,
                        "price_centavos": price,
                        "fee_centavos": fee,
                        "total_centavos": price + fee,
                        "expires_at": expires_at,
                    },
                )
                for item, slots, item_price, item_fee in zip(
                    items, priced, prices, allocate_fee(fee, prices), strict=True
                ):
                    await booking_repository.expire_lapsed(
                        session, item.court_id, [s for s, _ in slots], now
                    )
                    await booking_repository.insert(
                        session,
                        fields={
                            "court_id": item.court_id,
                            "player_id": customer.id,
                            "checkout_id": checkout.id,
                            "type": BookingType.PLAYER_BOOKING,
                            "booking_status": BookingStatus.PENDING,
                            "payment_status": PaymentStatus.UNPAID,
                            "price_centavos": item_price,
                            "fee_centavos": item_fee,
                            "total_centavos": item_price + item_fee,
                            "expires_at": expires_at,
                            "created_by_user_id": customer.id,
                        },
                        slot_prices=slots,
                    )
        except AppError as exc:
            if exc.code != "SLOT_UNAVAILABLE":
                raise
            raise await self._taken(session, items, courts, now) from None
        await session.commit()
        await session.refresh(checkout, attribute_names=["bookings"])
        return checkout

    @staticmethod
    async def _taken(
        session: AsyncSession,
        items: list[CheckoutItem],
        courts: dict[int, CourtModel],
        now: datetime,
    ) -> AppError:
        """Name the hours someone else holds, so the customer knows which
        picks to drop. Read after the savepoint rolled back our own rows."""
        wanted = [(i.court_id, s) for i in items for s in slot_starts(i.starts_at, i.hours)]
        start = min(s for _, s in wanted)
        end = max(s for _, s in wanted) + timedelta(hours=1)
        taken = await booking_repository.occupancy(
            session, list(courts), start, end, now, scope=CATALOG
        )
        names = [
            f"{courts[c].name} · {s.astimezone(MANILA).strftime('%a %d %b, %-I %p')}"
            for c, s in sorted(wanted, key=lambda w: (w[1], courts[w[0]].name))
            if (c, s) in taken
        ]
        return AppError(
            409,
            "SLOT_UNAVAILABLE",
            "These hours were just taken: " + "; ".join(names)
            if names
            else "That slot is no longer available.",
        )

    async def get_mine(
        self, session: AsyncSession, customer: UserModel, checkout_id: int
    ) -> CheckoutModel:
        checkout = await checkout_repository.get_for_customer(session, checkout_id, customer.id)
        if checkout is None:
            raise checkout_not_found()  # someone else's looks exactly like none
        return checkout

    async def out(self, session: AsyncSession, checkout: CheckoutModel) -> CheckoutOut:
        payment = await payment_repository.latest_for_checkout(session, checkout.id)
        refund = (
            await payment_repository.refund_for(session, payment.id)
            if payment is not None
            else None
        )
        return CheckoutOut.of(
            checkout,
            datetime.now(UTC),
            checkout_url=payment.checkout_url if payment is not None else None,
            refund_kind=refund.kind if refund is not None else None,
        )


checkout_service = CheckoutService()
