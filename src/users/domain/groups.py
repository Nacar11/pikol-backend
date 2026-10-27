from enum import StrEnum


class GroupKind(StrEnum):
    """ADR 0003. CUSTOMER is the one venue-less group; its membership IS the
    right to book as yourself. MASTER and STAFF belong to a venue."""

    CUSTOMER = "CUSTOMER"
    MASTER = "MASTER"
    STAFF = "STAFF"


CUSTOMER_GROUP_NAME = "Customer"
MASTER_GROUP_NAME = "Venue master"
FRONT_DESK_GROUP_NAME = "Front desk"

# Owner-only: held through users.system_admin alone and never grantable to
# any group, by anyone — including the super admin (ADR 0003).
OWNER_ONLY: frozenset[str] = frozenset({"VENUE:Manage", "PARAMETER:Manage"})

_BOOKING_OPS = frozenset(
    {
        "BOOKING:ReadAny",
        "BOOKING:CreateWalkIn",
        "BOOKING:MarkPaid",
        "BOOKING:SetOutcome",
        "BOOKING:CancelAny",
    }
)

# The seeds every venue is created with (dbml Appendix B). Migration 0006
# backfills FROM these, so the code and the database cannot disagree.
MASTER_DEFAULT_CODES: frozenset[str] = frozenset(
    {"COURT:Manage", "PRICE:Manage", "CLOSURE:Manage", "USER:Manage"} | _BOOKING_OPS
)
FRONT_DESK_CODES: frozenset[str] = frozenset({"CLOSURE:Manage"} | _BOOKING_OPS)
