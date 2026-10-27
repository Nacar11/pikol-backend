"""The plan's closing walkthrough (ADR 0003), end to end through the API:
the owner makes Ana master of two venues; Ana, acting at one, builds a group
and adds a customer by email; that customer then works at that venue only,
with exactly the group's codes, and is still a customer."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import GroupKind
from src.users.persistence.group_repository import group_repository
from src.utils.api import API_PREFIX
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue

ADMIN = f"{API_PREFIX}/admin"
ME = f"{API_PREFIX}/users/me"
WEEKEND_CODES = ["BOOKING:CreateWalkIn", "BOOKING:ReadAny", "CLOSURE:Manage"]


async def test_owner_master_staff_walkthrough(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a = await make_venue(db_session, "Alpha")
    b = await make_venue(db_session, "Bravo")
    owner = bearer(
        await login(
            client,
            (await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)).email,
        )
    )
    ana = await make_user(db_session, email="ana@x.ph", role=RoleCode.SUPER_ADMIN)  # no group yet
    kim = await make_user(db_session, email="kim@x.ph")  # a customer

    # 1. The owner makes Ana master of both venues.
    for venue in (a, b):
        master = await group_repository.venue_group_id(db_session, venue.id, GroupKind.MASTER)
        added = await client.post(
            f"{ADMIN}/user-groups/{master}/members", headers=owner, json={"email": ana.email}
        )
        assert added.status_code == 201, added.text

    # 2. Ana, acting at Alpha, creates "Weekend crew" and ticks its codes.
    ana_at_a = {**bearer(await login(client, ana.email)), "X-Venue-ID": str(a.id)}
    crew = await client.post(
        f"{ADMIN}/user-groups", headers=ana_at_a, json={"name": "Weekend crew"}
    )
    assert crew.status_code == 201, crew.text
    assert crew.json()["venue_id"] == a.id
    crew_id = crew.json()["id"]
    codes = await client.put(
        f"{ADMIN}/user-groups/{crew_id}/permissions",
        headers=ana_at_a,
        json={"codes": WEEKEND_CODES},
    )
    assert codes.json()["permissions"] == WEEKEND_CODES

    # 3. Ana adds Kim — a customer — by exact email.
    joined = await client.post(
        f"{ADMIN}/user-groups/{crew_id}/members", headers=ana_at_a, json={"email": "KIM@x.ph"}
    )
    assert joined.status_code == 201, joined.text
    assert joined.json()["is_customer"] is True

    # 4. Kim works at Alpha only, with exactly the crew's codes, and is still
    #    a customer.
    kim_auth = bearer(await login(client, kim.email))
    venues = (await client.get(f"{ME}/venues", headers=kim_auth)).json()
    permissions = await client.get(
        f"{ME}/permissions", headers={**kim_auth, "X-Venue-ID": str(a.id)}
    )
    at_bravo = await client.get(f"{ME}/permissions", headers={**kim_auth, "X-Venue-ID": str(b.id)})
    me = (await client.get(ME, headers=kim_auth)).json()

    assert [(v["venue_id"], v["groups"]) for v in venues] == [(a.id, ["Weekend crew"])]
    assert permissions.json() == WEEKEND_CODES
    assert at_bravo.status_code == 403
    assert me["is_customer"] is True
