from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.persistence.models import UserModel
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue


@dataclass
class World:
    venue_a: VenueModel
    venue_b: VenueModel
    admin: dict[str, str]
    admin_user: UserModel
    manager_a: dict[str, str]
    manager_a_user: UserModel
    staff_a: dict[str, str]
    player: dict[str, str]


@pytest.fixture
async def world(client: AsyncClient, db_session: AsyncSession) -> World:
    venue_a = await make_venue(db_session, "Alpha")
    venue_b = await make_venue(db_session, "Bravo")
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    manager = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=venue_a.id)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue_a.id)
    player = await make_user(db_session)
    return World(
        venue_a=venue_a,
        venue_b=venue_b,
        admin=bearer(await login(client, admin.email)),
        admin_user=admin,
        manager_a=bearer(await login(client, manager.email)),
        manager_a_user=manager,
        staff_a=bearer(await login(client, staff.email)),
        player=bearer(await login(client, player.email)),
    )
