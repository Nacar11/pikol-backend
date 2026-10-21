from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.database.session import get_session
from src.users.dto import PasswordChangeRequest, ProfileUpdate, UserOut
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.users.service import user_service
from src.utils.api import API_PREFIX

# Identity comes from the token, never the URL — there is no /users/{id}
# here, so a caller cannot even name someone else (spec §3.5).
router = APIRouter(prefix=f"{API_PREFIX}/users/me", tags=["me"])


@router.get("")
async def get_me(user: UserModel = Depends(get_current_user)) -> UserOut:
    return UserOut.of(user)


@router.patch("")
async def update_me(
    body: ProfileUpdate,
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    return UserOut.of(await user_service.update_profile(session, user, body))


@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revokes every refresh token; the client logs in again."""
    await user_service.change_password(session, user, body)


@router.get("/permissions")
async def my_permissions(
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """What the frontend gates UI on — never a role parsed client-side."""
    return await user_repository.permission_codes(session, user)
