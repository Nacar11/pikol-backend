from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from src.payments.adapters.mock import MockProvider
from src.payments.domain.enums import PaymentOutcome, RefundKind
from src.payments.domain.port import PaymentNotFound, ProviderError
from src.utils.api import API_PREFIX


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
    assert pending.status == PaymentOutcome.PENDING
    assert paid.status == PaymentOutcome.SUCCESSFUL
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


async def _paid(provider: MockProvider) -> str:
    session = await provider.create_checkout(
        amount_centavos=51500, reference="R", description="x", return_url="http://r"
    )
    provider.settle(session.checkout_id, "PAYMENT_SUCCESS")
    return session.checkout_id


async def test_mock_void_marks_voided_and_records_it() -> None:
    provider = MockProvider(api_base_url="http://api.test")
    payment_id = await _paid(provider)

    reference = await provider.void(payment_id)

    assert (await provider.fetch_payment(payment_id)).provider_status == "VOIDED"
    assert provider.undone[payment_id] == (RefundKind.VOID, reference, 51500)


async def test_mock_refund_records_amount() -> None:
    provider = MockProvider(api_base_url="http://api.test")
    payment_id = await _paid(provider)

    reference = await provider.refund(payment_id, 51500)

    assert (await provider.fetch_payment(payment_id)).status == PaymentOutcome.REFUNDED
    assert provider.undone[payment_id] == (RefundKind.REFUND, reference, 51500)


async def test_mock_undo_failure_raises_provider_error() -> None:
    provider = MockProvider(api_base_url="http://api.test")
    payment_id = await _paid(provider)
    provider.fail_undo = True

    with pytest.raises(ProviderError):
        await provider.void(payment_id)
    with pytest.raises(ProviderError):
        await provider.refund(payment_id, 51500)
    assert payment_id not in provider.undone
    assert (await provider.fetch_payment(payment_id)).provider_status == "PAYMENT_SUCCESS"


async def test_settle_stamps_payment_at_on_success() -> None:
    provider = MockProvider(api_base_url="http://api.test")
    session = await provider.create_checkout(
        amount_centavos=100, reference="R", description="x", return_url="http://r"
    )
    when = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)

    provider.settle(session.checkout_id, "PAYMENT_SUCCESS", paid_at=when)

    assert (await provider.fetch_payment(session.checkout_id)).paid_at == when


async def test_mock_page_offers_four_outcomes(client: AsyncClient, provider: MockProvider) -> None:
    session = await provider.create_checkout(
        amount_centavos=51500, reference="PKL-AB234", description="x", return_url="http://r"
    )

    page = await client.get(f"{API_PREFIX}/mock-maya/checkouts/{session.checkout_id}")

    assert page.status_code == 200
    for label, outcome in (
        ("Pay", "success"),
        ("Decline", "failure"),
        ("Back out", "cancel"),
        ("Expire", "expire"),
    ):
        assert f">{label}</button>" in page.text
        assert f"/{session.checkout_id}/{outcome}" in page.text
