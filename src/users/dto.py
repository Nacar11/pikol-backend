from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.users.domain.groups import GroupKind
from src.users.persistence.models import UserGroupModel, UserModel
from src.utils.fields import LookupEmail


class GroupRef(BaseModel):
    id: int
    name: str
    kind: GroupKind
    venue_id: int | None

    @classmethod
    def of(cls, group: UserGroupModel) -> "GroupRef":
        return cls(id=group.id, name=group.name, kind=group.kind, venue_id=group.venue_id)


class UserOut(BaseModel):
    """`groups` is the caller's view: memberships at their venue plus
    Customer, never another venue's (ADR 0003)."""

    id: int
    email: str
    first_name: str
    last_name: str
    mobile: str | None
    is_active: bool
    email_verified_at: datetime | None
    groups: list[GroupRef]

    @classmethod
    def of(cls, user: UserModel, groups: list[UserGroupModel]) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            mobile=user.mobile,
            is_active=user.is_active,
            email_verified_at=user.email_verified_at,
            groups=[GroupRef.of(g) for g in groups],
        )


class MeOut(UserOut):
    """The caller's own account. `is_customer` is the Customer-group axis
    (ADR 0003) — staff access comes from /users/me/venues."""

    is_customer: bool


class MyVenueOut(BaseModel):
    """A venue the caller may act at — what the console's switcher lists."""

    venue_id: int
    name: str
    is_master: bool
    groups: list[str]


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


class StaffInviteRequest(BaseModel):
    """An invite into one group. There is no venue or role field: the venue
    is the group's, and the server checks the group is one the caller may
    fill (ADR 0003). extra="forbid" turns a smuggled venue_id into a 422."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    mobile: str | None = Field(default=None, max_length=32)
    group_id: int


class AdminInviteRequest(StaffInviteRequest):
    """The SUPER_ADMIN's invite: the same body, but any staff or master
    group at any venue. A super admin is never invited — only the CLI makes
    one."""


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    mobile: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None


class GroupOut(BaseModel):
    """`read_only` is from the caller's side: a venue's master group is the
    owner's to edit, so it is read-only to everyone else (ADR 0003)."""

    id: int
    name: str
    kind: GroupKind
    venue_id: int | None
    member_count: int
    permissions: list[str]
    read_only: bool


class GroupCreate(BaseModel):
    """Always a STAFF group: there is no `kind`, and extra="forbid" makes
    sending one a 422. `venue_id` is the owner's alone; for anyone else the
    server takes the active venue and refuses a body that names one."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    venue_id: int | None = None


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)


class GroupPermissionsUpdate(BaseModel):
    """The whole checkbox state. Codes the caller does not hold are kept on
    the group as they were, whatever this list says (ADR 0003)."""

    model_config = ConfigDict(extra="forbid")

    codes: list[str] = Field(max_length=100)


class PermissionOut(BaseModel):
    code: str
    resource: str
    action: str
    description: str | None
    owner_only: bool


class MemberOut(BaseModel):
    """`pending_invite`: invited but no password set yet. `is_customer` backs
    the "also a customer" tag."""

    user_id: int
    email: str
    first_name: str
    last_name: str
    is_active: bool
    pending_invite: bool
    is_customer: bool
    added_at: datetime


class MemberAdd(BaseModel):
    """By exact email: finds one person and lists nobody else, so the
    picker can never become a directory of every customer (ADR 0003)."""

    model_config = ConfigDict(extra="forbid")

    email: LookupEmail
