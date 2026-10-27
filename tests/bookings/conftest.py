from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.courts.persistence.models import CourtModel
from src.parameters.persistence.models import ParameterModel
from src.parameters.service import parameter_service
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.time import MANILA
from src.venues.persistence.models import VenueModel
from tests.auth.helpers import RoleCode, bearer, login, make_court, make_user, make_venue

CHECKOUTS = f"{API_PREFIX}/checkouts/me"
ADMIN = f"{API_PREFIX}/admin"


@dataclass
class Venue:
    venue: VenueModel
    court: CourtModel
    court2: CourtModel


@dataclass
class World:
    a: Venue
    b: Venue
    player: dict[str, str]
    player_user: UserModel
    player2: dict[str, str]
    staff_a: dict[str, str]
    staff_a_user: UserModel
    manager_a: dict[str, str]


async def _venue(session: AsyncSession, name: str) -> Venue:
    venue = await make_venue(session, name)
    return Venue(
        venue,
        await make_court(session, venue.id, f"{name} 1", sort_order=1),
        await make_court(session, venue.id, f"{name} 2", sort_order=2),
    )


@pytest.fixture
async def world(client: AsyncClient, db_session: AsyncSession) -> World:
    a, b = await _venue(db_session, "A"), await _venue(db_session, "B")
    player = await make_user(db_session)
    player2 = await make_user(db_session)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=a.venue.id)
    manager = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=a.venue.id)
    return World(
        a=a,
        b=b,
        player=bearer(await login(client, player.email)),
        player_user=player,
        player2=bearer(await login(client, player2.email)),
        staff_a=bearer(await login(client, staff.email)),
        staff_a_user=staff,
        manager_a=bearer(await login(client, manager.email)),
    )


async def set_parameter(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        update(ParameterModel).where(ParameterModel.key == key).values(value=value)
    )
    parameter_service.invalidate()


def manila_date(at: datetime) -> date:
    return at.astimezone(MANILA).date()


def items(court_id: int, starts_at: datetime, hours: int = 1) -> dict[str, Any]:
    """A one-item checkout body."""
    return {"items": [{"court_id": court_id, "starts_at": starts_at.isoformat(), "hours": hours}]}


async def hold(
    client: AsyncClient, headers: dict[str, str], court_id: int, starts_at: datetime, hours: int = 1
) -> dict[str, Any]:
    """Hold hours through a one-item checkout. Returns its booking, plus
    `checkout_id` and the whole `checkout`, so `booking["id"]` stays the
    booking group's id."""
    response = await client.post(CHECKOUTS, headers=headers, json=items(court_id, starts_at, hours))
    assert response.status_code == 201, response.text
    checkout: dict[str, Any] = response.json()
    return {**checkout["bookings"][0], "checkout_id": checkout["id"], "checkout": checkout}
