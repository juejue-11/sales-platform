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


def quote_in_text(quote: str, text: str) -> bool:
    """Evidence validation with a small tolerance for collector whitespace."""
    if not isinstance(quote, str) or not quote.strip() or not isinstance(text, str):
        return False
    if quote in text:
        return True
    return collapsed(quote) in collapsed(text)


def safe_join(root: Path, relative: str) -> Path:
    """Resolve a relative archive path and reject traversal outside root."""
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("archive path is outside the archive directory") from exc
    return candidate
