import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.courts.persistence.models import CourtModel
from src.devtools import e2e_seed
from src.devtools.e2e_seed import (
    CUSTOMER_EMAILS,
    DESK_EMAIL,
    OWNER_EMAIL,
    assert_e2e_database,
    seed,
)
from src.users.persistence.group_repository import group_repository
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
    assert CUSTOMER_EMAILS == ("customer1.e2e@example.com", "customer2.e2e@example.com")
    for email in CUSTOMER_EMAILS:
        customer = (await db_session.scalars(select(UserModel).filter_by(email=email))).one()
        assert await group_repository.is_customer(db_session, customer.id)


async def test_main_refuses_before_seeding_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    async def fake_seed(session: object) -> None:
        calls.append(session)

    monkeypatch.setattr(e2e_seed, "seed", fake_seed)
    with pytest.raises(SystemExit) as exit_:
        await e2e_seed._main("postgresql+asyncpg://p:p@localhost:5433/pikol")

    assert exit_.value.code == 2
    assert calls == []
