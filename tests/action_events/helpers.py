from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.action_events.persistence.models import ActionEventModel


async def events_of(
    session: AsyncSession, *, group_id: int | None = None, checkout_id: int | None = None
) -> list[ActionEventModel]:
    """Every event about this booking or checkout, oldest first."""
    clauses = []
    if group_id is not None:
        clauses.append(ActionEventModel.booking_group_id == group_id)
    if checkout_id is not None:
        clauses.append(ActionEventModel.checkout_id == checkout_id)
    rows = await session.execute(
        select(ActionEventModel)
        .where(or_(*clauses))
        .order_by(ActionEventModel.occurred_at, ActionEventModel.id)
    )
    return list(rows.scalars().all())


async def types_of(session: AsyncSession, **subject: int) -> list[str]:
    return [e.type for e in await events_of(session, **subject)]
