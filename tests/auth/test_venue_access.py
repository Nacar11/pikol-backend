"""The active venue (ADR 0003): permissions are those held AT the venue named
in X-Venue-ID, never the union across a person's venues."""

from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import FRONT_DESK_CODES, MASTER_DEFAULT_CODES, GroupKind
from src.users.domain.roles import PERMISSIONS
from src.users.persistence.group_repository import group_repository
from src.utils.api import API_PREFIX
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue, venue_group_id

ADMIN = f"{API_PREFIX}/admin"
ME = f"{API_PREFIX}/users/me"


@dataclass
class Ana:
    """Master at A, front desk at B, nothing at C."""

    a: VenueModel
    b: VenueModel
    c: VenueModel
    headers: dict[str, str]


@pytest.fixture
async def ana(client: AsyncClient, db_session: AsyncSession) -> Ana:
    a = await make_venue(db_session, "Alpha")
    b = await make_venue(db_session, "Bravo")
    c = await make_venue(db_session, "Charlie")
    user = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=a.id)
    front_desk_b = await venue_group_id(db_session, b.id, GroupKind.STAFF)
    await group_repository.add_member(db_session, front_desk_b, user.id, added_by=None)
    return Ana(a, b, c, bearer(await login(client, user.email)))


def at(headers: dict[str, str], venue: VenueModel) -> dict[str, str]:
    return {**headers, "X-Venue-ID": str(venue.id)}


async def create_court(client: AsyncClient, headers: dict[str, str], venue: VenueModel) -> int:
    response = await client.post(
        f"{ADMIN}/venues/{venue.id}/courts", headers=headers, json={"name": "New court"}
    )
    return response.status_code


async def test_master_codes_apply_at_their_own_venue(client: AsyncClient, ana: Ana) -> None:
    assert await create_court(client, at(ana.headers, ana.a), ana.a) == 201


async def test_front_desk_codes_apply_at_the_other_venue(client: AsyncClient, ana: Ana) -> None:
    # The summed model would allow this — she manages courts at A.
    assert await create_court(client, at(ana.headers, ana.b), ana.b) == 403


async def test_acting_at_one_venue_never_reaches_another(client: AsyncClient, ana: Ana) -> None:
    # Master codes at A do not carry a write against B's id; B is out of scope.
    assert await create_court(client, at(ana.headers, ana.a), ana.b) == 404


async def test_several_venues_and_no_header_is_400(client: AsyncClient, ana: Ana) -> None:
    response = await client.post(
        f"{ADMIN}/venues/{ana.a.id}/courts", headers=ana.headers, json={"name": "New court"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VENUE_REQUIRED"


async def test_header_naming_a_venue_without_membership_is_403(
    client: AsyncClient, ana: Ana
) -> None:
    assert await create_court(client, at(ana.headers, ana.c), ana.c) == 403


async def test_single_venue_staff_need_no_header(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    venue = await make_venue(db_session)
    manager = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=venue.id)
    headers = bearer(await login(client, manager.email))

    assert await create_court(client, headers, venue) == 201


async def test_customer_has_no_staff_access(client: AsyncClient, db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    customer = await make_user(db_session)
    headers = bearer(await login(client, customer.email))

    assert await create_court(client, headers, venue) == 403
    assert await create_court(client, at(headers, venue), venue) == 403


async def test_permissions_follow_the_header(client: AsyncClient, ana: Ana) -> None:
    at_a = await client.get(f"{ME}/permissions", headers=at(ana.headers, ana.a))
    at_b = await client.get(f"{ME}/permissions", headers=at(ana.headers, ana.b))
    union = await client.get(f"{ME}/permissions", headers=ana.headers)

    assert set(at_a.json()) == MASTER_DEFAULT_CODES
    assert set(at_b.json()) == FRONT_DESK_CODES
    assert set(union.json()) == MASTER_DEFAULT_CODES | FRONT_DESK_CODES


async def test_permissions_for_a_foreign_venue_is_403(client: AsyncClient, ana: Ana) -> None:
    response = await client.get(f"{ME}/permissions", headers=at(ana.headers, ana.c))
    assert response.status_code == 403


async def test_my_venues_lists_each_membership(client: AsyncClient, ana: Ana) -> None:
    body = (await client.get(f"{ME}/venues", headers=ana.headers)).json()

    assert body == [
        {"venue_id": ana.a.id, "name": "Alpha", "is_master": True, "groups": ["Venue master"]},
        {"venue_id": ana.b.id, "name": "Bravo", "is_master": False, "groups": ["Front desk"]},
    ]


async def test_owner_sees_every_venue_and_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    venue = await make_venue(db_session, "Zulu")
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    headers = bearer(await login(client, admin.email))

    venues = (await client.get(f"{ME}/venues", headers=headers)).json()
    permissions = (await client.get(f"{ME}/permissions", headers=headers)).json()

    assert venue.id in {v["venue_id"] for v in venues}
    assert set(permissions) == set(PERMISSIONS)


async def test_customer_venues_is_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    customer = await make_user(db_session)
    headers = bearer(await login(client, customer.email))

    assert (await client.get(f"{ME}/venues", headers=headers)).json() == []
    assert (await client.get(ME, headers=headers)).json()["is_customer"] is True
