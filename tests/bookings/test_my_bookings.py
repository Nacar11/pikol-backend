from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus
from src.bookings.persistence.models import BookingGroupModel
from tests.bookings.conftest import BOOKINGS, World, hold, set_parameter
from tests.bookings.helpers import next_hour

MINE = f"{BOOKINGS}/me"


async def test_my_bookings_lists_only_mine_newest_first(client: AsyncClient, world: World) -> None:
    first = await hold(client, world.player, world.a.court.id, next_hour(1))
    second = await hold(client, world.player, world.a.court.id, next_hour(2))
    await hold(client, world.player2, world.a.court2.id, next_hour(1))

    response = await client.get(MINE, headers=world.player)

    assert response.status_code == 200
    assert [b["id"] for b in response.json()["data"]] == [second["id"], first["id"]]
    assert response.json()["total"] == 2


async def test_other_players_booking_is_404(client: AsyncClient, world: World) -> None:
    theirs = await hold(client, world.player2, world.a.court.id, next_hour())

    got = await client.get(f"{MINE}/{theirs['id']}", headers=world.player)
    cancel = await client.post(f"{MINE}/{theirs['id']}/cancel", headers=world.player, json={})

    assert got.status_code == cancel.status_code == 404


async def test_lapsed_hold_displays_expired(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """Nothing has written EXPIRED yet — cleanup is lazy — but the player
    must never be shown a dead hold as PENDING."""
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await db_session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == booking["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    body = (await client.get(f"{MINE}/{booking['id']}", headers=world.player)).json()

    assert body["booking_status"] == "PENDING"
    assert body["display_status"] == "EXPIRED"


async def test_cancel_hold_releases_slot(client: AsyncClient, world: World) -> None:
    start = next_hour()
    booking = await hold(client, world.player, world.a.court.id, start, hours=2)

    response = await client.post(f"{MINE}/{booking['id']}/cancel", headers=world.player, json={})

    assert response.status_code == 200
    assert response.json()["booking_status"] == "CANCELLED"
    await hold(client, world.player2, world.a.court.id, start + timedelta(hours=1))


async def test_cancel_records_audit_fields(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())

    body = (
        await client.post(
            f"{MINE}/{booking['id']}/cancel", headers=world.player, json={"reason": "Rain"}
        )
    ).json()
    unexplained = await hold(client, world.player, world.a.court.id, next_hour(2))
    default = (
        await client.post(f"{MINE}/{unexplained['id']}/cancel", headers=world.player, json={})
    ).json()

    assert body["cancellation_reason"] == "Rain"
    assert body["cancelled_at"] is not None
    assert default["cancellation_reason"] == "Cancelled by player"


async def test_cancel_twice_is_409(client: AsyncClient, world: World) -> None:
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await client.post(f"{MINE}/{booking['id']}/cancel", headers=world.player, json={})

    again = await client.post(f"{MINE}/{booking['id']}/cancel", headers=world.player, json={})

    assert again.status_code == 409


async def test_cancel_confirmed_after_cutoff_is_409(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """A paid booking (confirmed directly here — checkout is Phase 5) inside
    the cutoff window cannot be cancelled by the player."""
    await set_parameter(db_session, "cancellation_cutoff_minutes", str(60 * 24 * 3))
    booking = await hold(client, world.player, world.a.court.id, next_hour())
    await db_session.execute(
        update(BookingGroupModel)
        .where(BookingGroupModel.id == booking["id"])
        .values(booking_status=BookingStatus.CONFIRMED, expires_at=None)
    )

    response = await client.post(f"{MINE}/{booking['id']}/cancel", headers=world.player, json={})

    assert response.status_code == 409
    assert response.json()["code"] == "CANCELLATION_CUTOFF_PASSED"


async def test_unknown_id_is_404(client: AsyncClient, world: World) -> None:
    assert (await client.get(f"{MINE}/999999", headers=world.player)).status_code == 404
