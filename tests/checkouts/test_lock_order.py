"""Every path that locks a checkout and its payment takes the checkout first.
Cancel and the refund flow lock the checkout then write the payment; if the
webhook path locked the payment first, a void racing its own VOIDED webhook
could deadlock."""

from typing import Any

from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from src.payments.adapters.mock import MockProvider
from tests.bookings.conftest import World, hold
from tests.bookings.helpers import next_hour
from tests.payments.helpers import WEBHOOK, open_maya


async def test_the_webhook_locks_the_checkout_before_the_payment(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    provider.settle(payment_id, "PAYMENT_SUCCESS")
    locks: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if "FOR UPDATE" in statement:
            table = statement.split("FROM", 1)[1].split()[0]
            locks.append(table)

    engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", record)
    try:
        await client.post(WEBHOOK, content=provider.webhook_body(payment_id))
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert "checkouts" in locks and "payments" in locks
    assert locks.index("checkouts") < locks.index("payments")
