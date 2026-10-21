from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.pricing.domain.pricing import PriceResolver, PriceRule, convenience_fee_centavos

SATURDAY, MONDAY = 6, 1
DEFAULT = 50000

# The §4.4 table: flat ₱700, Saturday ₱800, 6pm ₱600, Saturday 6pm ₱900.
FLAT = PriceRule(None, None, 70000)
SAT = PriceRule(SATURDAY, None, 80000)
SIX_PM = PriceRule(None, 18, 60000)
SAT_SIX_PM = PriceRule(SATURDAY, 18, 90000)
ALL = [FLAT, SAT, SIX_PM, SAT_SIX_PM]


def manila(day: int, hour: int) -> datetime:
    """2026-09-19 is a Saturday; 2026-09-21 a Monday. Manila is UTC+8."""
    return datetime.fromisoformat(f"2026-09-{day:02d}T{hour:02d}:00:00+08:00")


def test_day_and_hour_beats_everything() -> None:
    assert PriceResolver(ALL, DEFAULT).price_at(manila(19, 18)) == 90000


def test_hour_beats_day() -> None:
    assert PriceResolver([FLAT, SAT, SIX_PM], DEFAULT).price_at(manila(19, 18)) == 60000


def test_day_beats_flat() -> None:
    assert PriceResolver(ALL, DEFAULT).price_at(manila(19, 10)) == 80000


def test_flat_beats_default() -> None:
    assert PriceResolver(ALL, DEFAULT).price_at(manila(21, 10)) == 70000


def test_no_rules_falls_back_to_parameter() -> None:
    assert PriceResolver([], DEFAULT).price_at(manila(21, 10)) == DEFAULT


def test_saturday_midnight_rule_prices_friday_1600_utc() -> None:
    """Manila Saturday 00:00 is Friday 16:00 UTC. Resolving on the UTC weekday
    would charge the Friday price for the Saturday 12am slot."""
    resolver = PriceResolver([PriceRule(SATURDAY, 0, 12345)], DEFAULT)

    assert resolver.price_at(datetime(2026, 9, 18, 16, tzinfo=UTC)) == 12345
    assert resolver.price_at(datetime(2026, 9, 19, 16, tzinfo=UTC)) == DEFAULT


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        PriceResolver(ALL, DEFAULT).price_at(datetime(2026, 9, 19, 18))


@pytest.mark.parametrize(
    ("price", "percent", "fee"),
    [
        (50000, Decimal("3.00"), 1500),
        (33333, Decimal("3.00"), 1000),  # 999.99 → 1000
        (50, Decimal("3.00"), 2),  # 1.5 rounds half UP, not to even
        (70000, Decimal("0"), 0),
    ],
)
def test_convenience_fee(price: int, percent: Decimal, fee: int) -> None:
    assert convenience_fee_centavos(price, percent) == fee
