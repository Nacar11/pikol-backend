import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from src.config.settings import get_settings
from src.utils.errors import AppError

_hasher = PasswordHasher()  # argon2id, library-default cost

# Verified against when the email is unknown, so a login for a nonexistent
# account costs the same as one with a wrong password. Without it the
# response time alone says which emails have accounts.
_DUMMY_HASH = _hasher.hash("pikol-timing-equaliser")


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str | None) -> bool:
    """False on any mismatch or malformed hash; never raises."""
    try:
        return _hasher.verify(hashed or _DUMMY_HASH, raw) and hashed is not None
    except (VerificationError, InvalidHashError):
        return False


def new_opaque_token() -> tuple[str, str]:
    """A single-use email token as (raw, sha256_hex).

    The raw value goes into the email and nowhere else; the hash is stored.
    SHA-256 rather than argon2 because the token is 256 random bits — there
    is nothing to brute-force — and the hash must be indexable for lookup.
    """
    raw = secrets.token_urlsafe(32)
    return raw, hash_opaque_token(raw)


def hash_opaque_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]


def invalid_token() -> AppError:
    return AppError(401, "INVALID_TOKEN", "Your session is invalid or has expired.")


def encode_access(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def encode_refresh(user_id: int, jti: UUID) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.refresh_token_days)
    claims = {"sub": str(user_id), "typ": "refresh", "jti": str(jti), "iat": now, "exp": expires_at}
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, expires_at


def decode(token: str, expected: TokenType) -> dict[str, Any]:
    """Verify signature, expiry, and type; any failure is INVALID_TOKEN.

    The `typ` check is what stops a 7-day refresh token being used as an
    access token, which would turn every refresh token into a week-long
    bearer credential that no revocation ledger covers.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],  # pinned: never let the token choose
            options={"require": ["sub", "typ", "exp", "iat"]},
        )
    except jwt.PyJWTError:
        raise invalid_token() from None
    if claims["typ"] != expected:
        raise invalid_token()
    return claims
