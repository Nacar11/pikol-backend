"""User groups (ADR 0003). Every rule that stops a non-owner from escalating
lives here, in one place, each pinned by a test that fails when it goes."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import VenueAccess
from src.auth.scope import VenueScope
from src.users.domain.groups import OWNER_ONLY, GroupKind
from src.users.domain.roles import PERMISSIONS
from src.users.dto import (
    GroupCreate,
    GroupOut,
    GroupPermissionsUpdate,
    GroupUpdate,
    MemberAdd,
    MemberOut,
)
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupModel, UserModel
from src.users.persistence.repository import user_repository
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

    async def set_permissions(
        self,
        session: AsyncSession,
        caller: UserModel,
        group_id: int,
        body: GroupPermissionsUpdate,
        *,
        access: VenueAccess,
    ) -> UserGroupModel:
        group = await self.editable(session, caller, group_id, scope=access.scope)
        requested = frozenset(body.codes)
        if unknown := requested - frozenset(PERMISSIONS):
            raise AppError(
                422, "UNKNOWN_PERMISSION", f"Unknown permission: {', '.join(sorted(unknown))}."
            )
        if requested & OWNER_ONLY:
            # Never grantable, by anyone — the owner holds these through
            # system_admin, not through any group.
            raise AppError(
                422, "OWNER_ONLY_PERMISSION", "Venues and parameters stay with the owner."
            )
        if group.kind == GroupKind.CUSTOMER and requested:
            raise AppError(
                422, "CUSTOMER_HAS_NO_PERMISSIONS", "Customers book as themselves, not with codes."
            )
        current = (await group_repository.codes_by_group(session, [group.id]))[group.id]
        if caller.system_admin:
            final = requested
        else:
            held = access.permissions
            if granted := (requested - current) - held:
                raise AppError(
                    403,
                    "FORBIDDEN",
                    f"You can only grant permissions you hold: {', '.join(sorted(granted))}.",
                )
            # Touch only what the caller holds; leave the rest as it was.
            final = (requested & held) | (current - held)
            if "USER:Manage" in current - final:
                await self._refuse_self_lockout(session, caller, group, access)
        await group_repository.set_codes(session, group.id, final)
        group.updated_by_user_id = caller.id
        await session.commit()
        await session.refresh(group)
        return group

    # ── Members ──────────────────────────────────────────────────────────

    async def _fillable(
        self, session: AsyncSession, caller: UserModel, group_id: int, *, scope: VenueScope
    ) -> UserGroupModel:
        """A group whose membership the caller may change. Master and Customer
        membership is the owner's alone; Customer is already a 404 to anyone
        else, so only the master check remains to say out loud."""
        group = await self.get(session, group_id, scope=scope)
        if group.kind != GroupKind.STAFF and not caller.system_admin:
            raise owner_only("Only the owner can change who is a venue master.")
        return group

    async def members(
        self,
        session: AsyncSession,
        group_id: int,
        *,
        scope: VenueScope,
        page: int,
        limit: int,
    ) -> tuple[list[MemberOut], int]:
        group = await self.get(session, group_id, scope=scope)
        rows, total = await group_repository.list_members(session, group.id, page=page, limit=limit)
        customers = await group_repository.customers_among(session, [u.id for u, _, _ in rows])
        return [
            MemberOut(
                user_id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                is_active=user.is_active,
                pending_invite=pending,
                is_customer=user.id in customers,
                added_at=added,
            )
            for user, added, pending in rows
        ], total

    async def add_member(
        self,
        session: AsyncSession,
        caller: UserModel,
        group_id: int,
        body: MemberAdd,
        *,
        scope: VenueScope,
    ) -> MemberOut:
        group = await self._fillable(session, caller, group_id, scope=scope)
        user = await user_repository.get_by_email(session, body.email)
        if user is None:
            # Missing and deleted accounts read the same.
            raise AppError(404, "NOT_FOUND", "No account with that email.")
        await session.refresh(user, ["password_hash"])
        pending = user.password_hash is None
        if not user.is_active and not pending:
            raise AppError(422, "USER_INACTIVE", "That account is deactivated.")
        if await group_repository.is_member(session, group.id, user.id):
            raise AppError(409, "ALREADY_MEMBER", "They are already in this group.")
        member = await group_repository.add_member(session, group.id, user.id, added_by=caller.id)
        await session.commit()
        return MemberOut(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
            pending_invite=pending,
            is_customer=await group_repository.is_customer(session, user.id),
            added_at=member.created_at,
        )

    async def remove_member(
        self,
        session: AsyncSession,
        caller: UserModel,
        group_id: int,
        user_id: int,
        *,
        access: VenueAccess,
    ) -> None:
        group = await self._fillable(session, caller, group_id, scope=access.scope)
        if not caller.system_admin:
            assert group.venue_id is not None  # STAFF groups always have one
            if not access.is_master and await group_repository.is_master_at(
                session, user_id, group.venue_id
            ):
                raise owner_only("Only the owner or a venue master can remove a venue master.")
            if user_id == caller.id:
                codes = (await group_repository.codes_by_group(session, [group.id]))[group.id]
                if "USER:Manage" in codes:
                    await self._refuse_self_lockout(session, caller, group, access)
        if not await group_repository.remove_member(session, group.id, user_id):
            raise AppError(404, "NOT_FOUND", "They are not in this group.")
        await session.commit()

    async def _refuse_self_lockout(
        self, session: AsyncSession, caller: UserModel, group: UserGroupModel, access: VenueAccess
    ) -> None:
        """Nobody removes their own last way to manage groups at a venue."""
        if not await group_repository.is_member(session, group.id, caller.id):
            return
        others = await group_repository.staff_codes(
            session, caller.id, venue_id=access.scope.venue_id, excluding_group=group.id
        )
        if "USER:Manage" not in others:
            raise AppError(
                409,
                "SELF_LOCKOUT",
                "That would remove your own access to manage groups here.",
            )

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
