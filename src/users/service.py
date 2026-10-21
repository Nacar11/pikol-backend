from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import hash_password, verify_password
from src.auth.service import auth_service
from src.users.dto import PasswordChangeRequest, ProfileUpdate
from src.users.persistence.models import UserModel
from src.utils.errors import AppError


class UserService:
    async def update_profile(
        self, session: AsyncSession, user: UserModel, body: ProfileUpdate
    ) -> UserModel:
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await session.commit()
        await session.refresh(user, ["role"])
        return user

    async def change_password(
        self, session: AsyncSession, user: UserModel, body: PasswordChangeRequest
    ) -> None:
        await session.refresh(user, ["password_hash"])
        if not verify_password(body.current_password, user.password_hash):
            raise AppError(400, "WRONG_PASSWORD", "Your current password is incorrect.")
        user.password_hash = hash_password(body.new_password)
        await auth_service.revoke_all(session, user.id)
        await session.commit()


user_service = UserService()
