from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.roles import PERMISSIONS, RoleCode
from src.users.persistence.models import PermissionModel, RoleModel, UserModel, role_permissions

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

    async def permission_codes(self, session: AsyncSession, user: UserModel) -> list[str]:
        """Sorted codes the user holds. system_admin holds every one: it is
        the bypass axis, so it never depends on a role_permissions row."""
        if user.system_admin:
            return sorted(PERMISSIONS)
        result = await session.execute(
            select(PermissionModel.code)
            .join(role_permissions, role_permissions.c.permission_id == PermissionModel.id)
            .where(role_permissions.c.role_id == user.role_id)
            .order_by(PermissionModel.code)
        )
        return list(result.scalars().all())

    # The two methods below read venue-owned data, so `scope` is keyword-only
    # with NO default — see VenueScope.

    async def list(
        self, session: AsyncSession, *, scope: "VenueScope", page: int, limit: int
    ) -> tuple[list[UserModel], int]:
        live = (UserModel.deleted_at.is_(None), scope.where(UserModel.venue_id))
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
                scope.where(UserModel.venue_id),
            )
        )
        return result.scalar_one_or_none()


user_repository = UserRepository()
