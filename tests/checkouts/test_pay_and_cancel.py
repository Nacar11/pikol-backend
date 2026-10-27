import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus, PaymentStatus
from src.bookings.persistence.models import BookingGroupModel
from src.bookings.persistence.repository import booking_repository
from src.checkouts.persistence.models import CheckoutModel
from src.checkouts.persistence.repository import checkout_repository
from src.payments.adapters.mock import MockProvider
from src.payments.domain.enums import PaymentOutcome, PaymentProviderName
from src.payments.domain.port import ProviderError
from src.payments.persistence.models import PaymentModel
from src.payments.persistence.repository import payment_repository
from src.utils.errors import constraint_of
from tests.bookings.conftest import CHECKOUTS, World, hold
from tests.bookings.helpers import next_hour
from tests.checkouts.helpers import action, cell, item, lapse


async def _payments(session: AsyncSession, checkout_id: int) -> int:
    return (
        await session.execute(select(func.count()).where(PaymentModel.checkout_id == checkout_id))
    ).scalar_one()


async def test_pay_returns_a_maya_url_for_the_checkout_total(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    response = await action(client, world.player, booking["checkout_id"], "pay")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checkout_url"].startswith("http://test/api/v1/mock-maya/checkouts/")
    payment_id = body["checkout_url"].rsplit("/", 1)[1]
    sent = provider.describe(payment_id)
    assert sent["requestReferenceNumber"] == body["reference_code"]
    assert sent["totalAmount"]["value"] == "515.00"
    assert provider.return_url(payment_id) == f"http://localhost:3000/book/result/{body['id']}"
    assert await _payments(db_session, booking["checkout_id"]) == 1


async def test_pay_twice_reuses_the_same_maya_session(
    client: AsyncClient, world: World, db_session: AsyncSession, provider: MockProvider
) -> None:
    """A second Maya session could also be paid — never open two."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    first = await action(client, world.player, booking["checkout_id"], "pay")
    second = await action(client, world.player, booking["checkout_id"], "pay")
    read = await client.get(f"{CHECKOUTS}/{booking['checkout_id']}", headers=world.player)

    assert first.json()["checkout_url"] == second.json()["checkout_url"]
    assert read.json()["checkout_url"] == first.json()["checkout_url"]
    assert await _payments(db_session, booking["checkout_id"]) == 1


async def test_pay_after_the_hold_ended_is_refused(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await lapse(db_session, booking["checkout_id"])

    response = await action(client, world.player, booking["checkout_id"], "pay")

    assert (response.status_code, response.json()["code"]) == (409, "HOLD_ENDED")
    assert await _payments(db_session, booking["checkout_id"]) == 0


async def test_provider_outage_is_502_and_keeps_the_hold(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    provider: MockProvider,
    monkeypatch: object,
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    async def down(**_: object) -> None:
        raise ProviderError("down")

    monkeypatch.setattr(provider, "create_checkout", down)  # type: ignore[attr-defined]
    response = await action(client, world.player, booking["checkout_id"], "pay")

    assert (response.status_code, response.json()["code"]) == (502, "PAYMENT_PROVIDER_UNAVAILABLE")
    again = await client.get(f"{CHECKOUTS}/{booking['checkout_id']}", headers=world.player)
    assert again.json()["status"] == "PENDING"


async def test_someone_elses_checkout_cannot_be_paid_or_cancelled(
    client: AsyncClient, world: World
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    for name in ("pay", "cancel"):
        assert (
            await action(client, world.player2, booking["checkout_id"], name)
        ).status_code == 404


async def test_cancel_releases_every_hour_at_once(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    start = next_hour()
    created = await client.post(
        CHECKOUTS,
        headers=world.player,
        json={"items": [item(world.a.court.id, start, 2), item(world.a.court2.id, start)]},
    )
    checkout_id = created.json()["id"]
    await action(client, world.player, checkout_id, "pay")

    response = await action(client, world.player, checkout_id, "cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["checkout_url"] is None
    groups = (
        (
            await db_session.execute(
                select(BookingGroupModel)
                .where(BookingGroupModel.checkout_id == checkout_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert {g.booking_status for g in groups} == {BookingStatus.CANCELLED}
    assert {g.cancellation_reason for g in groups} == {"Cancelled by customer"}
    assert {g.cancelled_by_user_id for g in groups} == {world.player_user.id}
    payment = (
        await db_session.execute(
            select(PaymentModel).where(PaymentModel.checkout_id == checkout_id)
        )
    ).scalar_one()
    await db_session.refresh(payment)
    assert payment.status == PaymentOutcome.CANCELLED
    for court_id in (world.a.court.id, world.a.court2.id):
        assert await cell(client, world, court_id, start) == "AVAILABLE"


async def test_cancel_a_paid_checkout_is_refused(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Scenario 12: paid bookings are final."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    checkout = await db_session.get(CheckoutModel, booking["checkout_id"])
    group = await db_session.get(BookingGroupModel, booking["id"])
    assert checkout is not None and group is not None
    await checkout_repository.set_status(db_session, checkout, PaymentOutcome.SUCCESSFUL)
    await booking_repository.set_status(
        db_session, group, BookingStatus.CONFIRMED, payment_status=PaymentStatus.PAID
    )

    response = await action(client, world.player, booking["checkout_id"], "cancel")

    assert (response.status_code, response.json()["code"]) == (409, "PAID_IS_FINAL")
    await db_session.refresh(group)
    assert group.booking_status == BookingStatus.CONFIRMED


async def test_cancel_after_the_hold_ended_is_refused(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await lapse(db_session, booking["checkout_id"])

    response = await action(client, world.player, booking["checkout_id"], "cancel")

    assert (response.status_code, response.json()["code"]) == (409, "HOLD_ENDED")


async def test_a_checkout_has_one_open_maya_attempt(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await action(client, world.player, booking["checkout_id"], "pay")

    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            db_session.add(
                PaymentModel(
                    checkout_id=booking["checkout_id"],
                    provider=PaymentProviderName.MOCK,
                    provider_checkout_id="second-attempt",
                    checkout_url="http://test/second",
                    amount_centavos=booking["checkout"]["total_centavos"],
                    status=PaymentOutcome.PENDING,
                )
            )
            await db_session.flush()
    assert constraint_of(exc.value) == "uq_payments_one_open_attempt"


async def test_concurrent_pay_returns_the_first_maya_session(
    client: AsyncClient,
    world: World,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two /pay requests both saw no attempt and both opened Maya: the second
    insert loses to the index and returns the winner's URL, never its own."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    first = (await action(client, world.player, booking["checkout_id"], "pay")).json()
    real = payment_repository.latest_for_checkout
    calls = {"n": 0}

    async def stale_once(session: AsyncSession, checkout_id: int) -> PaymentModel | None:
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(session, checkout_id)

    monkeypatch.setattr(payment_repository, "latest_for_checkout", stale_once)
    second = await action(client, world.player, booking["checkout_id"], "pay")

    assert second.status_code == 200, second.text
    assert second.json()["checkout_url"] == first["checkout_url"]
    assert await _payments(db_session, booking["checkout_id"]) == 1
