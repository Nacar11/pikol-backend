import json
from decimal import Decimal

import pytest

from src.payments.adapters.maya_wire import (
    checkout_request,
    from_pesos,
    parse_notice,
    parse_payment,
    to_pesos,
)
from src.payments.domain.enums import ProviderPaymentStatus
from src.payments.domain.port import ProviderError


def test_centavos_become_two_decimal_pesos() -> None:
    assert to_pesos(164800) == Decimal("1648.00")
    assert to_pesos(1) == Decimal("0.01")
    assert str(to_pesos(50000)) == "500.00"


@pytest.mark.parametrize(("value", "centavos"), [("1648.00", 164800), ("0.01", 1), (500, 50000)])
def test_pesos_become_centavos(value: str | int, centavos: int) -> None:
    assert from_pesos(value) == centavos


def test_sub_centavo_amount_rejected() -> None:
    with pytest.raises(ValueError):
        from_pesos("16.485")


def test_float_amount_rejected() -> None:
    """A float has already lost precision by the time it arrives here."""
    with pytest.raises(TypeError):
        from_pesos(16.48)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("maya", "ours"),
    [
        ("PAYMENT_SUCCESS", ProviderPaymentStatus.PAID),
        ("PAYMENT_FAILED", ProviderPaymentStatus.FAILED),
        ("PAYMENT_EXPIRED", ProviderPaymentStatus.EXPIRED),
        ("PAYMENT_CANCELLED", ProviderPaymentStatus.CANCELLED),
        ("PENDING_TOKEN", ProviderPaymentStatus.PENDING),
        ("SOMETHING_NEW", ProviderPaymentStatus.PENDING),
    ],
)
def test_status_mapping(maya: str, ours: ProviderPaymentStatus) -> None:
    raw = {"id": "p1", "status": maya, "amount": "1.00", "requestReferenceNumber": "PKL-AAAAA"}
    assert parse_payment(raw).status == ours


def test_parse_payment_reads_both_amount_shapes() -> None:
    flat = {
        "id": "p",
        "status": "PAYMENT_SUCCESS",
        "amount": "16.48",
        "requestReferenceNumber": "R",
    }
    nested = {
        "id": "p",
        "paymentStatus": "PAYMENT_SUCCESS",
        "totalAmount": {"value": "16.48", "currency": "PHP"},
        "requestReferenceNumber": "R",
    }
    assert parse_payment(flat).amount_centavos == parse_payment(nested).amount_centavos == 1648


def test_parse_payment_rejects_other_currency() -> None:
    raw = {
        "id": "p",
        "status": "PAYMENT_SUCCESS",
        "totalAmount": {"value": "1.00", "currency": "USD"},
        "requestReferenceNumber": "R",
    }
    with pytest.raises(ProviderError):
        parse_payment(raw)


def test_parse_notice_reads_only_the_id() -> None:
    body = json.dumps({"id": "pay-1", "paymentStatus": "PAYMENT_SUCCESS", "amount": "999"})
    assert parse_notice(body.encode()).payment_id == "pay-1"


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"id": 5}'])
def test_parse_notice_rejects_garbage(body: bytes) -> None:
    with pytest.raises(ProviderError):
        parse_notice(body)


def test_checkout_request_shape() -> None:
    request = checkout_request(
        amount_centavos=164800,
        reference="PKL-AB234",
        description="Court 1",
        return_url="http://localhost:3000/checkout/return?booking_id=5",
    )
    assert request["totalAmount"] == {"value": "1648.00", "currency": "PHP"}
    assert request["requestReferenceNumber"] == "PKL-AB234"
    assert set(request["redirectUrl"]) == {"success", "failure", "cancel"}
