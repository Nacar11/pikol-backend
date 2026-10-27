from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, model_validator


class Parameters(BaseModel):
    """Every tunable business rule, typed (spec §4.5).

    Values arrive from the database as text; Pydantic does the coercion, so a
    typo raises here rather than silently becoming zero somewhere that moves
    money.
    """

    # "ignore", not "forbid": after a migration adds a row, the release still
    # serving must not 500 every booking until the deploy lands. Missing keys
    # still raise — every field is required.
    model_config = {"extra": "ignore"}

    default_slot_price_centavos: int = Field(ge=0)
    convenience_fee_percent: Decimal = Field(ge=0, le=100)
    hold_duration_minutes: int = Field(gt=0)
    booking_horizon_days: int = Field(gt=0)
    max_active_holds_per_player: int = Field(gt=0)
    staff_backdate_days: int = Field(ge=0)
    cancellation_cutoff_minutes: int = Field(ge=0)
    min_lead_minutes: int = Field(ge=0)
    invitation_expiry_days: int = Field(gt=0)
    webhook_payload_retention_days: int = Field(gt=0)

    @model_validator(mode="after")
    def lead_time_must_cover_the_hold(self) -> Self:
        if self.min_lead_minutes < self.hold_duration_minutes:
            raise ValueError(
                f"min_lead_minutes ({self.min_lead_minutes}) must be >= "
                f"hold_duration_minutes ({self.hold_duration_minutes}); "
                f"otherwise a hold can outlive the slot it holds"
            )
        return self
