"""ADR 0004's test scenarios, against the mock Maya."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingSlotModel
from src.email.adapters import MemoryEmailSender
from src.payments.adapters.mock import MockProvider
from src.payments.domain.enums import RefundKind
from src.payments.persistence.models import PaymentModel, WebhookEventModel
from src.utils.api import API_PREFIX
from tests.bookings.conftest import ADMIN, CHECKOUTS, World, hold
from tests.bookings.helpers import next_hour
from tests.checkouts.helpers import action, cell, item, lapse
from tests.payments.helpers import WEBHOOK, checkout, groups, open_maya, pay

MOCK = f"{API_PREFIX}/mock-maya/checkouts"


def _receipts(outbox: MemoryEmailSender) -> list[Any]:
    return [m for m in outbox.outbox if "receipt" in m.subject.lower()]


async def _read(client: AsyncClient, world: World, checkout_id: int) -> dict[str, Any]:
    response = await client.get(f"{CHECKOUTS}/{checkout_id}", headers=world.player)
    body: dict[str, Any] = response.json()
    return body


async def _two_courts(client: AsyncClient, world: World, start: datetime) -> int:
    response = await client.post(
        CHECKOUTS,
        headers=world.player,
        json={"items": [item(world.a.court.id, start, 2), item(world.a.court2.id, start)]},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def _events(session: AsyncSession) -> int:
    """Events this test recorded. now() is the transaction's start, so rows
    already committed to a developer's database fall outside it."""
    return (
        await session.execute(
            select(func.count())
            .select_from(WebhookEventModel)
            .where(WebhookEventModel.received_at >= func.now())
        )
    ).scalar_one()


