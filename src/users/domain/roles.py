from enum import StrEnum

from src.users.domain.groups import FRONT_DESK_CODES, MASTER_DEFAULT_CODES


class RoleCode(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    VENUE_MANAGER = "VENUE_MANAGER"
    VENUE_STAFF = "VENUE_STAFF"
    PLAYER = "PLAYER"


# Every permission code (spec §6.3), as RESOURCE:Action.
PERMISSIONS: tuple[str, ...] = (
    "VENUE:Manage",
    "COURT:Manage",
    "PRICE:Manage",
    "CLOSURE:Manage",
    "BOOKING:ReadAny",
    "BOOKING:CreateWalkIn",
    "BOOKING:MarkPaid",
    "BOOKING:SetOutcome",
    "BOOKING:CancelAny",
    "USER:Manage",
    "PARAMETER:Manage",
)

# The seeded matrix (dbml Appendix B). The 0002 migration seeds FROM this, so
# the code and the database cannot disagree.
#
# SUPER_ADMIN holds nothing here on purpose: users.system_admin short-circuits
# the permission guard (asima ADR 0001), so the owner never depends on a row.
# VENUE_MANAGER's USER:Manage is narrowed to VENUE_STAFF at their own venue by
# the endpoint, not by this table — see spec §6.4.
ROLE_PERMISSIONS: dict[RoleCode, frozenset[str]] = {
    RoleCode.SUPER_ADMIN: frozenset(),
    RoleCode.VENUE_MANAGER: MASTER_DEFAULT_CODES,
    RoleCode.VENUE_STAFF: FRONT_DESK_CODES,
    RoleCode.PLAYER: frozenset(),
}
