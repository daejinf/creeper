"""Time rules: store UTC (timezone-aware), display Asia/Seoul."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime is not allowed; attach a timezone")
    return value.astimezone(timezone.utc)


def to_display(value: datetime) -> datetime:
    return to_utc(value).astimezone(DISPLAY_TZ)
