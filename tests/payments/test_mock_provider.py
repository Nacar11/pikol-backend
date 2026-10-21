import pytest

from src.payments.adapters.mock import MockProvider
from src.payments.domain.enums import ProviderPaymentStatus
from src.payments.domain.port import PaymentNotFound


async def test_round_trip_through_maya_shapes() -> None:
    provider = MockProvider(api_base_url="http://api.test")
    session = await provider.create_checkout(
        amount_centavos=164800, reference="PKL-AB234", description="x", return_url="http://r"
    )

    pending = await provider.fetch_payment(session.checkout_id)
    provider.settle(session.checkout_id, "PAYMENT_SUCCESS")
    paid = await provider.fetch_payment(session.checkout_id)

    assert (
        session.checkout_url == f"http://api.test/api/v1/mock-maya/checkouts/{session.checkout_id}"
    )
    assert pending.status == ProviderPaymentStatus.PENDING
    assert paid.status == ProviderPaymentStatus.PAID
    assert paid.amount_centavos == 164800
    assert paid.reference == "PKL-AB234"
    assert paid.payment_id == session.checkout_id  # Maya: the checkoutId IS the paymentId


async def test_unknown_payment_is_not_found() -> None:
    with pytest.raises(PaymentNotFound):
        await MockProvider(api_base_url="http://api.test").fetch_payment("nope")


async def test_webhook_body_is_maya_shaped() -> None:
    provider = MockProvider(api_base_url="http://api.test")
    session = await provider.create_checkout(
        amount_centavos=100, reference="R", description="x", return_url="http://r"
    )
    provider.settle(session.checkout_id, "PAYMENT_SUCCESS")

    assert provider.parse_webhook(provider.webhook_body(session.checkout_id)).payment_id == (
        session.checkout_id
    )
