from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingGroupModel, BookingSlotModel
from src.email.adapters import MemoryEmailSender
from src.payments.adapters.mock import MockProvider
from src.payments.domain.enums import PaymentProviderName
from src.payments.persistence.repository import payment_repository
from tests.bookings.conftest import ADMIN, World, hold
from tests.bookings.helpers import insert_booking, next_hour
from tests.payments.helpers import group, pay

QUEUE = f"{ADMIN}/bookings/unfulfilled"


async def _lapse(session: AsyncSession, booking: dict[str, Any]) -> None:
    await session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == booking["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=20))
    )


async def test_lapsed_hold_with_free_slots_is_restored(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    """Maya's one-hour checkout makes this the EXPECTED late path (ADR 0002)."""
    booking = await hold(client, world.player, world.a.court.id, next_hour(), hours=2)
    await _lapse(db_session, booking)

    await pay(client, db_session, provider, booking)

    g = await group(db_session, booking)
    assert (g.booking_status, g.payment_status, g.needs_resolution) == ("CONFIRMED", "PAID", False)
    assert {s.booking_status for s in g.slots} == {"CONFIRMED"}
    assert any("receipt" in m.subject.lower() for m in outbox.outbox)


async def test_lapsed_hold_whose_slot_was_taken_is_flagged(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    start = next_hour()
    late = await hold(client, world.player, world.a.court.id, start, hours=2)
    await _lapse(db_session, late)
    taker = await hold(client, world.player2, world.a.court.id, start + timedelta(hours=1))

    await pay(client, db_session, provider, late)

    g = await group(db_session, late)
    other = await group(db_session, taker)
    assert (g.booking_status, g.payment_status, g.needs_resolution) == ("EXPIRED", "PAID", True)
    assert {s.booking_status for s in g.slots} == {"EXPIRED"}
    assert (other.booking_status, other.needs_resolution) == ("PENDING", False)  # untouched


async def test_payment_after_the_slot_started_is_flagged(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await _lapse(db_session, booking)
    started = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    await db_session.execute(
        update(BookingSlotModel)
        .where(BookingSlotModel.booking_group_id == booking["id"])
        .values(starts_at=started)
    )

    await pay(client, db_session, provider, booking)

    g = await group(db_session, booking)
    assert (g.booking_status, g.payment_status, g.needs_resolution) == ("EXPIRED", "PAID", True)


async def test_player_cancelled_then_paid_is_flagged(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await client.post(f"/api/v1/bookings/me/{booking['id']}/cancel", headers=world.player, json={})

    await pay(client, db_session, provider, booking)

    g = await group(db_session, booking)
    assert (g.booking_status, g.payment_status, g.needs_resolution) == ("CANCELLED", "PAID", True)


async def test_double_payment_is_flagged(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Paid twice (two tabs, two checkouts): the booking stays confirmed and a
    human refunds the extra."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await pay(client, db_session, provider, booking)
    extra = await provider.create_checkout(
        amount_centavos=booking["total_centavos"],
        reference=booking["reference_code"],
        description="x",
        return_url="http://r",
    )
    await payment_repository.add_checkout(
        db_session,
        booking_group_id=booking["id"],
        provider=PaymentProviderName.MOCK,
        checkout=extra,
        amount_centavos=booking["total_centavos"],
    )

    await pay(client, db_session, provider, booking)  # pays the latest: `extra`

    g = await group(db_session, booking)
    assert (g.booking_status, g.payment_status, g.needs_resolution) == ("CONFIRMED", "PAID", True)


async def test_queue_is_scoped_and_resolve_clears_it(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    ours = await insert_booking(
        db_session,
        court_id=world.a.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
    )
    theirs = await insert_booking(
        db_session,
        court_id=world.b.court.id,
        created_by=world.staff_a_user.id,
        starts_at=next_hour(),
    )
    for g in (ours, theirs):
        g.needs_resolution = True
    await db_session.flush()

    queue = await client.get(QUEUE, headers=world.staff_a)
    resolve = await client.post(
        f"{ADMIN}/bookings/{ours.id}/resolve",
        headers=world.staff_a,
        json={"resolution": "Refunded via Maya portal"},
    )
    again = await client.post(
        f"{ADMIN}/bookings/{ours.id}/resolve", headers=world.staff_a, json={"resolution": "x"}
    )
    after = await client.get(QUEUE, headers=world.staff_a)

    assert [b["id"] for b in queue.json()["data"]] == [ours.id]
    assert resolve.status_code == 200
    assert "Refunded via Maya portal" in resolve.json()["notes"]
    assert again.status_code == 409
    assert after.json()["data"] == []


async def test_player_cannot_see_the_queue(client: AsyncClient, world: World) -> None:
    assert (await client.get(QUEUE, headers=world.player)).status_code == 403
