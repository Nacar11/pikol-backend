from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.roles import RoleCode
from src.users.persistence.models import RoleModel, UserGroupMemberModel, UserGroupModel, UserModel

if TYPE_CHECKING:
    from src.auth.scope import VenueScope


class UserRepository:
    async def get_by_email(self, session: AsyncSession, email: str) -> UserModel | None:
        """Case-insensitive, live accounts only — the same rule as the
        users_email_lower_uq index, so lookup and uniqueness agree."""
        result = await session.execute(
            select(UserModel).where(
                func.lower(UserModel.email) == email.lower(),
                UserModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, session: AsyncSession, user_id: int) -> UserModel | None:
        result = await session.execute(
            select(UserModel).where(UserModel.id == user_id, UserModel.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def role_id(self, session: AsyncSession, code: RoleCode) -> int:
        result = await session.execute(select(RoleModel.id).where(RoleModel.code == code))
        return result.scalar_one()

    # The two methods below read venue-owned data, so `scope` is keyword-only
    # with NO default — see VenueScope.

    @staticmethod
    def _at(scope: "VenueScope") -> ColumnElement[bool]:
        """Someone is "at" a venue when they hold one of its groups (ADR 0003).
        The owner sees everyone, members of no group included."""
        if scope.all_venues:
            return true()
        return exists().where(
            UserGroupMemberModel.user_id == UserModel.id,
            UserGroupMemberModel.group_id == UserGroupModel.id,
            scope.where(UserGroupModel.venue_id),
        )

    async def list(
        self,
        session: AsyncSession,
        *,
        scope: "VenueScope",
        page: int,
        limit: int,
        q: str | None = None,
    ) -> tuple[list[UserModel], int]:
        live = [UserModel.deleted_at.is_(None), self._at(scope)]
        if q:
            # Escape LIKE's wildcards so "a_b" matches literally.
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            live.append(
                or_(
                    *(
                        column.ilike(pattern, escape="\\")
                        for column in (UserModel.email, UserModel.first_name, UserModel.last_name)
                    )
                )
            )
        total = (await session.execute(select(func.count()).where(*live))).scalar_one()
        rows = await session.execute(
            select(UserModel)
            .where(*live)
            .order_by(UserModel.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.scalars().all()), total

    async def get_scoped(
        self, session: AsyncSession, user_id: int, *, scope: "VenueScope"
    ) -> UserModel | None:
        result = await session.execute(
            select(UserModel).where(
                UserModel.id == user_id,
                UserModel.deleted_at.is_(None),
                self._at(scope),
            )
        )
        return result.scalar_one_or_none()


user_repository = UserRepository()
