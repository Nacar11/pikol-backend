from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.bookings.domain.enums import BookingStatus, BookingType, PaymentMethod, PaymentStatus
from src.bookings.domain.rules import (
    can_cancel_by_player,
    can_cancel_by_staff,
    can_mark_paid,
    can_set_outcome,
    check_on_the_hour,
    check_player_window,
    check_walk_in_window,
    slot_starts,
)
from src.bookings.dto import (
    Availability,
    GridCourt,
    GridSlot,
    GridStatus,
    HoldRequest,
    WalkInRequest,
)
from src.bookings.persistence.models import BookingGroupModel
from src.bookings.persistence.repository import booking_repository
from src.bookings.service_support import snapshot
from src.courts.persistence.models import CourtModel
from src.courts.persistence.repository import court_repository
from src.parameters.service import parameter_service
from src.pricing.domain.pricing import convenience_fee_centavos
from src.pricing.service import pricing_service
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.utils.time import MANILA, manila_day_bounds
from src.venues.service import venue_service

# Players book across every venue; the court's own venue is checked for
# being active, not for belonging to the caller.
CATALOG = VenueScope.everything()


PLAYER_CANCEL_REASON = "Cancelled by player"


def court_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Court not found.")


def booking_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Booking not found.")


class BookingService:
    async def bookable_court(
        self, session: AsyncSession, court_id: int, *, scope: VenueScope
    ) -> CourtModel:
        """An active court at an active venue, inside `scope` — else 404."""
        court = await court_repository.get(session, court_id, scope=scope)
        if court is None or not court.is_active:
            raise court_not_found()
        await venue_service.get(session, court.venue_id, scope=scope, active_only=True)
        return court

    async def create_hold(
        self, session: AsyncSession, player: UserModel, body: HoldRequest
    ) -> BookingGroupModel:
        """POST /bookings — one transaction, in the order of spec §5.3."""
        now = datetime.now(UTC)
        params = await parameter_service.get(session)
        court = await self.bookable_court(session, body.court_id, scope=CATALOG)

        check_on_the_hour(body.starts_at)
        check_player_window(
            body.starts_at,
            body.hours,
            now,
            min_lead_minutes=params.min_lead_minutes,
            horizon_days=params.booking_horizon_days,
        )
        if (
            await booking_repository.count_live_holds(session, player.id, now)
            >= params.max_active_holds_per_player
        ):
            raise AppError(
                409,
                "HOLD_LIMIT",
                f"You already have {params.max_active_holds_per_player} unpaid holds. "
                f"Pay for or cancel one first.",
            )

        starts, slot_prices, price = await self._priced_slots(session, court.id, body)
        fee = convenience_fee_centavos(price, params.convenience_fee_percent)

        await booking_repository.expire_lapsed(session, court.id, starts, now)
        group = await booking_repository.insert(
            session,
            fields={
                "court_id": court.id,
                "player_id": player.id,
                "type": BookingType.PLAYER_BOOKING,
                "booking_status": BookingStatus.PENDING,
                "payment_status": PaymentStatus.UNPAID,
                "price_centavos": price,
                "fee_centavos": fee,
                "total_centavos": price + fee,
                "expires_at": now + timedelta(minutes=params.hold_duration_minutes),
                "created_by_user_id": player.id,
            },
            slot_prices=slot_prices,
        )
        await session.commit()
        return group

    async def _priced_slots(
        self, session: AsyncSession, court_id: int, body: HoldRequest
    ) -> tuple[list[datetime], list[tuple[datetime, int]], int]:
        """Each hour's price from the court's rules, frozen into the booking."""
        starts = slot_starts(body.starts_at, body.hours)
        resolver = (await pricing_service.resolvers_for(session, [court_id], scope=CATALOG))[
            court_id
        ]
        slot_prices = [(s, resolver.price_at(s)) for s in starts]
        return starts, slot_prices, sum(p for _, p in slot_prices)

    async def availability(self, session: AsyncSession, venue_id: int, day: date) -> Availability:
        """The whole day grid for a venue (spec §7.2): one occupancy query,
        one rules query, every price resolved in memory."""
        now = datetime.now(UTC)
        await venue_service.get(session, venue_id, scope=CATALOG, active_only=True)
        courts, _ = await court_repository.list_for_venue(
            session, venue_id, scope=CATALOG, page=1, limit=100, active_only=True
        )
        start, end = manila_day_bounds(day)
        hours = [start + timedelta(hours=h) for h in range(24)]
        court_ids = [c.id for c in courts]
        taken = await booking_repository.occupancy(
            session, court_ids, start, end, now, scope=CATALOG
        )
        resolvers = await pricing_service.resolvers_for(session, court_ids, scope=CATALOG)
        bookable_from = now + timedelta(
            minutes=(await parameter_service.get(session)).min_lead_minutes
        )

        def status(court_id: int, starts_at: datetime) -> GridStatus:
            occupant = taken.get((court_id, starts_at))
            if occupant == BookingType.MAINTENANCE:
                return "CLOSED"
            if occupant is not None:
                return "BOOKED"  # never WHO — that stays on staff endpoints
            if starts_at < bookable_from:
                return "PAST"
            return "AVAILABLE"

        return Availability(
            venue_id=venue_id,
            date=day,
            courts=[
                GridCourt(
                    court_id=c.id,
                    name=c.name,
                    slots=[
                        GridSlot(
                            starts_at=s,
                            hour=s.astimezone(MANILA).hour,
                            status=status(c.id, s),
                            price_centavos=resolvers[c.id].price_at(s),
                        )
                        for s in hours
                    ],
                )
                for c in courts
            ],
        )

    async def get_for_player(
        self, session: AsyncSession, player: UserModel, booking_id: int
    ) -> BookingGroupModel:
        group = await booking_repository.get_for_player(session, booking_id, player.id)
        if group is None:
            raise booking_not_found()  # someone else's booking looks exactly like none
        return group

    async def cancel_by_player(
        self, session: AsyncSession, player: UserModel, booking_id: int, reason: str | None
    ) -> BookingGroupModel:
        """Abandon a live hold, or cancel a confirmed booking before the
        cutoff. No refund (spec decision 9); the slot returns to inventory."""
        now = datetime.now(UTC)
        group = await self.get_for_player(session, player, booking_id)
        params = await parameter_service.get(session)
        can_cancel_by_player(
            snapshot(group), now, cutoff_minutes=params.cancellation_cutoff_minutes
        )
        await booking_repository.set_status(
            session,
            group,
            BookingStatus.CANCELLED,
            cancelled_at=now,
            cancelled_by_user_id=player.id,
            cancellation_reason=reason or PLAYER_CANCEL_REASON,
        )
        await session.commit()
        return group

    # ── staff ────────────────────────────────────────────────────────────

    async def get_scoped(
        self, session: AsyncSession, booking_id: int, *, scope: VenueScope
    ) -> BookingGroupModel:
        group = await booking_repository.get_scoped(session, booking_id, scope=scope)
        if group is None:
            raise booking_not_found()  # out of scope looks exactly like missing
        return group

    async def create_walk_in(
        self, session: AsyncSession, staff: UserModel, body: WalkInRequest, *, scope: VenueScope
    ) -> BookingGroupModel:
        """CONFIRMED/UNPAID immediately — it blocks the slot before money
        changes hands, which is why there are two status axes (spec §5.1)."""
        now = datetime.now(UTC)
        params = await parameter_service.get(session)
        court = await self.bookable_court(session, body.court_id, scope=scope)
        check_on_the_hour(body.starts_at)
        check_walk_in_window(body.starts_at, now, backdate_days=params.staff_backdate_days)
        starts, slot_prices, price = await self._priced_slots(session, court.id, body)

        await booking_repository.expire_lapsed(session, court.id, starts, now)
        group = await booking_repository.insert(
            session,
            fields={
                "court_id": court.id,
                "player_id": None,
                "type": BookingType.WALK_IN,
                "booking_status": BookingStatus.CONFIRMED,
                "payment_status": PaymentStatus.UNPAID,
                "price_centavos": price,
                "fee_centavos": 0,  # walk-ins pay no convenience fee
                "total_centavos": price,
                "notes": body.notes,
                "created_by_user_id": staff.id,
            },
            slot_prices=slot_prices,
        )
        await session.commit()
        return group

    async def cancel_by_staff(
        self,
        session: AsyncSession,
        staff: UserModel,
        booking_id: int,
        reason: str,
        *,
        scope: VenueScope,
    ) -> BookingGroupModel:
        group = await self.get_scoped(session, booking_id, scope=scope)
        can_cancel_by_staff(snapshot(group))
        await booking_repository.set_status(
            session,
            group,
            BookingStatus.CANCELLED,
            cancelled_at=datetime.now(UTC),
            cancelled_by_user_id=staff.id,
            cancellation_reason=reason,
        )
        await session.commit()
        return group

    async def mark_paid(
        self,
        session: AsyncSession,
        booking_id: int,
        method: PaymentMethod,
        *,
        scope: VenueScope,
    ) -> BookingGroupModel:
        group = await self.get_scoped(session, booking_id, scope=scope)
        can_mark_paid(snapshot(group))
        # payment_status, not booking_status — so not a set_status call.
        group.payment_status = PaymentStatus.PAID
        group.payment_method = method
        await session.commit()
        await session.refresh(group)
        return group

    async def set_outcome(
        self,
        session: AsyncSession,
        booking_id: int,
        outcome: BookingStatus,
        *,
        scope: VenueScope,
    ) -> BookingGroupModel:
        group = await self.get_scoped(session, booking_id, scope=scope)
        can_set_outcome(snapshot(group), datetime.now(UTC))
        await booking_repository.set_status(session, group, outcome)
        await session.commit()
        return group


booking_service = BookingService()
