"""User groups (ADR 0003). Every rule that stops a non-owner from escalating
lives here, in one place, each pinned by a test that fails when it goes."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.users.domain.groups import GroupKind
from src.users.dto import GroupCreate, GroupOut, GroupUpdate
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupModel, UserModel
from src.utils.errors import AppError

NAME_CONSTRAINT = "uq_user_groups_venue_name"


def group_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Group not found.")


def owner_only(detail: str) -> AppError:
    return AppError(403, "FORBIDDEN", detail)


def name_taken() -> AppError:
    return AppError(409, "GROUP_NAME_TAKEN", "This venue already has a group with that name.")


class GroupService:
    async def get(
        self, session: AsyncSession, group_id: int, *, scope: VenueScope
    ) -> UserGroupModel:
        """Ownership after fetch: another venue's group, and Customer for a
        non-owner, are 404 — byte-for-byte a group that does not exist."""
        group = await group_repository.get_scoped(session, group_id, scope=scope)
        if group is None:
            raise group_not_found()
        return group

    async def editable(
        self, session: AsyncSession, caller: UserModel, group_id: int, *, scope: VenueScope
    ) -> UserGroupModel:
        """A group the caller may change. Master groups are the owner's."""
        group = await self.get(session, group_id, scope=scope)
        if group.kind == GroupKind.MASTER and not caller.system_admin:
            raise owner_only("Only the owner can change a venue master group.")
        return group

    async def out_many(
        self, session: AsyncSession, caller: UserModel, groups: list[UserGroupModel]
    ) -> list[GroupOut]:
        ids = [g.id for g in groups]
        counts = await group_repository.member_counts(session, ids)
        codes = await group_repository.codes_by_group(session, ids)
        return [
            GroupOut(
                id=g.id,
                name=g.name,
                kind=g.kind,
                venue_id=g.venue_id,
                member_count=counts[g.id],
                permissions=sorted(codes[g.id]),
                read_only=g.kind == GroupKind.MASTER and not caller.system_admin,
            )
            for g in groups
        ]

    async def out(
        self, session: AsyncSession, caller: UserModel, group: UserGroupModel
    ) -> GroupOut:
        return (await self.out_many(session, caller, [group]))[0]

    async def create(
        self, session: AsyncSession, caller: UserModel, body: GroupCreate, *, scope: VenueScope
    ) -> UserGroupModel:
        if caller.system_admin:
            if body.venue_id is None:
                raise AppError(422, "VENUE_REQUIRED", "Choose the venue this group belongs to.")
            venue_id = body.venue_id
        else:
            if body.venue_id is not None:
                # The server decides a group's venue, never the body.
                raise AppError(
                    422, "FIELD_NOT_ALLOWED", "A group is created at the venue you are acting at."
                )
            assert scope.venue_id is not None  # a non-owner acts at exactly one venue
            venue_id = scope.venue_id
        group = UserGroupModel(
            kind=GroupKind.STAFF,
            venue_id=venue_id,
            name=body.name.strip(),
            created_by_user_id=caller.id,
            updated_by_user_id=caller.id,
        )
        await self._save(session, group)
        return group

    async def rename(
        self,
        session: AsyncSession,
        caller: UserModel,
        group_id: int,
        body: GroupUpdate,
        *,
        scope: VenueScope,
    ) -> UserGroupModel:
        group = await self.editable(session, caller, group_id, scope=scope)
        if group.kind == GroupKind.CUSTOMER:
            raise AppError(422, "BUILT_IN_GROUP", "The Customer group cannot be renamed.")
        group.name = body.name.strip()
        group.updated_by_user_id = caller.id
        await self._save(session, group)
        return group

    async def delete(
        self, session: AsyncSession, caller: UserModel, group_id: int, *, scope: VenueScope
    ) -> None:
        group = await self.editable(session, caller, group_id, scope=scope)
        if group.kind != GroupKind.STAFF:
            raise AppError(409, "BUILT_IN_GROUP", "Built-in groups cannot be deleted.")
        if (await group_repository.member_counts(session, [group.id]))[group.id]:
            # Refused rather than cascaded: nobody loses access by accident.
            raise AppError(
                409, "GROUP_NOT_EMPTY", "Move this group's members out before deleting it."
            )
        await session.delete(group)
        await session.commit()

    async def _save(self, session: AsyncSession, group: UserGroupModel) -> None:
        """The unique index is the name check — no SELECT-then-INSERT race."""
        try:
            async with session.begin_nested():
                session.add(group)
                await session.flush()
        except IntegrityError as exc:
            if NAME_CONSTRAINT in str(exc.orig):
                raise name_taken() from None
            raise
        await session.commit()
        await session.refresh(group)


group_service = GroupService()
