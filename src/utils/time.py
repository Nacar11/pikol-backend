from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MANILA = ZoneInfo("Asia/Manila")


def manila_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Half-open UTC bounds [start, end) of one Manila calendar day.

    EVERY day-bounded query goes through this. Writing
    `WHERE starts_at::date = :day` instead slices on UTC days, which returns
    the wrong 24 hours and puts the 12am-1am slot — the one with its own
    price rule — on the previous day's grid.

    PH has no DST, so the offset is a fixed +08 and this needs no special
    cases.
    """
    start = datetime.combine(day, time.min, tzinfo=MANILA)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)
