from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.domain.roles import RoleCode
from src.users.persistence.models import RoleModel, UserModel


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


user_repository = UserRepository()
