"""Configuration, argument validation, and policy for project research.

This module owns the values that define a research run.  It deliberately has
no database or HTTP dependency so callers can validate a tool request and
fingerprint its source configuration without constructing a session.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESEARCH_POLICY_VERSION = "project_research_v1"
RESEARCH_TOOLS = (
    "search_project_sources",
    "read_research_source",
    "assess_source_relation",
    "search_research_content",
)
RESEARCH_TOOL_SET = frozenset(RESEARCH_TOOLS)
RESEARCH_RELATIONS = frozenset({
    "unconfirmed",
    "same_procurement",
    "related_project",
    "buyer_background",
    "unrelated",
    "conflict",
})
VERIFIED_RELATIONS = frozenset({"same_procurement", "related_project", "buyer_background"})
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent.parent / "research_sources.json"
DEFAULT_BUDGETS = {
    "max_calls": 24,
    "max_download_bytes": 12 * 1024 * 1024,
    "max_chars": 160_000,
    "max_results": 20,
    "max_redirects": 4,
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")


class ResearchError(ValueError):
    """A controlled tool failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_value(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _iso(value: Any, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    text = value.strip()
    if len(text) > 64 or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
        text,
    ):
        raise ValueError(f"{field} must be an ISO date")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return text


def publication_in_range(value: str | None, args: dict[str, Any]) -> bool:
    if not value:
        return True
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    for field, is_start in (("published_after", True), ("published_before", False)):
        bound = args.get(field)
        if not bound:
            continue
        if len(bound) == 10:
            candidate_value, bound_value = published.date(), datetime.fromisoformat(bound).date()
        else:
            candidate_value, bound_value = published, datetime.fromisoformat(bound.replace("Z", "+00:00"))
            if bound_value.tzinfo is None:
                bound_value = bound_value.replace(tzinfo=timezone.utc)
        if (is_start and candidate_value < bound_value) or (not is_start and candidate_value > bound_value):
            return False
    return True


def string_value(value: Any, field: str, *, required: bool = True, max_len: int = 4000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_len:
        raise ValueError(f"{field} is too long")
    return value


def identifier(value: Any, field: str) -> str:
    value = string_value(value, field, max_len=256)
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field} has invalid characters")
    return value


def integer_value(value: Any, field: str, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < low or value > high:
        raise ValueError(f"{field} is outside the allowed range")
    return value


def validate_research_arguments(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one of the four research tool payloads."""
    if not isinstance(tool, str) or tool not in RESEARCH_TOOL_SET:
        raise ValueError("unknown_research_tool")
    if not isinstance(args, dict):
        raise ValueError("arguments_must_be_object")
    allowed = {
        "search_project_sources": {"purpose", "query", "source_ids", "limit", "offset",
                                    "published_after", "published_before"},
        "read_research_source": {"source_ref", "purpose", "page", "offset", "limit", "refresh"},
        "assess_source_relation": {"source_ref", "source_version", "relation", "quote", "reason"},
        "search_research_content": {"source_ref", "source_version", "query", "purpose", "limit"},
    }[tool]
    unknown = set(args) - allowed
    if unknown:
        raise ValueError("unknown_argument:" + ",".join(sorted(str(item) for item in unknown)))

    if tool == "search_project_sources":
        result = {
            "purpose": string_value(args.get("purpose"), "purpose"),
            "query": string_value(args.get("query"), "query"),
            "limit": integer_value(args.get("limit"), "limit", default=10, low=1, high=50),
            "offset": integer_value(args.get("offset"), "offset", default=0, low=0, high=10_000),
        }
        source_ids = args.get("source_ids")
        if source_ids is not None:
            if not isinstance(source_ids, list) or not all(isinstance(item, str) and item.strip() for item in source_ids):
                raise ValueError("source_ids must be a list of strings")
            if len(source_ids) > 20:
                raise ValueError("too many source_ids")
            result["source_ids"] = [identifier(item, "source_ids") for item in source_ids]
        else:
            result["source_ids"] = []
        result["published_after"] = _iso(args.get("published_after"), field="published_after")
        result["published_before"] = _iso(args.get("published_before"), field="published_before")
        if result["published_after"] and result["published_before"]:
            start = datetime.fromisoformat(result["published_after"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(result["published_before"].replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if start > end:
                raise ValueError("published_after must not be after published_before")
        return result

    if tool == "read_research_source":
        refresh = args.get("refresh", False)
        if not isinstance(refresh, bool):
            raise ValueError("refresh must be a boolean")
        return {
            "source_ref": identifier(args.get("source_ref"), "source_ref"),
            "purpose": string_value(args.get("purpose"), "purpose"),
            "page": integer_value(args.get("page"), "page", default=0, low=0, high=100_000),
            "offset": integer_value(args.get("offset"), "offset", default=0, low=0, high=240_000),
            "limit": integer_value(args.get("limit"), "limit", default=4000, low=1, high=4000),
            "refresh": refresh,
        }

    if tool == "assess_source_relation":
        relation = string_value(args.get("relation"), "relation", max_len=64)
        if relation not in RESEARCH_RELATIONS:
            raise ValueError("invalid_relation")
        return {
            "source_ref": identifier(args.get("source_ref"), "source_ref"),
            "source_version": identifier(args.get("source_version"), "source_version"),
            "relation": relation,
            "quote": string_value(args.get("quote"), "quote", max_len=4000),
            "reason": string_value(args.get("reason"), "reason", max_len=4000),
        }

    return {
        "source_ref": identifier(args.get("source_ref"), "source_ref"),
        "source_version": identifier(args.get("source_version"), "source_version"),
        "query": string_value(args.get("query"), "query"),
        "purpose": string_value(args.get("purpose"), "purpose"),
        "limit": integer_value(args.get("limit"), "limit", default=10, low=1, high=20),
    }


def sources_path(sources_path: str | Path | None = None) -> Path:
    if sources_path is not None:
        return Path(sources_path)
    configured = os.environ.get("SCREENING_RESEARCH_SOURCES", "").strip()
    return Path(configured) if configured else DEFAULT_SOURCES_PATH


def load_config(sources_path_value: str | Path | None = None) -> dict[str, Any]:
    path = sources_path(sources_path_value)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sources": [], "budgets": dict(DEFAULT_BUDGETS)}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"sources": [], "budgets": dict(DEFAULT_BUDGETS), "invalid": True}
    if isinstance(data, list):
        data = {"sources": data}
    if not isinstance(data, dict):
        return {"sources": [], "budgets": dict(DEFAULT_BUDGETS), "invalid": True}
    sources = data.get("sources", data.get("source_configs", []))
    if not isinstance(sources, list):
        sources = []
    budgets = dict(DEFAULT_BUDGETS)
    if isinstance(data.get("budgets"), dict):
        for key in budgets:
            value = data["budgets"].get(key)
            if isinstance(value, int) and value > 0:
                budgets[key] = value
    return {**data, "sources": [item for item in sources if isinstance(item, dict)], "budgets": budgets}


def configuration_fingerprint(sources_path_value: str | Path | None = None) -> str:
    path = sources_path(sources_path_value)
    try:
        raw = path.read_bytes()
    except OSError:
        raw = b"<unconfigured>"
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "DEFAULT_BUDGETS",
    "DEFAULT_SOURCES_PATH",
    "IDENTIFIER_RE",
    "RESEARCH_POLICY_VERSION",
    "RESEARCH_RELATIONS",
    "RESEARCH_TOOL_SET",
    "RESEARCH_TOOLS",
    "ResearchError",
    "VERIFIED_RELATIONS",
    "configuration_fingerprint",
    "identifier",
    "json_text",
    "json_value",
    "load_config",
    "now",
    "publication_in_range",
    "sources_path",
    "validate_research_arguments",
]
