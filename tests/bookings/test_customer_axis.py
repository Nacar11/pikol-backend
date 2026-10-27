"""Customer access and staff access are independent (ADR 0003): one account
may book as itself and work the counter, and losing one leaves the other."""

from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import GroupKind
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupMemberModel
from tests.auth.helpers import bearer, login, make_user
from tests.bookings.conftest import ADMIN, BOOKINGS, World
from tests.bookings.helpers import next_hour


async def _hold(client: AsyncClient, headers: dict[str, str], court_id: int, day: int) -> int:
    response = await client.post(
        BOOKINGS,
        headers=headers,
        json={"court_id": court_id, "starts_at": next_hour(days=day).isoformat()},
    )
    return response.status_code


async def test_customer_who_is_also_staff_can_do_both(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    kim = await make_user(db_session)  # a customer
    front_desk = await group_repository.venue_group_id(
        db_session, world.a.venue.id, GroupKind.STAFF
    )
    await group_repository.add_member(db_session, front_desk, kim.id, added_by=None)
    headers = bearer(await login(client, kim.email))

    walk_in = await client.post(
        f"{ADMIN}/bookings/walk-in",
        headers=headers,
        json={"court_id": world.a.court.id, "starts_at": next_hour(days=2).isoformat()},
    )

    assert await _hold(client, headers, world.a.court2.id, day=3) == 201
    assert walk_in.status_code == 201


async def test_leaving_customer_stops_booking_but_not_staff_work(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    kim = await make_user(db_session)
    front_desk = await group_repository.venue_group_id(
        db_session, world.a.venue.id, GroupKind.STAFF
    )
    await group_repository.add_member(db_session, front_desk, kim.id, added_by=None)
    await db_session.execute(
        delete(UserGroupMemberModel).where(
            UserGroupMemberModel.user_id == kim.id,
            UserGroupMemberModel.group_id == await group_repository.customer_group_id(db_session),
        )
    )
    headers = bearer(await login(client, kim.email))

    schedule = await client.get(f"{ADMIN}/bookings", headers=headers)

    assert await _hold(client, headers, world.a.court.id, day=2) == 403
    assert schedule.status_code == 200


async def test_staff_who_are_not_customers_cannot_book(client: AsyncClient, world: World) -> None:
    assert await _hold(client, world.staff_a, world.a.court.id, day=2) == 403
