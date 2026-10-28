from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType
from src.action_events.service import event_service
from src.auth.scope import VenueScope
from src.bookings.domain.enums import BookingStatus, BookingType, PaymentMethod, PaymentStatus
from src.bookings.domain.errors import BookingError
from src.bookings.domain.rules import (
    SLOT,
    can_cancel_by_staff,
    can_cancel_closure,
    can_mark_paid,
    can_set_outcome,
    check_on_the_hour,
    check_walk_in_window,
    slot_starts,
)
from src.bookings.dto import (
    MAX_CLOSURE,
    Availability,
    ClosureRequest,
    DayOccupancy,
    GridCourt,
    GridSlot,
    GridStatus,
    WalkInRequest,
)
from src.bookings.persistence.models import BookingGroupModel
from src.bookings.persistence.repository import booking_repository
from src.bookings.service_support import snapshot
from src.courts.persistence.models import CourtModel
from src.courts.persistence.repository import court_repository
from src.courts.service import court_service
from src.parameters.service import parameter_service
from src.pricing.service import pricing_service
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.utils.time import MANILA, manila_day_bounds
from src.venues.service import venue_service

# Players book across every venue; the court's own venue is checked for
# being active, not for belonging to the caller.
CATALOG = VenueScope.everything()


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

    async def priced_slots(
        self, session: AsyncSession, court_id: int, starts_at: datetime, hours: int
    ) -> list[tuple[datetime, int]]:
        """Each hour's price from the court's rules, frozen into the booking."""
        resolver = (await pricing_service.resolvers_for(session, [court_id], scope=CATALOG))[
            court_id
        ]
        return [(s, resolver.price_at(s)) for s in slot_starts(starts_at, hours)]

    async def daily_occupancy(
        self, session: AsyncSession, venue_id: int, first_day: date, days: int, *, scope: VenueScope
    ) -> list[DayOccupancy]:
        """Booked and bookable court-hours per Manila day, for the console's
        two-week strip. Built on `occupancy`, so a lapsed hold never counts.
        A closure is not a booking: it shrinks `bookable_hours` instead."""
        await venue_service.get(session, venue_id, scope=scope, active_only=False)
        courts, _ = await court_repository.list_for_venue(
            session, venue_id, scope=scope, page=1, limit=100, active_only=True
        )
        start, _ = manila_day_bounds(first_day)
        _, end = manila_day_bounds(first_day + timedelta(days=days - 1))
        taken = await booking_repository.occupancy(
            session, [c.id for c in courts], start, end, datetime.now(UTC), scope=scope
        )
        booked: dict[date, int] = {}
        closed: dict[date, int] = {}
        for (_, starts_at), type_ in taken.items():
            day = starts_at.astimezone(MANILA).date()
            bucket = closed if type_ == BookingType.MAINTENANCE else booked
            bucket[day] = bucket.get(day, 0) + 1
        return [
            DayOccupancy(
                date=day,
                booked_hours=booked.get(day, 0),
                bookable_hours=len(courts) * 24 - closed.get(day, 0),
            )
            for day in (first_day + timedelta(days=i) for i in range(days))
        ]

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
        params = await parameter_service.get(session)
        bookable_from = now + timedelta(minutes=params.min_lead_minutes)

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
            convenience_fee_percent=params.convenience_fee_percent,
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
        slot_prices = await self.priced_slots(session, court.id, body.starts_at, body.hours)
        starts = [s for s, _ in slot_prices]
        price = sum(p for _, p in slot_prices)

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
        await event_service.record(
            session,
            ActionEventType.WALK_IN_BOOKED,
            actor=staff,
            group=group,
            data={"notes": body.notes} if body.notes else None,
            occurred_at=now,
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
        return await self._cancel(session, group, staff, reason, datetime.now(UTC))

    async def mark_paid(
        self,
        session: AsyncSession,
        staff: UserModel,
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
        await event_service.record(
            session,
            ActionEventType.MARKED_PAID,
            actor=staff,
            group=group,
            data={"method": method, "amount_centavos": group.total_centavos},
        )
        await session.commit()
        await session.refresh(group)
        return group

    async def set_outcome(
        self,
        session: AsyncSession,
        staff: UserModel,
        booking_id: int,
        outcome: BookingStatus,
        *,
        scope: VenueScope,
    ) -> BookingGroupModel:
        group = await self.get_scoped(session, booking_id, scope=scope)
        can_set_outcome(snapshot(group), datetime.now(UTC))
        await booking_repository.set_status(session, group, outcome)
        await event_service.record(
            session,
            ActionEventType.COMPLETED
            if outcome == BookingStatus.COMPLETED
            else ActionEventType.NO_SHOW,
            actor=staff,
            group=group,
        )
        await session.commit()
        return group

    async def resolve(
        self,
        session: AsyncSession,
        staff: UserModel,
        booking_id: int,
        resolution: str,
        *,
        scope: VenueScope,
    ) -> BookingGroupModel:
        """Take a booking out of the resolution queue (spec §5.5). The flag
        clears; the record of what was done stays in the notes."""
        group = await self.get_scoped(session, booking_id, scope=scope)
        if not group.needs_resolution:
            raise AppError(409, "NOT_FLAGGED", "This booking is not awaiting resolution.")
        line = f"[resolved by user {staff.id}] {resolution}"
        group.notes = f"{group.notes}\n{line}" if group.notes else line
        group.needs_resolution = False
        await event_service.record(
            session,
            ActionEventType.RESOLVED,
            actor=staff,
            group=group,
            data={"resolution": resolution},
        )
        await session.commit()
        await session.refresh(group)
        return group

    async def close_courts(
        self, session: AsyncSession, staff: UserModel, body: ClosureRequest, *, scope: VenueScope
    ) -> list[BookingGroupModel]:
        """All or nothing. A closure can never evict a booking: the slot
        index refuses it, and staff must cancel the booking first, on the
        record (spec §6.5)."""
        now = datetime.now(UTC)
        check_on_the_hour(body.starts_at)
        check_on_the_hour(body.ends_at)
        hours = int((body.ends_at - body.starts_at) / SLOT)
        if hours < 1:
            raise BookingError("EMPTY_RANGE", "The closure must end after it starts.", "invalid")
        if hours > MAX_CLOSURE * 24:
            raise BookingError(
                "RANGE_TOO_LONG", f"Close at most {MAX_CLOSURE} days at a time.", "invalid"
            )
        if body.starts_at < now.replace(minute=0, second=0, microsecond=0):
            raise BookingError("IN_THE_PAST", "A closure cannot start in the past.", "invalid")

        court_ids = list(dict.fromkeys(body.court_ids))  # dedupe, keep order
        for court_id in court_ids:
            await court_service.get(session, court_id, scope=scope)  # 404 if not yours
        starts = slot_starts(body.starts_at, hours)

        groups = []
        # One savepoint around every court, so a collision on the third
        # court also undoes the first two.
        async with session.begin_nested():
            for court_id in court_ids:
                await booking_repository.expire_lapsed(session, court_id, starts, now)
                closure = await booking_repository.insert(
                    session,
                    fields={
                        "court_id": court_id,
                        "player_id": None,
                        "type": BookingType.MAINTENANCE,
                        "booking_status": BookingStatus.CONFIRMED,
                        "payment_status": PaymentStatus.UNPAID,
                        "price_centavos": 0,
                        "fee_centavos": 0,
                        "total_centavos": 0,
                        "notes": body.reason,
                        "created_by_user_id": staff.id,
                    },
                    slot_prices=[(s, 0) for s in starts],
                )
                await event_service.record(
                    session,
                    ActionEventType.CLOSURE_CREATED,
                    actor=staff,
                    group=closure,
                    data={"reason": body.reason},
                    occurred_at=now,
                )
                groups.append(closure)
        await session.commit()
        return groups

    async def reopen(
        self,
        session: AsyncSession,
        staff: UserModel,
        closure_id: int,
        reason: str,
        *,
        scope: VenueScope,
    ) -> BookingGroupModel:
        group = await self.get_scoped(session, closure_id, scope=scope)
        can_cancel_closure(snapshot(group))
        return await self._cancel(session, group, staff, reason, datetime.now(UTC))

    async def _cancel(
        self,
        session: AsyncSession,
        group: BookingGroupModel,
        by: UserModel,
        reason: str,
        now: datetime,
    ) -> BookingGroupModel:
        await booking_repository.set_status(
            session,
            group,
            BookingStatus.CANCELLED,
            cancelled_at=now,
            cancelled_by_user_id=by.id,
            cancellation_reason=reason,
        )
        await event_service.record(
            session,
            ActionEventType.REOPENED
            if group.type == BookingType.MAINTENANCE
            else ActionEventType.CANCELLED,
            actor=by,
            group=group,
            data={"reason": reason},
            occurred_at=now,
        )
        await session.commit()
        return group


booking_service = BookingService()
