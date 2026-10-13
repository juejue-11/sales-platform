from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


try:
    SHANGHAI = ZoneInfo("Asia/Shanghai")
except Exception:  # Windows bundled runtimes may not ship the IANA tzdata file.
    SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def json_value(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def normalize_timestamp(value: Any, *, end_of_day: bool = False) -> str | None:
    """Return a timezone-bearing ISO timestamp while accepting collector formats."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        # The collector uses both a date and a date separated by spaces/newlines.
        raw = raw.replace("年", "-").replace("月", "-").replace("日", "")
        raw = raw.replace("/", "-").replace("时", ":").replace("分", "")
        raw = re.sub(r"\s+", " ", raw)
        if re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", raw):
            try:
                d = datetime.strptime(raw, "%Y-%m-%d").date()
                return datetime.combine(d, time.max if end_of_day else time.min, SHANGHAI).isoformat(timespec="seconds")
            except ValueError:
                return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            # Common source format: 2026-10-21 9:00, with a one-digit hour.
            match = re.fullmatch(r"(20\d{2}-\d{1,2}-\d{1,2})\s+(\d{1,2}):(\d{2})", raw)
            if not match:
                return None
            try:
                dt = datetime.strptime(f"{match.group(1)} {int(match.group(2)):02d}:{match.group(3)}", "%Y-%m-%d %H:%M")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI).isoformat(timespec="seconds")


def parse_timestamp(value: Any, *, deadline: bool = False) -> datetime | None:
    if value is None or value == "":
        return None
    normalized = normalize_timestamp(value, end_of_day=deadline)
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def collapsed(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _evidence_view(text: str) -> tuple[str, list[int]]:
    """Normalize layout whitespace, retaining an offset to every source character.

    Chinese words and Chinese/number boundaries may be split by HTML extraction.
    Spaces inside numbers or Latin identifiers remain significant.
    """
    chars: list[str] = []
    offsets: list[int] = []

    def cjk(char: str) -> bool:
        return bool(char and ('\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff'))

    for match in re.finditer(r'\s+|\S', text):
        value = match.group()
        if value.isspace():
            before = text[match.start() - 1] if match.start() else ''
            after = text[match.end()] if match.end() < len(text) else ''
            layout_only = (cjk(before) and (cjk(after) or after.isdigit())) or (before.isdigit() and cjk(after))
            if not before or not after or layout_only:
                continue
            value = ' '
        chars.append(value)
        offsets.append(match.start())
    return ''.join(chars), offsets


def source_quote(quote: str, text: str) -> str | None:
    """Resolve a quotation to a contiguous, unchanged substring of its source."""
    if not isinstance(quote, str) or not quote.strip() or not isinstance(text, str):
        return None
    quote = quote.strip()
    if quote in text:
        return quote
    wanted, _ = _evidence_view(quote)
    available, offsets = _evidence_view(text)
    if not wanted:
        return None
    start = available.find(wanted)
    if start < 0:
        return None
    return text[offsets[start]:offsets[start + len(wanted) - 1] + 1]


def quote_in_text(quote: str, text: str) -> bool:
    return source_quote(quote, text) is not None


def source_quote_hint(quote: str, text: str) -> str | None:
    """Suggest original text for a layout mismatch, never accept evidence.

    The correction prompt can offer the original punctuation and line breaks
    back to the model. The eventual quotation still passes ``source_quote``;
    this lookup cannot make a rewritten or stitched quote valid by itself.
    """
    if not isinstance(quote, str) or not isinstance(text, str):
        return None
    wanted = ''.join(match.group() for match in re.finditer(r'[\w./:%+-]', quote))
    matches = list(re.finditer(r'[\w./:%+-]', text))
    available = ''.join(match.group() for match in matches)
    if len(wanted) < 8:
        return None
    start = available.find(wanted)
    if start < 0:
        return None
    result = text[matches[start].start():matches[start + len(wanted) - 1].end()]
    return result if len(result) <= 2000 else None


def safe_join(root: Path, relative: str) -> Path:
    """Resolve a relative archive path and reject traversal outside root."""
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("archive path is outside the archive directory") from exc
    return candidate
