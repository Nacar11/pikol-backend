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

    async def add_member(
        self, session: AsyncSession, group_id: int, user_id: int, *, added_by: int | None
    ) -> None:
        session.add(
            UserGroupMemberModel(group_id=group_id, user_id=user_id, added_by_user_id=added_by)
        )
        await session.flush()


group_repository = GroupRepository()
