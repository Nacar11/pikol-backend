import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.courts.persistence.models import CourtModel
from src.devtools.e2e_seed import DESK_EMAIL, OWNER_EMAIL, assert_e2e_database, seed
from src.users.persistence.models import UserGroupMemberModel, UserGroupModel, UserModel
from src.venues.persistence.models import VenueModel


def test_refuses_a_database_not_named_e2e() -> None:
    with pytest.raises(SystemExit) as exit_:
        assert_e2e_database("postgresql+asyncpg://p:p@localhost:5433/pikol")
    assert exit_.value.code == 2


def test_accepts_an_e2e_database() -> None:
    assert_e2e_database("postgresql+asyncpg://p:p@localhost:5433/pikol_e2e")


async def test_seeds_the_owner_two_venues_and_a_front_desk(db_session: AsyncSession) -> None:
    await seed(db_session)

    owner = (await db_session.scalars(select(UserModel).filter_by(email=OWNER_EMAIL))).one()
    assert owner.system_admin and owner.email_verified_at is not None
    venues = {
        v.name: v
        for v in await db_session.scalars(select(VenueModel).where(VenueModel.name.like("E2E %")))
    }
    assert set(venues) == {"E2E Riverside", "E2E Lakeside"}
    riverside = await db_session.scalars(
        select(CourtModel.name)
        .where(CourtModel.venue_id == venues["E2E Riverside"].id)
        .order_by(CourtModel.sort_order)
    )
    assert list(riverside) == ["Court 1", "Court 2", "Court 3"]
    desk = (await db_session.scalars(select(UserModel).filter_by(email=DESK_EMAIL))).one()
    assert not desk.system_admin
    groups = await db_session.scalars(
        select(UserGroupModel.name)
        .join(UserGroupMemberModel, UserGroupMemberModel.group_id == UserGroupModel.id)
        .where(UserGroupMemberModel.user_id == desk.id)
    )
    assert list(groups) == ["Front desk"]
