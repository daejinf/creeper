from datetime import datetime, timezone

import pytest

from moim.timeutil import to_display, to_utc, utcnow


def test_utcnow_is_aware_utc():
    assert utcnow().utcoffset().total_seconds() == 0


def test_display_is_asia_seoul():
    d = to_display(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert (d.hour, d.day) == (9, 1)
    assert str(d.tzinfo) == "Asia/Seoul"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 1, 1))
