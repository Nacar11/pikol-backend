from enum import StrEnum


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

_BOOKING_OPS = frozenset(
    {
        "BOOKING:ReadAny",
        "BOOKING:CreateWalkIn",
        "BOOKING:MarkPaid",
        "BOOKING:SetOutcome",
        "BOOKING:CancelAny",
    }
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
    RoleCode.VENUE_MANAGER: frozenset(
        {"COURT:Manage", "PRICE:Manage", "CLOSURE:Manage", "USER:Manage"} | _BOOKING_OPS
    ),
    RoleCode.VENUE_STAFF: frozenset({"CLOSURE:Manage"} | _BOOKING_OPS),
    RoleCode.PLAYER: frozenset(),
}
