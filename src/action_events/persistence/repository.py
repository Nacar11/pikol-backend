from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.domain.enums import ActionEventType, ActorKind
from src.action_events.persistence.models import ActionEventModel


class ActionEventRepository:
    """Append-only: insert and read. Nothing here updates or deletes."""

    async def insert(
        self,
        session: AsyncSession,
        *,
        type: ActionEventType,
        actor_kind: ActorKind,
        actor_user_id: int | None,
        occurred_at: datetime,
        booking_group_id: int | None = None,
        checkout_id: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> ActionEventModel:
        event = ActionEventModel(
            type=type,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            booking_group_id=booking_group_id,
            checkout_id=checkout_id,
            data=data or {},
        )
        session.add(event)
        await session.flush()
        return event

    async def for_booking(
        self, session: AsyncSession, booking_group_id: int, checkout_id: int | None
    ) -> list[ActionEventModel]:
        """The booking's own events plus its checkout's booking-less ones
        (the payment events), newest first."""
        subject = ActionEventModel.booking_group_id == booking_group_id
        if checkout_id is not None:
            subject = or_(
                subject,
                and_(
                    ActionEventModel.booking_group_id.is_(None),
                    ActionEventModel.checkout_id == checkout_id,
                ),
            )
        return await self._newest_first(session, subject)

    async def for_checkout(self, session: AsyncSession, checkout_id: int) -> list[ActionEventModel]:
        return await self._newest_first(session, ActionEventModel.checkout_id == checkout_id)

    @staticmethod
    async def _newest_first(session: AsyncSession, where: Any) -> list[ActionEventModel]:
        rows = await session.execute(
            select(ActionEventModel)
            .where(where)
            .order_by(ActionEventModel.occurred_at.desc(), ActionEventModel.id.desc())
        )
        return list(rows.scalars().all())


action_event_repository = ActionEventRepository()
