from sqlalchemy import insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.groups import (
    FRONT_DESK_CODES,
    FRONT_DESK_GROUP_NAME,
    MASTER_DEFAULT_CODES,
    MASTER_GROUP_NAME,
    GroupKind,
)
from src.users.persistence.models import (
    PermissionModel,
    UserGroupMemberModel,
    UserGroupModel,
    user_group_permissions,
)


class GroupRepository:
    async def customer_group_id(self, session: AsyncSession) -> int:
        result = await session.execute(
            select(UserGroupModel.id).where(UserGroupModel.kind == GroupKind.CUSTOMER)
        )
        return result.scalar_one()

    async def venue_group_id(self, session: AsyncSession, venue_id: int, kind: GroupKind) -> int:
        """The venue's master group, or its first staff group (the seeded Front
        desk) — enough for the role-shaped writers that exist until 0007."""
        result = await session.execute(
            select(UserGroupModel.id)
            .where(UserGroupModel.venue_id == venue_id, UserGroupModel.kind == kind)
            .order_by(UserGroupModel.id)
            .limit(1)
        )
        return result.scalar_one()

    async def seed_venue_groups(
        self, session: AsyncSession, venue_id: int, *, created_by: int | None
    ) -> None:
        """Every venue starts with a Venue master and a Front desk (dbml
        Appendix B). Call inside the transaction that creates the venue."""
        for kind, name, codes in (
            (GroupKind.MASTER, MASTER_GROUP_NAME, MASTER_DEFAULT_CODES),
            (GroupKind.STAFF, FRONT_DESK_GROUP_NAME, FRONT_DESK_CODES),
        ):
            group = UserGroupModel(
                kind=kind,
                venue_id=venue_id,
                name=name,
                created_by_user_id=created_by,
                updated_by_user_id=created_by,
            )
            session.add(group)
            await session.flush()
            await session.execute(
                insert(user_group_permissions).from_select(
                    ["group_id", "permission_id"],
                    select(literal(group.id), PermissionModel.id).where(
                        PermissionModel.code.in_(codes)
                    ),
                )
            )

    async def is_customer(self, session: AsyncSession, user_id: int) -> bool:
        result = await session.execute(
            select(UserGroupMemberModel.user_id)
            .join(UserGroupModel, UserGroupModel.id == UserGroupMemberModel.group_id)
            .where(
                UserGroupMemberModel.user_id == user_id,
                UserGroupModel.kind == GroupKind.CUSTOMER,
            )
        )
        return result.first() is not None

    async def staff_memberships(
        self, session: AsyncSession, user_id: int
    ) -> list[tuple[int, GroupKind, str]]:
        """(venue_id, kind, group name) for each MASTER/STAFF group the user is
        in, ordered by venue then group — never the CUSTOMER group."""
        rows = await session.execute(
            select(UserGroupModel.venue_id, UserGroupModel.kind, UserGroupModel.name)
            .join(UserGroupMemberModel, UserGroupMemberModel.group_id == UserGroupModel.id)
            .where(
                UserGroupMemberModel.user_id == user_id,
                UserGroupModel.kind != GroupKind.CUSTOMER,
            )
            .order_by(UserGroupModel.venue_id, UserGroupModel.id)
        )
        return [(venue_id, kind, name) for venue_id, kind, name in rows if venue_id is not None]

    async def staff_codes(
        self, session: AsyncSession, user_id: int, *, venue_id: int | None
    ) -> frozenset[str]:
        """The union of the user's staff groups' codes — at ONE venue when
        `venue_id` is given (what the server authorises on), or across all of
        them when None (coarse client checks only, never authorisation)."""
        query = (
            select(PermissionModel.code)
            .join(
                user_group_permissions,
                user_group_permissions.c.permission_id == PermissionModel.id,
            )
            .join(UserGroupModel, UserGroupModel.id == user_group_permissions.c.group_id)
            .join(UserGroupMemberModel, UserGroupMemberModel.group_id == UserGroupModel.id)
            .where(
                UserGroupMemberModel.user_id == user_id,
                UserGroupModel.kind != GroupKind.CUSTOMER,
            )
        )
        if venue_id is not None:
            query = query.where(UserGroupModel.venue_id == venue_id)
        return frozenset((await session.execute(query)).scalars())

    async def add_member(
        self, session: AsyncSession, group_id: int, user_id: int, *, added_by: int | None
    ) -> None:
        session.add(
            UserGroupMemberModel(group_id=group_id, user_id=user_id, added_by_user_id=added_by)
        )
        await session.flush()


group_repository = GroupRepository()
