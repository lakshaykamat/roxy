"""Deterministic relative-date parsing for expense requests.

The LLM normally resolves relative phrases into concrete dates using the current
time supplied in each turn, but these helpers give Roxy a reliable, testable way
to interpret phrases like "yesterday" or "last friday" in the user's timezone.
All results are timezone-local calendar values, formatted for the API.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_WEEKDAYS = {name.casefold(): index for index, name in enumerate(calendar.day_name)}
_WEEKDAY_ABBR = {name.casefold(): index for index, name in enumerate(calendar.day_abbr)}


def _local_today(now: datetime | None, timezone: str) -> date:
    reference = now or datetime.now(ZoneInfo(timezone))
    if reference.tzinfo is not None:
        reference = reference.astimezone(ZoneInfo(timezone))
    return reference.date()


def resolve_relative_date(
    phrase: str, *, now: datetime | None = None, timezone: str = "Asia/Kolkata"
) -> str:
    """Resolve a relative day phrase into ``YYYY-MM-DD``.

    Supports: today/tonight/now, yesterday, tomorrow, "last <weekday>" (the most
    recent past weekday) and "next <weekday>" (the upcoming weekday).
    Raises :class:`ValueError` for anything it does not understand.
    """
    text = phrase.strip().casefold()
    today = _local_today(now, timezone)

    if text in {"today", "tonight", "now"}:
        return today.isoformat()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    for prefix, direction in (("last ", -1), ("next ", 1), ("this ", 0)):
        if text.startswith(prefix):
            weekday = _weekday_index(text[len(prefix):])
            return _nearest_weekday(today, weekday, direction).isoformat()

    weekday = _weekday_index(text)
    if weekday is not None:
        return _nearest_weekday(today, weekday, -1).isoformat()

    raise ValueError(f"Unrecognised relative date: {phrase!r}")


def resolve_month(
    phrase: str, *, now: datetime | None = None, timezone: str = "Asia/Kolkata"
) -> str:
    """Resolve "this month"/"last month"/"next month" into ``YYYY-MM``."""
    text = phrase.strip().casefold()
    today = _local_today(now, timezone)
    if text in {"this month", "current month", "month"}:
        return f"{today.year:04d}-{today.month:02d}"
    if text == "last month":
        year = today.year - (today.month == 1)
        month = 12 if today.month == 1 else today.month - 1
        return f"{year:04d}-{month:02d}"
    if text == "next month":
        year = today.year + (today.month == 12)
        month = 1 if today.month == 12 else today.month + 1
        return f"{year:04d}-{month:02d}"
    raise ValueError(f"Unrecognised month phrase: {phrase!r}")


def _weekday_index(name: str | None) -> int | None:
    if not name:
        return None
    key = name.strip().casefold()
    if key in _WEEKDAYS:
        return _WEEKDAYS[key]
    return _WEEKDAY_ABBR.get(key)


def _nearest_weekday(today: date, weekday: int | None, direction: int) -> date:
    if weekday is None:
        raise ValueError("Expected a weekday name.")
    if direction < 0:
        delta = (today.weekday() - weekday) % 7 or 7
        return today - timedelta(days=delta)
    if direction > 0:
        delta = (weekday - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    # "this <weekday>": the occurrence within the current week (Mon-Sun).
    return today + timedelta(days=weekday - today.weekday())
