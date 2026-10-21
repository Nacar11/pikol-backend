import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.roles import ROLE_PERMISSIONS, RoleCode
from src.users.persistence.models import RoleModel, UserModel


async def _player_role_id(session: AsyncSession) -> int:
    return (
        await session.execute(select(RoleModel.id).where(RoleModel.code == RoleCode.PLAYER))
    ).scalar_one()


async def _add_user(session: AsyncSession, email: str) -> UserModel:
    user = UserModel(
        email=email, first_name="Jane", last_name="Doe", role_id=await _player_role_id(session)
    )
    session.add(user)
    await session.flush()
    return user


async def test_seeded_matrix_matches_spec(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text(
            "SELECT r.code, p.code FROM roles r "
            "LEFT JOIN role_permissions rp ON rp.role_id = r.id "
            "LEFT JOIN permissions p ON p.id = rp.permission_id"
        )
    )
    seeded: dict[str, set[str]] = {}
    for role, permission in rows:
        seeded.setdefault(role, set())
        if permission is not None:
            seeded[role].add(permission)

    assert seeded == {role.value: set(codes) for role, codes in ROLE_PERMISSIONS.items()}
    assert "USER:Manage" in seeded["VENUE_MANAGER"]
    assert "PARAMETER:Manage" not in seeded["VENUE_MANAGER"]
    assert "VENUE:Manage" not in seeded["VENUE_MANAGER"]


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
