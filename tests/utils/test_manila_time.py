from datetime import UTC, date, datetime, timedelta

from src.utils.time import MANILA, manila_day_bounds


def test_manila_day_begins_at_1600_utc_the_previous_day() -> None:
    start, end = manila_day_bounds(date(2026, 9, 15))

    assert start == datetime(2026, 9, 14, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 15, 16, 0, tzinfo=UTC)


def test_bounds_span_exactly_one_day() -> None:
    start, end = manila_day_bounds(date(2026, 9, 15))

    assert end - start == timedelta(days=1)


def test_the_midnight_slot_belongs_to_its_own_manila_day() -> None:
    """This is the test that matters. The 12am-1am slot has its own price
    rule, so misplacing it onto the previous day changes what a player is
    charged. Slicing on `starts_at::date` in UTC puts it on 14 September."""
    midnight_slot = datetime(2026, 9, 15, 0, 0, tzinfo=MANILA).astimezone(UTC)
    start, end = manila_day_bounds(date(2026, 9, 15))

    assert start <= midnight_slot < end


def test_the_last_slot_of_the_day_is_inside_the_bounds() -> None:
    last_slot = datetime(2026, 9, 15, 23, 0, tzinfo=MANILA).astimezone(UTC)
    start, end = manila_day_bounds(date(2026, 9, 15))

    assert start <= last_slot < end
