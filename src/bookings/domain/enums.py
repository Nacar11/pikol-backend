from enum import StrEnum


class BookingType(StrEnum):
    PLAYER_BOOKING = "PLAYER_BOOKING"
    WALK_IN = "WALK_IN"
    MAINTENANCE = "MAINTENANCE"  # a closure — same table, so one index blocks every collision


class BookingStatus(StrEnum):
    PENDING = "PENDING"  # a hold; NEVER "held" without expires_at > now
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PaymentStatus(StrEnum):
    UNPAID = "UNPAID"
    PAID = "PAID"
    FAILED = "FAILED"


class PaymentMethod(StrEnum):
    ONLINE = "ONLINE"  # through the provider; the player also pays the fee
    CASH = "CASH"
    MANUAL_GCASH = "MANUAL_GCASH"  # scanned the venue's own QR; the provider never sees it


# The only statuses that free a slot — the uq_court_slot_active predicate.
RELEASING = frozenset({BookingStatus.CANCELLED, BookingStatus.EXPIRED})
