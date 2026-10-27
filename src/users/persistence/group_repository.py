from typing import TYPE_CHECKING

from sqlalchemy import case, delete, func, insert, literal, or_, select
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

if TYPE_CHECKING:
    from src.auth.scope import VenueScope


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
        self,
        session: AsyncSession,
        user_id: int,
        *,
        venue_id: int | None,
        excluding_group: int | None = None,
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
        if excluding_group is not None:
            query = query.where(UserGroupModel.id != excluding_group)
        return frozenset((await session.execute(query)).scalars())

    # The reads below touch venue-owned groups, so `scope` is keyword-only
    # with NO default — see VenueScope.

    async def get_scoped(
        self, session: AsyncSession, group_id: int, *, scope: "VenueScope"
    ) -> UserGroupModel | None:
        """A group the caller may see. The CUSTOMER group has no venue, so a
        venue-bound scope never matches it — only the owner sees it."""
        result = await session.execute(
            select(UserGroupModel).where(
                UserGroupModel.id == group_id, scope.where(UserGroupModel.venue_id)
            )
        )
        return result.scalar_one_or_none()

    async def list_scoped(
        self,
        session: AsyncSession,
        *,
        scope: "VenueScope",
        page: int,
        limit: int,
        venue_id: int | None = None,
        kind: GroupKind | None = None,
    ) -> tuple[list[UserGroupModel], int]:
        """Customer first, then each venue's master before its staff groups."""
        filters = [scope.where(UserGroupModel.venue_id)]
        if venue_id is not None:
            filters.append(UserGroupModel.venue_id == venue_id)
        if kind is not None:
            filters.append(UserGroupModel.kind == kind)
        total = (
            await session.execute(select(func.count()).select_from(UserGroupModel).where(*filters))
        ).scalar_one()
        rows = await session.execute(
            select(UserGroupModel)
            .where(*filters)
            .order_by(
                UserGroupModel.venue_id.nulls_first(),
                case((UserGroupModel.kind == GroupKind.MASTER, 0), else_=1),
                UserGroupModel.id,
            )
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.scalars()), total

    async def member_counts(self, session: AsyncSession, group_ids: list[int]) -> dict[int, int]:
        rows = await session.execute(
            select(UserGroupMemberModel.group_id, func.count())
            .where(UserGroupMemberModel.group_id.in_(group_ids))
            .group_by(UserGroupMemberModel.group_id)
        )
        counts = {group_id: 0 for group_id in group_ids}
        counts.update(dict(rows.tuples().all()))
        return counts

    async def codes_by_group(
        self, session: AsyncSession, group_ids: list[int]
    ) -> dict[int, frozenset[str]]:
        rows = await session.execute(
            select(user_group_permissions.c.group_id, PermissionModel.code)
            .join(PermissionModel, PermissionModel.id == user_group_permissions.c.permission_id)
            .where(user_group_permissions.c.group_id.in_(group_ids))
        )
        codes: dict[int, set[str]] = {group_id: set() for group_id in group_ids}
        for group_id, code in rows.tuples():
            codes[group_id].add(code)
        return {group_id: frozenset(c) for group_id, c in codes.items()}

    async def groups_for(
        self, session: AsyncSession, user_ids: list[int], *, scope: "VenueScope"
    ) -> dict[int, list[UserGroupModel]]:
        """Each user's groups as the caller may see them: those at the caller's
        venue, plus Customer — never a membership at another venue."""
        rows = await session.execute(
            select(UserGroupMemberModel.user_id, UserGroupModel)
            .join(UserGroupModel, UserGroupModel.id == UserGroupMemberModel.group_id)
            .where(
                UserGroupMemberModel.user_id.in_(user_ids),
                or_(
                    UserGroupModel.kind == GroupKind.CUSTOMER,
                    scope.where(UserGroupModel.venue_id),
                ),
            )
            .order_by(UserGroupModel.venue_id.nulls_first(), UserGroupModel.id)
        )
        groups: dict[int, list[UserGroupModel]] = {user_id: [] for user_id in user_ids}
        for user_id, group in rows.tuples():
            groups[user_id].append(group)
        return groups

    async def is_master_at(self, session: AsyncSession, user_id: int, venue_id: int) -> bool:
        result = await session.execute(
            select(UserGroupMemberModel.user_id)
            .join(UserGroupModel, UserGroupModel.id == UserGroupMemberModel.group_id)
            .where(
                UserGroupMemberModel.user_id == user_id,
                UserGroupModel.kind == GroupKind.MASTER,
                UserGroupModel.venue_id == venue_id,
            )
        )
        return result.first() is not None

    async def belongs_elsewhere(self, session: AsyncSession, user_id: int, venue_id: int) -> bool:
        """Any membership not at `venue_id` — another venue's group, or
        Customer. Such a person is not one venue's to deactivate."""
        result = await session.execute(
            select(UserGroupMemberModel.user_id)
            .join(UserGroupModel, UserGroupModel.id == UserGroupMemberModel.group_id)
            .where(
                UserGroupMemberModel.user_id == user_id,
                UserGroupModel.venue_id.is_distinct_from(venue_id),
            )
        )
        return result.first() is not None

    async def is_member(self, session: AsyncSession, group_id: int, user_id: int) -> bool:
        result = await session.execute(
            select(UserGroupMemberModel.user_id).where(
                UserGroupMemberModel.group_id == group_id, UserGroupMemberModel.user_id == user_id
            )
        )
        return result.first() is not None

    async def set_codes(self, session: AsyncSession, group_id: int, codes: frozenset[str]) -> None:
        """Replace the group's permissions with exactly `codes`."""
        await session.execute(
            delete(user_group_permissions).where(user_group_permissions.c.group_id == group_id)
        )
        if codes:
            await session.execute(
                insert(user_group_permissions).from_select(
                    ["group_id", "permission_id"],
                    select(literal(group_id), PermissionModel.id).where(
                        PermissionModel.code.in_(codes)
                    ),
                )
            )

    async def all_permissions(self, session: AsyncSession) -> list[PermissionModel]:
        rows = await session.execute(
            select(PermissionModel).order_by(PermissionModel.resource, PermissionModel.id)
        )
        return list(rows.scalars())

    async def add_member(
        self, session: AsyncSession, group_id: int, user_id: int, *, added_by: int | None
    ) -> None:
        session.add(
            UserGroupMemberModel(group_id=group_id, user_id=user_id, added_by_user_id=added_by)
        )
        await session.flush()


group_repository = GroupRepository()
