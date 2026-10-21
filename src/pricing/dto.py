from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.pricing.persistence.models import PriceRuleModel

# ₱100,000 per hour. A typo'd extra zero should be refused, not charged.
MAX_PRICE_CENTAVOS = 10_000_000

Price = Field(ge=0, le=MAX_PRICE_CENTAVOS)


class PriceRuleOut(BaseModel):
    id: int
    court_id: int
    day_of_week: int | None
    hour_of_day: int | None
    price_centavos: int

    @classmethod
    def of(cls, rule: PriceRuleModel) -> "PriceRuleOut":
        return cls(
            id=rule.id,
            court_id=rule.court_id,
            day_of_week=rule.day_of_week,
            hour_of_day=rule.hour_of_day,
            price_centavos=rule.price_centavos,
        )


class PriceRuleCreate(BaseModel):
    """Omit day_of_week / hour_of_day for "any". day_of_week is 0 = Sunday;
    both are Manila-local."""

    model_config = ConfigDict(extra="forbid")

    day_of_week: int | None = Field(default=None, ge=0, le=6)
    hour_of_day: int | None = Field(default=None, ge=0, le=23)
    price_centavos: int = Price


class PriceRuleUpdate(BaseModel):
    """Price only. Changing day or hour makes it a different rule — delete
    and create, so the uniqueness constraint stays the only arbiter."""

    model_config = ConfigDict(extra="forbid")

    price_centavos: int = Price


class ResolvedPrice(BaseModel):
    starts_at: datetime  # UTC
    hour: int  # Manila hour, 0–23
    price_centavos: int
