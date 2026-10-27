import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.roles import PERMISSIONS
from src.users.persistence.models import UserModel


async def _add_user(session: AsyncSession, email: str) -> UserModel:
    user = UserModel(email=email, first_name="Jane", last_name="Doe")
    session.add(user)
    await session.flush()
    return user


async def test_permissions_table_matches_the_code(db_session: AsyncSession) -> None:
    rows = await db_session.execute(text("SELECT code FROM permissions"))

    assert set(rows.scalars()) == set(PERMISSIONS)


async def test_roles_are_gone(db_session: AsyncSession) -> None:
    """ADR 0003: groups carry every permission; nothing stores a role."""
    tables = await db_session.execute(
        text("SELECT to_regclass('roles'), to_regclass('role_permissions')")
    )
    columns = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name IN ('role_id', 'venue_id')"
        )
    )

    assert tables.one() == (None, None)
    assert columns.all() == []


async def test_email_unique_case_insensitively(db_session: AsyncSession) -> None:
    await _add_user(db_session, "jane@x.com")

    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await _add_user(db_session, "JANE@x.com")

    assert "users_email_lower_uq" in str(exc.value)


async def test_email_reusable_after_soft_delete(db_session: AsyncSession) -> None:
    first = await _add_user(db_session, "jane@x.com")
    await db_session.execute(
        update(UserModel).where(UserModel.id == first.id).values(deleted_at=text("now()"))
    )

    second = await _add_user(db_session, "jane@x.com")

    assert second.id != first.id


async def test_password_hash_not_loaded_by_default(db_session: AsyncSession) -> None:
    created = await _add_user(db_session, "jane@x.com")
    db_session.expunge_all()

    user = (
        await db_session.execute(select(UserModel).where(UserModel.id == created.id))
    ).scalar_one()

    assert "password_hash" not in inspect(user).dict