async def test_paid_within_the_hold_confirms_every_booking(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    """Scenario 1."""
    checkout_id = await _two_courts(client, world, next_hour())

    response = await pay(client, db_session, provider, world.player, checkout_id)

    assert response.status_code == 200
    assert (await _read(client, world, checkout_id))["status"] == "SUCCESSFUL"
    for g in await groups(db_session, checkout_id):
        assert (g.booking_status, g.payment_status, g.payment_method) == (
            "CONFIRMED",
            "PAID",
            "ONLINE",
        )
        assert g.expires_at is None
        assert {s.booking_status for s in g.slots} == {"CONFIRMED"}
    receipts = _receipts(outbox)
    assert len(receipts) == 1
    assert receipts[0].to == world.player_user.email
    assert world.a.court.name in receipts[0].body and world.a.court2.name in receipts[0].body
    payment = (
        await db_session.execute(
            select(PaymentModel).where(PaymentModel.checkout_id == checkout_id)
        )
    ).scalar_one()
    await db_session.refresh(payment)
    assert (payment.status, payment.provider_status) == ("SUCCESSFUL", "PAYMENT_SUCCESS")


async def test_backing_out_frees_the_hours_at_once(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 3."""
    start = next_hour()
    booking = await hold(client, world.player, world.a.court.id, start)

    await pay(
        client, db_session, provider, world.player, booking["checkout_id"], "PAYMENT_CANCELLED"
    )

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "CANCELLED"
    (g,) = await groups(db_session, booking["checkout_id"])
    assert g.booking_status == "CANCELLED"
    assert g.cancellation_reason == "Backed out of payment"
    assert await cell(client, world, world.a.court.id, start) == "AVAILABLE"


async def test_decline_fails_and_frees_the_hours(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 4."""
    start = next_hour()
    booking = await hold(client, world.player, world.a.court.id, start)

    await pay(client, db_session, provider, world.player, booking["checkout_id"], "PAYMENT_FAILED")

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "FAILED"
    (g,) = await groups(db_session, booking["checkout_id"])
    assert (g.booking_status, g.cancellation_reason) == ("CANCELLED", "Payment failed")
    await hold(client, world.player2, world.a.court.id, start)  # anyone, at once


async def test_auth_failed_is_a_failure(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    await pay(client, db_session, provider, world.player, booking["checkout_id"], "AUTH_FAILED")

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "FAILED"


async def test_after_a_failure_pay_now_makes_a_new_checkout(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 5: no carry-over from the failed one."""
    start = next_hour()
    failed = await hold(client, world.player, world.a.court.id, start)
    await pay(client, db_session, provider, world.player, failed["checkout_id"], "PAYMENT_FAILED")

    again = await hold(client, world.player, world.a.court.id, start)

    assert again["checkout_id"] != failed["checkout_id"]
    assert again["checkout"]["reference_code"] != failed["checkout"]["reference_code"]
    assert again["checkout"]["status"] == "PENDING"
    assert (await _read(client, world, failed["checkout_id"]))["status"] == "FAILED"


async def test_hold_end_reads_cancelled_and_maya_expiry_changes_nothing(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 6."""
    start = next_hour()
    booking = await hold(client, world.player, world.a.court.id, start)
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    await lapse(db_session, booking["checkout_id"])

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "CANCELLED"
    assert await cell(client, world, world.a.court.id, start) == "AVAILABLE"

    provider.settle(payment_id, "PAYMENT_EXPIRED")
    await client.post(WEBHOOK, content=provider.webhook_body(payment_id))

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "CANCELLED"
    (g,) = await groups(db_session, booking["checkout_id"])
    assert g.booking_status == "EXPIRED"
    assert g.needs_resolution is False


async def test_late_payment_with_free_hours_is_booked(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    """Scenario 7."""
    checkout_id = await _two_courts(client, world, next_hour())
    await open_maya(client, world.player, checkout_id)
    await lapse(db_session, checkout_id, minutes=7)

    await pay(client, db_session, provider, world.player, checkout_id)

    assert (await _read(client, world, checkout_id))["status"] == "SUCCESSFUL"
    assert {
        (g.booking_status, g.payment_status) for g in await groups(db_session, checkout_id)
    } == {("CONFIRMED", "PAID")}
    assert len(_receipts(outbox)) == 1


async def test_late_payment_with_a_taken_hour_is_voided_same_day(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    """Scenario 8."""
    start = next_hour()
    late = await hold(client, world.player, world.a.court.id, start)
    payment_id = await open_maya(client, world.player, late["checkout_id"])
    await lapse(db_session, late["checkout_id"])
    taker = await hold(client, world.player2, world.a.court.id, start)

    await pay(client, db_session, provider, world.player, late["checkout_id"])

    body = await _read(client, world, late["checkout_id"])
    assert (body["status"], body["refund_kind"]) == ("REFUNDED", "VOID")
    (g,) = await groups(db_session, late["checkout_id"])
    assert (g.booking_status, g.needs_resolution) == ("EXPIRED", False)
    assert "VOID" in (g.notes or "")
    (other,) = await groups(db_session, taker["checkout_id"])
    assert other.booking_status == "PENDING"  # untouched
    kind, _, amount = provider.undone[payment_id]
    assert (kind, amount) == (RefundKind.VOID, late["checkout"]["total_centavos"])
    assert _receipts(outbox) == []


async def test_one_taken_hour_refunds_the_whole_payment(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 9: two courts, only one taken — neither is booked."""
    start = next_hour()
    checkout_id = await _two_courts(client, world, start)
    await open_maya(client, world.player, checkout_id)
    await lapse(db_session, checkout_id)
    await hold(client, world.player2, world.a.court2.id, start)

    await pay(client, db_session, provider, world.player, checkout_id)

    assert (await _read(client, world, checkout_id))["status"] == "REFUNDED"
    assert {g.booking_status for g in await groups(db_session, checkout_id)} == {"EXPIRED"}
    assert await cell(client, world, world.a.court.id, start) == "AVAILABLE"


async def test_late_payment_after_midnight_is_refunded_not_voided(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 10."""
    start = next_hour()
    late = await hold(client, world.player, world.a.court.id, start)
    payment_id = await open_maya(client, world.player, late["checkout_id"])
    await lapse(db_session, late["checkout_id"])
    await hold(client, world.player2, world.a.court.id, start)

    await pay(
        client,
        db_session,
        provider,
        world.player,
        late["checkout_id"],
        paid_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert (await _read(client, world, late["checkout_id"]))["refund_kind"] == "REFUND"
    kind, _, amount = provider.undone[payment_id]
    assert (kind, amount) == (RefundKind.REFUND, late["checkout"]["total_centavos"])


async def test_void_failure_is_flagged_for_staff(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 11: never silently dropped."""
    start = next_hour()
    late = await hold(client, world.player, world.a.court.id, start)
    await open_maya(client, world.player, late["checkout_id"])
    await lapse(db_session, late["checkout_id"])
    await hold(client, world.player2, world.a.court.id, start)
    provider.fail_undo = True

    await pay(client, db_session, provider, world.player, late["checkout_id"])

    assert (await _read(client, world, late["checkout_id"]))["status"] != "REFUNDED"
    (g,) = await groups(db_session, late["checkout_id"])
    assert g.needs_resolution is True
    assert "refund by hand" in (g.notes or "")
    queue = await client.get(f"{ADMIN}/bookings/unfulfilled", headers=world.staff_a)
    assert g.id in [b["id"] for b in queue.json()["data"]]


async def test_duplicate_webhook_applies_once(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    outbox: MemoryEmailSender,
) -> None:
    """Scenario 13."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    provider.settle(payment_id, "PAYMENT_SUCCESS")

    first = await client.post(WEBHOOK, content=provider.webhook_body(payment_id))
    again = await client.post(WEBHOOK, content=provider.webhook_body(payment_id))

    assert first.status_code == again.status_code == 200
    assert await _events(db_session) == 1
    assert len(_receipts(outbox)) == 1


async def test_body_claiming_success_is_not_trusted(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 14: Maya does not sign webhooks; the re-fetch says pending."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    provider.settle(payment_id, "PENDING_PAYMENT")
    forged = {
        "id": payment_id,
        "paymentStatus": "PAYMENT_SUCCESS",
        "status": "PAYMENT_SUCCESS",
        "totalAmount": {"value": "515.00", "currency": "PHP"},
        "requestReferenceNumber": booking["checkout"]["reference_code"],
    }

    response = await client.post(WEBHOOK, content=json.dumps(forged))

    assert response.status_code == 200
    assert (await _read(client, world, booking["checkout_id"]))["status"] == "PENDING"
    assert await _events(db_session) == 0


async def test_failure_redirect_still_shows_successful(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """Scenario 15: the webhook was lost and the customer lands on our page;
    its refresh asks Maya, and Maya says paid."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    await client.post(f"{MOCK}/{payment_id}/success", params={"deliver_webhook": "false"})
    assert (await _read(client, world, booking["checkout_id"]))["status"] == "PENDING"

    refreshed = await action(client, world.player, booking["checkout_id"], "refresh-payment")

    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "SUCCESSFUL"


async def test_refresh_before_paying_changes_nothing(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    refreshed = await action(client, world.player, booking["checkout_id"], "refresh-payment")

    assert refreshed.json()["status"] == "PENDING"


async def test_another_customers_refresh_is_404(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player2, world.a.court.id, next_hour())

    response = await action(client, world.player, booking["checkout_id"], "refresh-payment")

    assert response.status_code == 404


async def test_refresh_payment_is_rate_limited(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    codes = [
        (await action(client, world.player, booking["checkout_id"], "refresh-payment")).status_code
        for _ in range(11)
    ]

    assert codes[:10] == [200] * 10
    assert codes[10] == 429


async def test_cancel_then_payment_lands_is_rebooked_if_free(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """The customer paid on Maya, then pressed Cancel before the webhook."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await open_maya(client, world.player, booking["checkout_id"])
    await action(client, world.player, booking["checkout_id"], "cancel")

    await pay(client, db_session, provider, world.player, booking["checkout_id"])

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "SUCCESSFUL"
    (g,) = await groups(db_session, booking["checkout_id"])
    assert (g.booking_status, g.payment_status) == ("CONFIRMED", "PAID")
    assert (g.cancelled_at, g.cancellation_reason) == (None, None)


async def test_cancel_then_payment_lands_is_refunded_if_taken(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    start = next_hour()
    booking = await hold(client, world.player, world.a.court.id, start)
    await open_maya(client, world.player, booking["checkout_id"])
    await action(client, world.player, booking["checkout_id"], "cancel")
    await hold(client, world.player2, world.a.court.id, start)

    await pay(client, db_session, provider, world.player, booking["checkout_id"])

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "REFUNDED"
    (g,) = await groups(db_session, booking["checkout_id"])
    assert g.booking_status == "CANCELLED"


async def test_late_payment_after_an_hour_started_is_refunded(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """An hour whose start has passed is never re-acquired, even if free."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await open_maya(client, world.player, booking["checkout_id"])
    await lapse(db_session, booking["checkout_id"])
    started = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    await db_session.execute(
        update(BookingSlotModel)
        .where(BookingSlotModel.booking_group_id == booking["id"])
        .values(starts_at=started)
    )

    await pay(client, db_session, provider, world.player, booking["checkout_id"])

    assert (await _read(client, world, booking["checkout_id"]))["status"] == "REFUNDED"


async def test_amount_mismatch_is_flagged_not_confirmed(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])
    provider._payments[payment_id]["totalAmount"]["value"] = "1.00"

    await pay(client, db_session, provider, world.player, booking["checkout_id"])

    (g,) = await groups(db_session, booking["checkout_id"])
    assert (g.booking_status, g.needs_resolution) == ("PENDING", True)
    assert (await checkout(db_session, booking["checkout_id"])).status == "PENDING"


async def test_paying_on_the_mock_page_lands_on_the_result_page(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    payment_id = await open_maya(client, world.player, booking["checkout_id"])

    paid = await client.post(f"{MOCK}/{payment_id}/success")

    assert paid.status_code == 303
    assert paid.headers["location"].endswith(f"/book/result/{booking['checkout_id']}")
    assert (await _read(client, world, booking["checkout_id"]))["status"] == "SUCCESSFUL"
