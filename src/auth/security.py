import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

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
