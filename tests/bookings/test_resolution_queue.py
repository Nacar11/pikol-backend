from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.bookings.conftest import ADMIN, World
from tests.bookings.helpers import insert_booking, next_hour

QUEUE = f"{ADMIN}/bookings/unfulfilled"


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
