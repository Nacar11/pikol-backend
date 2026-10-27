import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.devtools.demo_seed import assert_demo_database, seed_customers
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel


def test_refuses_e2e_database() -> None:
    with pytest.raises(SystemExit) as exit_:
        assert_demo_database("postgresql+asyncpg://p:p@localhost:5433/pikol_e2e")
    assert exit_.value.code == 2


def test_refuses_supabase_host() -> None:
    with pytest.raises(SystemExit) as exit_:
        assert_demo_database(
            "postgresql+asyncpg://p:p@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
        )
    assert exit_.value.code == 2


def test_accepts_the_local_database() -> None:
    assert_demo_database("postgresql+asyncpg://p:p@localhost:5433/pikol")


async def test_seeds_customers_once(db_session: AsyncSession) -> None:
    emails = ["demo.seed.a@example.com", "demo.seed.b@example.com"]

    first = await seed_customers(db_session, emails, password="Demo-password-1")
    second = await seed_customers(db_session, emails, password="Demo-password-1")

    assert first == second == emails
    count = await db_session.scalar(
        select(func.count()).where(UserModel.email.in_(emails), UserModel.deleted_at.is_(None))
    )
    assert count == 2
    for email in emails:
        user = (await db_session.scalars(select(UserModel).filter_by(email=email))).one()
        assert user.is_active and user.email_verified_at is not None
        assert await group_repository.is_customer(db_session, user.id)


async def test_an_existing_account_is_made_a_customer(db_session: AsyncSession) -> None:
    from tests.auth.helpers import RoleCode, make_user

    staff = await make_user(db_session, role=RoleCode.SUPER_ADMIN, email="demo.staff@example.com")
    assert not await group_repository.is_customer(db_session, staff.id)

    await seed_customers(db_session, [staff.email], password="Demo-password-1")

    assert await group_repository.is_customer(db_session, staff.id)
