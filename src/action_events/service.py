from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType, ActorKind
from src.action_events.persistence.repository import action_event_repository
from src.bookings.persistence.models import BookingGroupModel
from src.checkouts.persistence.models import CheckoutModel
from src.users.persistence.models import UserModel


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


event_service = EventService()
