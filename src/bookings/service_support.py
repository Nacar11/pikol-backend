from src.bookings.domain.rules import BookingSnapshot
from src.bookings.persistence.models import BookingGroupModel


def snapshot(group: BookingGroupModel) -> BookingSnapshot:
    """The domain's view of a loaded group. Slots are loaded ordered by
    starts_at, so the first is the session start."""
    return BookingSnapshot(
        type=group.type,
        booking_status=group.booking_status,
        payment_status=group.payment_status,
        starts_at=group.slots[0].starts_at,
        hours=len(group.slots),
        expires_at=group.expires_at,
    )
