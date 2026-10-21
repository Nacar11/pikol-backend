from enum import StrEnum


class PaymentProviderName(StrEnum):
    MOCK = "MOCK"
    MAYA = "MAYA"


class ProviderPaymentStatus(StrEnum):
    """One provider attempt. booking_groups.payment_status holds the verdict."""

    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset(
    {
        ProviderPaymentStatus.PAID,
        ProviderPaymentStatus.FAILED,
        ProviderPaymentStatus.EXPIRED,
        ProviderPaymentStatus.CANCELLED,
    }
)
