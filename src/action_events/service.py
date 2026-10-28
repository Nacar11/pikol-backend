from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType, ActorKind
from src.action_events.dto import ActionEventOut, DerivedType, EventActor
from src.action_events.persistence.models import ActionEventModel
from src.action_events.persistence.repository import action_event_repository
from src.bookings.domain.enums import BookingStatus, BookingType
from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.persistence.models import CheckoutModel
from src.users.persistence.models import UserModel

# The resolution queue is the venue's business, not the customer's.
STAFF_ONLY = frozenset({ActionEventType.FLAGGED, ActionEventType.RESOLVED})
SYSTEM = EventActor(kind=ActorKind.SYSTEM, name=None)


def _derived(group: BookingGroupModel, now: datetime) -> list[tuple[DerivedType, datetime]]:
    """ "Play started" mirrors the derived IN_PROGRESS; "Hold ended" covers a
    lapsed hold nobody's write has cleaned up yet (the PENDING trap)."""
    out: list[tuple[DerivedType, datetime]] = []
    first = group.slots[0].starts_at if group.slots else None
    if (
        group.type != BookingType.MAINTENANCE
        and group.booking_status in (BookingStatus.CONFIRMED, BookingStatus.COMPLETED)
        and first is not None
        and first <= now
    ):
        out.append(("PLAY_STARTED", first))
    if (
        group.booking_status == BookingStatus.PENDING
        and group.expires_at is not None
        and group.expires_at <= now
    ):
        out.append(("HOLD_ENDED", group.expires_at))
    return out


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in data.items()}


class EventService:
    async def record(
        self,
        session: AsyncSession,
        type: ActionEventType,
        *,
        actor: UserModel | None,
        group: BookingGroupModel | None = None,
        checkout: CheckoutModel | None = None,
        data: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Write one event. Call it BEFORE the change's commit, so the event
        and the change land together or not at all.

        No actor is SYSTEM; the booking's player or the checkout's customer
        is CUSTOMER; anyone else is STAFF. A checkout alone (no group) is a
        payment event, recorded once for the whole checkout."""
        if group is not None:
            customer_id, checkout_id = group.player_id, group.checkout_id
        elif checkout is not None:
            customer_id, checkout_id = checkout.customer_id, checkout.id
        else:
            customer_id, checkout_id = None, None
        if actor is None:
            kind = ActorKind.SYSTEM
        elif actor.id == customer_id:
            kind = ActorKind.CUSTOMER
        else:
            kind = ActorKind.STAFF
        await action_event_repository.insert(
            session,
            type=type,
            actor_kind=kind,
            actor_user_id=actor.id if actor is not None else None,
            occurred_at=occurred_at or datetime.now(UTC),
            booking_group_id=group.id if group is not None else None,
            checkout_id=checkout_id,
            data=_jsonable(data or {}),
        )

    async def staff_timeline(
        self, session: AsyncSession, group: BookingGroupModel, now: datetime
    ) -> list[ActionEventOut]:
        """Everything, with names. Newest first."""
        events = await action_event_repository.for_booking(session, group.id, group.checkout_id)
        names = await self._names(session, events)

        def actor(e: ActionEventModel) -> EventActor:
            if e.actor_user_id is None:
                return SYSTEM
            return EventActor(kind=e.actor_kind, name=names.get(e.actor_user_id))

        return self._timeline(events, [group], actor, now)

    async def customer_timeline(
        self, session: AsyncSession, checkout: CheckoutModel, now: datetime
    ) -> list[ActionEventOut]:
        """The whole payment, every court. No resolution queue, no staff
        names: staff are "Venue staff" and the customer is "You"."""
        events = [
            e
            for e in await action_event_repository.for_checkout(session, checkout.id)
            if e.type not in STAFF_ONLY
        ]

        def actor(e: ActionEventModel) -> EventActor:
            if e.actor_kind == ActorKind.SYSTEM:
                return SYSTEM
            name = "You" if e.actor_kind == ActorKind.CUSTOMER else "Venue staff"
            return EventActor(kind=e.actor_kind, name=name)

        return self._timeline(events, list(checkout.bookings), actor, now)

    @staticmethod
    def _timeline(
        events: list[ActionEventModel],
        groups: list[BookingGroupModel],
        actor: Callable[[ActionEventModel], EventActor],
        now: datetime,
    ) -> list[ActionEventOut]:
        by_id = {g.id: g for g in groups}

        def about(group_id: int | None) -> dict[str, Any]:
            g = by_id.get(group_id) if group_id is not None else None
            return {
                "booking_id": group_id,
                "reference_code": g.reference_code if g else None,
                "court_name": g.court.name if g else None,
            }

        out = [
            ActionEventOut(
                id=e.id,
                type=e.type,
                occurred_at=e.occurred_at,
                actor=actor(e),
                data=e.data,
                derived=False,
                **about(e.booking_group_id),
            )
            for e in events
        ]
        out += [
            ActionEventOut(
                id=None,
                type=type_,
                occurred_at=at,
                actor=SYSTEM,
                data={},
                derived=True,
                **about(g.id),
            )
            for g in groups
            for type_, at in _derived(g, now)
        ]
        # Stable: equal times keep stored order (already newest first).
        return sorted(out, key=lambda e: e.occurred_at, reverse=True)

    @staticmethod
    async def _names(session: AsyncSession, events: list[ActionEventModel]) -> dict[int, str]:
        ids = {e.actor_user_id for e in events if e.actor_user_id is not None}
        if not ids:
            return {}
        rows = await session.execute(
            select(UserModel.id, UserModel.first_name, UserModel.last_name).where(
                UserModel.id.in_(ids)
            )
        )
        return {uid: f"{first} {last}" for uid, first, last in rows}


event_service = EventService()
