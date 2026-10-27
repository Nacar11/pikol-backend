from enum import StrEnum


class PaymentProviderName(StrEnum):
    MOCK = "MOCK"
    MAYA = "MAYA"


class PaymentOutcome(StrEnum):
    """The five statuses a customer sees (ADR 0004). `checkouts.status` holds
    the verdict; `payments.status` is what the one attempt came to. Maya's own
    wording is kept beside it in `payments.provider_status`."""

    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"  # backed out, or the hold ended first — may still turn paid
    REFUNDED = "REFUNDED"  # paid after the hold ended and an hour was taken


class RefundKind(StrEnum):
    VOID = "VOID"  # same Manila day as the payment
    REFUND = "REFUND"  # from the next day
