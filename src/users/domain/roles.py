# Every permission code (spec §6.3), as RESOURCE:Action. Groups hold them
# (ADR 0003); users.system_admin holds them all without a row.
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
