"""Conservative date parsing for procurement milestone evidence.

The procurement reader receives dates in both model-friendly ISO notation and
in the layout-preserving text collected from notices.  This module keeps the
conversion deliberately small and explicit: it recognises complete dates and
times, uses Asia/Shanghai as the local timezone, and never combines a date
from one end of a range with a time from another.

The public functions intentionally have no dependency on the rest of the
application so they can be used while importing or validating a reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Any


LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


@dataclass(frozen=True)
class _Occurrence:
    """One complete date, optionally paired with an explicitly stated time."""

    start: int
    end: int
    day: date
    clock: time | None
    seconds_specified: bool = False


@dataclass(frozen=True)
class _TimeParts:
    end: int
    hour: int
    minute: int
    second: int
    microsecond: int
    seconds_specified: bool
    tzinfo: timezone | None


# ``\s*`` between the components is intentional.  HTML/PDF collectors often
# put each Chinese date component on its own line (for example ``2026年\n10\n月``).
_CHINESE_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*年\s*"
    r"(?P<month>\d{1,2})\s*月\s*"
    r"(?P<day>\d{1,2})\s*日"
)
_WESTERN_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*(?P<separator>[-/.])\s*"
    r"(?P<month>\d{1,2})\s*(?P=separator)\s*"
    r"(?P<day>\d{1,2})(?!\d)"
)

# A complete source time needs an hour and minute.  Seconds are optional, but
# retaining whether they were present lets timestamp_supported reject a value
# such as 09:00:30 when the quote only says 09:00.
_TIME_RE = re.compile(
    r"""
    (?P<hour>\d{1,2})\s*
    (?:
        (?P<colon>[:：])\s*(?P<minute_colon>\d{1,2})
        (?:\s*[:：]\s*(?P<second_colon>\d{1,2})
           (?:[.,](?P<fraction_colon>\d{1,6}))?
        )?
        \s*(?P<colon_hour_suffix>时)?
      |
        (?P<shi>时)\s*(?P<minute_chinese>\d{1,2})\s*分
        (?:\s*(?P<second_chinese>\d{1,2})\s*秒
           (?:[.,](?P<fraction_chinese>\d{1,6}))?
        )?
    )
    """,
    re.VERBOSE,
)
_TIMEZONE_RE = re.compile(r"(?P<z>Z)|(?P<sign>[+-])(?P<hour>\d{2}):?(?P<minute>\d{2})")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date_from_match(match: re.Match[str]) -> date | None:
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except (TypeError, ValueError):
        return None


def _timezone_at(text: str, index: int, *, allow_space: bool) -> tuple[timezone | None, int]:
    """Read a timezone suffix immediately following a time.

    A quote parser does not skip whitespace before a suffix.  This matters for
    ranges such as ``09:00 - 2026年...``: treating the hyphen and the next four
    digits as a ``-20:26`` timezone would corrupt the endpoint pairing.  The
    standalone normalizer can allow a space because its input is one value,
    never a range.
    """

    cursor = index
    if allow_space:
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
    match = _TIMEZONE_RE.match(text, cursor)
    if not match:
        return None, index
    # ``09:00-2024-10-19`` is a compact range, not a timestamp with the
    # offset ``-20:24``.  A no-colon offset immediately followed by another
    # date separator is therefore rejected as a timezone token.  Offsets such
    # as ``-0400`` remain supported when they end the timestamp or precede
    # ordinary punctuation.
    if match.group("sign") and ":" not in match.group(0):
        after = text[match.end()] if match.end() < len(text) else ""
        if after and after in "-/.年月日":
            return None, index
    if match.group("z"):
        return timezone.utc, match.end()
    try:
        hours = int(match.group("hour"))
        minutes = int(match.group("minute"))
    except (TypeError, ValueError):
        return None, index
    # Python's fixed-offset timezone accepts offsets within a day.  Keeping
    # the normal ISO bounds also prevents ``-2026`` (a range separator) from
    # being read as an offset.
    if hours > 23 or minutes > 59:
        return None, index
    offset = timedelta(hours=hours, minutes=minutes)
    if match.group("sign") == "-":
        offset = -offset
    return timezone(offset), match.end()


def _time_after(text: str, index: int, *, allow_zone_space: bool) -> _TimeParts | None:
    """Parse a time directly after a date token.

    Only layout whitespace, an ISO ``T``, or a label colon is permitted
    between the date and time.  Text such as ``每日`` or ``工作时间`` therefore
    cannot make daily business hours look like the time belonging to a prior
    date.
    """

    cursor = index
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor < len(text) and text[cursor] in "Tt":
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
    elif cursor < len(text) and text[cursor] in ":：":
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1

    match = _TIME_RE.match(text, cursor)
    if not match:
        return None

    # A malformed longer component (for example ``9:000``) must not be
    # accepted as the valid prefix ``9:00``.
    if match.end() < len(text) and text[match.end()].isdigit():
        return None

    hour = int(match.group("hour"))
    minute_text = match.group("minute_colon") or match.group("minute_chinese")
    second_text = match.group("second_colon") or match.group("second_chinese")
    fraction_text = match.group("fraction_colon") or match.group("fraction_chinese")
    minute = int(minute_text)
    second = int(second_text or "0")
    microsecond = int((fraction_text or "").ljust(6, "0")) if fraction_text else 0
    seconds_specified = second_text is not None or fraction_text is not None

    try:
        time(hour, minute, second, microsecond)
    except ValueError:
        return None

    tzinfo, end = _timezone_at(text, match.end(), allow_space=allow_zone_space)
    if end == match.end():
        end = match.end()
    return _TimeParts(
        end=end,
        hour=hour,
        minute=minute,
        second=second,
        microsecond=microsecond,
        seconds_specified=seconds_specified,
        tzinfo=tzinfo,
    )


def _local_parts(day: date, parts: _TimeParts) -> tuple[date, time] | None:
    """Construct local (+08:00) date/time, applying an explicit offset."""

    try:
        source_tz = parts.tzinfo or LOCAL_TZ
        instant = datetime(
            day.year,
            day.month,
            day.day,
            parts.hour,
            parts.minute,
            parts.second,
            parts.microsecond,
            tzinfo=source_tz,
        )
        local = instant.astimezone(LOCAL_TZ)
        return local.date(), local.timetz().replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return None


def _date_matches(text: str) -> list[re.Match[str]]:
    matches = list(_CHINESE_DATE_RE.finditer(text))
    matches.extend(_WESTERN_DATE_RE.finditer(text))
    return sorted(matches, key=lambda match: (match.start(), match.end()))


def _occurrences(text: str, *, allow_zone_space: bool = False) -> list[_Occurrence]:
    """Extract complete date occurrences in source order."""

    result: list[_Occurrence] = []
    consumed_until = -1
    for match in _date_matches(text):
        if match.start() < consumed_until:
            continue
        day = _date_from_match(match)
        if day is None:
            continue
        parsed_time = _time_after(text, match.end(), allow_zone_space=allow_zone_space)
        if parsed_time is None:
            result.append(_Occurrence(match.start(), match.end(), day, None))
            consumed_until = match.end()
            continue
        local = _local_parts(day, parsed_time)
        if local is None:
            # Keep a valid date visible even when an adjacent time is malformed;
            # the malformed time itself cannot support a datetime claim.
            result.append(_Occurrence(match.start(), match.end(), day, None))
            consumed_until = match.end()
            continue
        local_day, local_time = local
        result.append(
            _Occurrence(
                match.start(),
                parsed_time.end,
                local_day,
                local_time,
                parsed_time.seconds_specified,
            )
        )
        consumed_until = parsed_time.end
    return result


def _format_occurrence(occurrence: _Occurrence) -> str:
    if occurrence.clock is None:
        return occurrence.day.isoformat()
    instant = datetime.combine(occurrence.day, occurrence.clock, tzinfo=LOCAL_TZ)
    timespec = "microseconds" if occurrence.clock.microsecond else "seconds"
    return instant.isoformat(timespec=timespec)


def _exact_value(value: Any) -> _Occurrence | None:
    raw = _as_text(value)
    if not raw:
        return None
    occurrences = _occurrences(raw, allow_zone_space=True)
    if len(occurrences) != 1:
        return None
    occurrence = occurrences[0]
    # A public value is a single date or timestamp, not a sentence containing
    # one date.  Only surrounding whitespace is ignored.
    if raw[: occurrence.start].strip() or raw[occurrence.end :].strip():
        return None
    return occurrence


def normalize_timestamp(value: str) -> str | None:
    """Return a canonical ISO date or local (+08:00) timestamp.

    Date-only values stay date-only.  A timestamp without an offset is treated
    as local Asia/Shanghai time.  Explicit offsets are converted to the same
    local timezone so equivalent instants compare consistently.
    """

    occurrence = _exact_value(value)
    return _format_occurrence(occurrence) if occurrence is not None else None


def dates_in_quote(quote: str) -> list[str]:
    """Return explicit complete dates/timestamps from a source quote.

    Date/time pairing is based on adjacency in the quote.  In particular,
    ``2026-10-08 至 2026-10-14 每日 9:00 时至 17:00 时`` yields two date-only
    endpoints; neither daily-hours value is borrowed by an endpoint.
    """

    text = _as_text(quote)
    if not text:
        return []
    # Preserve every occurrence and its source order.  Callers that need a
    # set of endpoints can deduplicate deliberately; silently dropping a
    # repeated explicit date here would lose evidence ordering.
    return [_format_occurrence(occurrence) for occurrence in _occurrences(text)]


def timestamp_supported(value: str, quote: str) -> bool:
    """Whether ``value`` is explicitly supported by a quote.

    A date-only value matches a quoted datetime on that same local date.  A
    datetime value must match one complete date/time occurrence, including
    seconds when the quote stated them.  This avoids cross-pairing the date of
    one range endpoint with the time of another endpoint.
    """

    claimed = _exact_value(value)
    if claimed is None:
        return False
    occurrences = _occurrences(_as_text(quote))
    if claimed.clock is None:
        return any(occurrence.day == claimed.day for occurrence in occurrences)
    for occurrence in occurrences:
        if occurrence.clock is None or occurrence.day != claimed.day:
            continue
        source_clock = occurrence.clock
        claimed_clock = claimed.clock
        if (source_clock.hour, source_clock.minute) != (claimed_clock.hour, claimed_clock.minute):
            continue
        if occurrence.seconds_specified:
            if (source_clock.second, source_clock.microsecond) != (
                claimed_clock.second,
                claimed_clock.microsecond,
            ):
                continue
        elif claimed_clock.second != 0 or claimed_clock.microsecond != 0:
            continue
        return True
    return False


__all__ = ["dates_in_quote", "normalize_timestamp", "timestamp_supported"]
