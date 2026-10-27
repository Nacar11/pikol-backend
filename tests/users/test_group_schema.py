import importlib

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import FRONT_DESK_CODES, MASTER_DEFAULT_CODES, GroupKind
from src.users.domain.roles import RoleCode
from src.users.persistence.models import (
    PermissionModel,
    UserGroupMemberModel,
    UserGroupModel,
    user_group_permissions,
)
from tests.auth.helpers import make_user, make_venue

migration = importlib.import_module("src.database.migrations.versions.0006_user_groups")


async def _add_group(
    session: AsyncSession, kind: GroupKind, venue_id: int | None, name: str
) -> None:
    async with session.begin_nested():
        session.add(UserGroupModel(kind=kind, venue_id=venue_id, name=name))
        await session.flush()


async def _codes(session: AsyncSession, group_id: int) -> set[str]:
    rows = await session.execute(
        select(PermissionModel.code)
        .join(user_group_permissions, user_group_permissions.c.permission_id == PermissionModel.id)
        .where(user_group_permissions.c.group_id == group_id)
    )
    return set(rows.scalars())


async def _groups_of(session: AsyncSession, user_id: int) -> list[tuple[GroupKind, int | None]]:
    rows = await session.execute(
        select(UserGroupModel.kind, UserGroupModel.venue_id)
        .join(UserGroupMemberModel, UserGroupMemberModel.group_id == UserGroupModel.id)
        .where(UserGroupMemberModel.user_id == user_id)
    )
    return [(kind, venue_id) for kind, venue_id in rows]


async def test_exactly_one_customer_group(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="uq_user_groups_one_customer"):
        await _add_group(db_session, GroupKind.CUSTOMER, None, "Second customers")


async def test_one_master_group_per_venue(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    with pytest.raises(IntegrityError, match="uq_user_groups_one_master_per_venue"):
        await _add_group(db_session, GroupKind.MASTER, venue.id, "Second masters")


async def test_customer_group_has_no_venue(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    await db_session.execute(delete(UserGroupModel).where(UserGroupModel.kind == "CUSTOMER"))
    with pytest.raises(IntegrityError, match="ck_group_venue_matches_kind"):
        await _add_group(db_session, GroupKind.CUSTOMER, venue.id, "Customers here")


async def test_venue_group_needs_a_venue(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_group_venue_matches_kind"):
        await _add_group(db_session, GroupKind.STAFF, None, "Floating staff")


async def test_group_names_unique_per_venue_ignoring_case(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    with pytest.raises(IntegrityError, match="uq_user_groups_venue_name"):
        await _add_group(db_session, GroupKind.STAFF, venue.id, "FRONT DESK")


async def test_same_name_allowed_at_another_venue(db_session: AsyncSession) -> None:
    alpha = await make_venue(db_session, "Alpha")
    bravo = await make_venue(db_session, "Bravo")
    await _add_group(db_session, GroupKind.STAFF, alpha.id, "Weekend crew")
    await _add_group(db_session, GroupKind.STAFF, bravo.id, "Weekend crew")


async def test_new_venue_gets_seeded_groups(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    rows = (
        await db_session.execute(select(UserGroupModel).where(UserGroupModel.venue_id == venue.id))
    ).scalars()
    by_kind = {g.kind: g for g in rows}

    assert set(by_kind) == {GroupKind.MASTER, GroupKind.STAFF}
    assert await _codes(db_session, by_kind[GroupKind.MASTER].id) == MASTER_DEFAULT_CODES
    assert await _codes(db_session, by_kind[GroupKind.STAFF].id) == FRONT_DESK_CODES
    assert "VENUE:Manage" not in MASTER_DEFAULT_CODES
    assert "PARAMETER:Manage" not in MASTER_DEFAULT_CODES


async def test_backfill_maps_each_role_to_its_group(db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    player = await make_user(db_session)
    manager = await make_user(db_session, role=RoleCode.VENUE_MANAGER, venue_id=venue.id)
    staff = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=venue.id)
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    # Start from the pre-0006 state: roles only, no memberships.
    await db_session.execute(delete(UserGroupMemberModel))

    def run(connection: Connection) -> None:
        with Operations.context(MigrationContext.configure(connection)):
            migration.backfill_members()

    await (await db_session.connection()).run_sync(run)

    assert await _groups_of(db_session, player.id) == [(GroupKind.CUSTOMER, None)]
    assert await _groups_of(db_session, manager.id) == [(GroupKind.MASTER, venue.id)]
    assert await _groups_of(db_session, staff.id) == [(GroupKind.STAFF, venue.id)]
    assert await _groups_of(db_session, admin.id) == []
