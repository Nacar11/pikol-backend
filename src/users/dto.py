from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.users.domain.roles import RoleCode
from src.users.persistence.models import UserModel


class UserOut(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    mobile: str | None
    role: RoleCode
    venue_id: int | None
    is_active: bool
    email_verified_at: datetime | None

    @classmethod
    def of(cls, user: UserModel) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            mobile=user.mobile,
            role=RoleCode(user.role.code),
            venue_id=user.venue_id,
            is_active=user.is_active,
            email_verified_at=user.email_verified_at,
        )


class ProfileUpdate(BaseModel):
    """What a user may change about themselves. Email, role, and venue are
    absent — and extra="forbid" makes sending them a 422, not a no-op."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    mobile: str | None = Field(default=None, max_length=32)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
