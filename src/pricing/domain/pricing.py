"""Price resolution and the convenience fee (spec §4.4).

Pure Python — no SQLAlchemy — so every rule here is unit-tested without a
database, and the booking path (Phase 4) and the admin preview share one
implementation.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from src.utils.time import MANILA


@dataclass(frozen=True)
class PriceRule:
    """NULL (None) is a wildcard. day_of_week is 0 = Sunday … 6 = Saturday;
    both fields are Manila-local."""

    day_of_week: int | None
    hour_of_day: int | None
    price_centavos: int


class PriceResolver:
    """One court's rules, indexed once and resolved in memory.

    A 6-court venue-day is 144 cells. Build one resolver per court from rules
    loaded in a single query and call price_at per cell — never one SQL
    lookup per cell (spec §7.2).
    """

    def __init__(self, rules: Iterable[PriceRule], default_centavos: int) -> None:
        self._prices = {(r.day_of_week, r.hour_of_day): r.price_centavos for r in rules}
        self._default = default_centavos

    def price_at(self, starts_at: datetime) -> int:
        if starts_at.tzinfo is None:
            # A naive value would be read as Manila time by astimezone() and
            # price the wrong hour without a sound. Refuse it.
            raise ValueError("starts_at must be timezone-aware")
        local = starts_at.astimezone(MANILA)
        day, hour = local.isoweekday() % 7, local.hour  # isoweekday: Sunday = 7
        # Most specific wins: (day, hour) → (hour) → (day) → flat → parameter.
        for key in ((day, hour), (None, hour), (day, None), (None, None)):
            if key in self._prices:
                return self._prices[key]
        return self._default


def convenience_fee_centavos(price_centavos: int, percent: Decimal) -> int:
    """round_half_up(price × percent / 100), in whole centavos.

    Decimal, never float, and ROUND_HALF_UP explicitly: Python's round() is
    banker's rounding, which would charge ₱0.02 on some amounts and ₱0.01 on
    others for the same half-centavo.
    """
    fee = Decimal(price_centavos) * percent / 100
    return int(fee.quantize(Decimal(1), rounding=ROUND_HALF_UP))
