from collections.abc import Awaitable, Callable

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import decode, invalid_token
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.utils.errors import AppError

# auto_error=False so a missing header goes through our envelope as a 401
# INVALID_TOKEN, not FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    session: AsyncSession = Depends(get_session),
) -> UserModel:
    """The caller, loaded fresh on every request.

    Loading (rather than trusting the token's claims) is what makes
    deactivation immediate: a disabled user's still-valid access token stops
    working on the next request instead of 15 minutes later.
    """
    if credentials is None:
        raise invalid_token()
    claims = decode(credentials.credentials, "access")
    user = await user_repository.get_by_id(session, int(claims["sub"]))
    if user is None or not user.is_active:
        raise invalid_token()
    return user


def forbidden() -> AppError:
    return AppError(403, "FORBIDDEN", "You do not have permission to do that.")


def require_permission(code: str) -> Callable[..., Awaitable[UserModel]]:
    """Dependency factory: the caller must hold `code`, or be system_admin.

    system_admin short-circuits before any lookup — the unconditional bypass
    axis, orthogonal to permissions (asima ADR 0001).
    """

    async def guard(
        user: UserModel = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> UserModel:
        if user.system_admin:
            return user
        if code not in await user_repository.permission_codes(session, user):
            raise forbidden()
        return user

    return guard


async def require_super_admin(user: UserModel = Depends(get_current_user)) -> UserModel:
    if not user.system_admin:
        raise forbidden()
    return user


async def require_player(user: UserModel = Depends(get_current_user)) -> UserModel:
    """Online holds are for players. Staff book walk-ins through their own
    route, which carries their own guards (spec §5.2)."""
    if user.role.code != "PLAYER":
        raise forbidden()
    return user
