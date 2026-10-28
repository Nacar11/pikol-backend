from enum import StrEnum


class ActionEventType(StrEnum):
    """One per lifecycle change of a booking or its payment (dbml
    `action_event_type`). "Play started" and "Hold ended" are derived on
    read and never stored, so they are not here."""

    HOLD_PLACED = "HOLD_PLACED"
    WALK_IN_BOOKED = "WALK_IN_BOOKED"
    CLOSURE_CREATED = "CLOSURE_CREATED"
    PAYMENT_STARTED = "PAYMENT_STARTED"  # checkout-level
    PAYMENT_SUCCEEDED = "PAYMENT_SUCCEEDED"  # checkout-level
    PAYMENT_FAILED = "PAYMENT_FAILED"  # checkout-level
    PAYMENT_CANCELLED = "PAYMENT_CANCELLED"  # checkout-level
    CONFIRMED = "CONFIRMED"
    MARKED_PAID = "MARKED_PAID"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"  # dated at the hold's deadline, not the cleanup
    REFUNDED = "REFUNDED"
    VOIDED = "VOIDED"
    FLAGGED = "FLAGGED"  # staff only
    RESOLVED = "RESOLVED"  # staff only
    REOPENED = "REOPENED"


class ActorKind(StrEnum):
    STAFF = "STAFF"
    CUSTOMER = "CUSTOMER"
    SYSTEM = "SYSTEM"  # Maya, the hold clock, the refund flow — no user
