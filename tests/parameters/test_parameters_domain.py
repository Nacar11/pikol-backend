from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.parameters.domain.parameters import Parameters

VALID: dict[str, str] = {
    "default_slot_price_centavos": "50000",
    "convenience_fee_percent": "3.00",
    "hold_duration_minutes": "15",
    "booking_horizon_days": "30",
    "max_active_holds_per_player": "3",
    "staff_backdate_days": "7",
    "cancellation_cutoff_minutes": "0",
    "min_lead_minutes": "20",
    "invitation_expiry_days": "7",
    "webhook_payload_retention_days": "90",
}


def test_coerces_stored_strings_to_types() -> None:
    params = Parameters.model_validate(VALID)

    assert params.default_slot_price_centavos == 50000
    assert params.convenience_fee_percent == Decimal("3.00")
    assert params.hold_duration_minutes == 15


def test_non_numeric_value_fails_loudly() -> None:
    """A typo in a money-moving parameter must raise, never silently become
    zero and charge every player nothing."""
    broken = VALID | {"convenience_fee_percent": "abc"}

    with pytest.raises(ValidationError) as exc:
        Parameters.model_validate(broken)

    assert "convenience_fee_percent" in str(exc.value)


def test_min_lead_below_hold_duration_is_rejected() -> None:
    """Spec section 4.5: were min_lead_minutes lower than hold_duration,
    a player could hold a slot starting in 5 minutes, pay at minute 12, and
    have the webhook confirm a session that began 7 minutes earlier."""
    broken = VALID | {"min_lead_minutes": "10", "hold_duration_minutes": "15"}

    with pytest.raises(ValidationError) as exc:
        Parameters.model_validate(broken)

    assert "min_lead_minutes" in str(exc.value)


def test_missing_parameter_is_rejected() -> None:
    incomplete = {k: v for k, v in VALID.items() if k != "hold_duration_minutes"}

    with pytest.raises(ValidationError):
        Parameters.model_validate(incomplete)


def test_negative_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Parameters.model_validate(VALID | {"default_slot_price_centavos": "-1"})
