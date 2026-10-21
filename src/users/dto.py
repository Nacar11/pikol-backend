from datetime import datetime

from pydantic import BaseModel

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
