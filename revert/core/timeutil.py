"""Time helpers. DB timestamps are naive UTC; business dates are IST.

IST has no DST, so a fixed offset is exact and avoids needing tzdata on Windows.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def to_ist(naive_utc: datetime) -> datetime:
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(IST)


def format_ist(naive_utc: datetime | None) -> str:
    if naive_utc is None:
        return "never"
    return to_ist(naive_utc).strftime("%d %b %Y, %I:%M %p IST")
