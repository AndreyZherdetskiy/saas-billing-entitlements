from datetime import UTC, datetime

from billing_platform.workers.tasks.create_usage_partition import month_bounds


def test_month_bounds_february_2026() -> None:
    start, end = month_bounds(datetime(2026, 2, 18, tzinfo=UTC))
    assert start == datetime(2026, 2, 1, tzinfo=UTC)
    assert end == datetime(2026, 3, 1, tzinfo=UTC)
