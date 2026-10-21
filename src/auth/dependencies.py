from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import decode, invalid_token
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository

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
