from __future__ import annotations
from datetime import datetime, timedelta
import pytz


def parse_time_hhmm(s: str) -> tuple[int, int] | None:
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:
        return None
    return None


def today_start_in_tz(tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    return tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(pytz.UTC)


def from_utc(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(pytz.timezone(tz))
