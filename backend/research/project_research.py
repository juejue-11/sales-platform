"""Controlled, project-scoped research over public source pages and files.

The research tools intentionally sit beside the collector.  They persist a
source identity and immutable fetched versions, while project/job links and
relation assessments keep a candidate from silently becoming evidence.  The
module does not touch ``records`` and never invokes a model.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..documents.public_documents import DocumentError, discover_attachments, parse_document


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
_VERIFIED_RELATIONS = frozenset({"same_procurement", "related_project", "buyer_background"})
_DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent.parent / "research_sources.json"
_DEFAULT_BUDGETS = {
    "max_calls": 24,
    "max_download_bytes": 12 * 1024 * 1024,
    "max_chars": 160_000,
    "max_results": 20,
    "max_redirects": 4,
}
_EXTENSIONS = (".pdf", ".doc", ".docx", ".xlsx", ".xls", ".zip")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")


class ResearchError(ValueError):
    """A controlled tool failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _iso(value: Any, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    text = value.strip()
    if len(text) > 64 or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?", text
    ):
        raise ValueError(f"{field} must be an ISO date")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    # Date-only and timezone-naive ISO values are valid inputs.  Normalize
    # them before comparing the optional search bounds below.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return text


def _publication_in_range(value: str | None, args: dict[str, Any]) -> bool:
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
        # Date-only bounds include that entire calendar day.
        if len(bound) == 10:
            candidate_value, bound_value = published.date(), datetime.fromisoformat(bound).date()
        else:
            candidate_value, bound_value = published, datetime.fromisoformat(bound.replace("Z", "+00:00"))
            if bound_value.tzinfo is None:
                bound_value = bound_value.replace(tzinfo=timezone.utc)
        if (is_start and candidate_value < bound_value) or (not is_start and candidate_value > bound_value):
            return False
    return True


def _str(value: Any, field: str, *, required: bool = True, max_len: int = 4000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_len:
        raise ValueError(f"{field} is too long")
    return value


def _identifier(value: Any, field: str) -> str:
    value = _str(value, field, max_len=256)
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field} has invalid characters")
    return value


def _int(value: Any, field: str, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < low or value > high:
        raise ValueError(f"{field} is outside the allowed range")
    return value


def validate_research_arguments(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one of the four research tool payloads.

    Unknown fields are rejected deliberately.  The returned dictionary is a
    fresh object and is the only shape passed to adapters or persistence.
    """
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
            "purpose": _str(args.get("purpose"), "purpose"),
            "query": _str(args.get("query"), "query"),
            "limit": _int(args.get("limit"), "limit", default=10, low=1, high=50),
            "offset": _int(args.get("offset"), "offset", default=0, low=0, high=10_000),
        }
        source_ids = args.get("source_ids")
        if source_ids is not None:
            if not isinstance(source_ids, list) or not all(isinstance(item, str) and item.strip() for item in source_ids):
                raise ValueError("source_ids must be a list of strings")
            if len(source_ids) > 20:
                raise ValueError("too many source_ids")
            result["source_ids"] = [_identifier(item, "source_ids") for item in source_ids]
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
            "source_ref": _identifier(args.get("source_ref"), "source_ref"),
            "purpose": _str(args.get("purpose"), "purpose"),
            "page": _int(args.get("page"), "page", default=0, low=0, high=100_000),
            "offset": _int(args.get("offset"), "offset", default=0, low=0, high=240_000),
            "limit": _int(args.get("limit"), "limit", default=4000, low=1, high=4000),
            "refresh": refresh,
        }

    if tool == "assess_source_relation":
        relation = _str(args.get("relation"), "relation", max_len=64)
        if relation not in RESEARCH_RELATIONS:
            raise ValueError("invalid_relation")
        return {
            "source_ref": _identifier(args.get("source_ref"), "source_ref"),
            "source_version": _identifier(args.get("source_version"), "source_version"),
            "relation": relation,
            "quote": _str(args.get("quote"), "quote", max_len=4000),
            "reason": _str(args.get("reason"), "reason", max_len=4000),
        }

    return {
        "source_ref": _identifier(args.get("source_ref"), "source_ref"),
        "source_version": _identifier(args.get("source_version"), "source_version"),
        "query": _str(args.get("query"), "query"),
        "purpose": _str(args.get("purpose"), "purpose"),
        "limit": _int(args.get("limit"), "limit", default=10, low=1, high=20),
    }


def _sources_path(sources_path: str | Path | None = None) -> Path:
    if sources_path is not None:
        return Path(sources_path)
    configured = os.environ.get("SCREENING_RESEARCH_SOURCES", "").strip()
    return Path(configured) if configured else _DEFAULT_SOURCES_PATH


def _config_data(sources_path: str | Path | None = None) -> dict[str, Any]:
    path = _sources_path(sources_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sources": [], "budgets": dict(_DEFAULT_BUDGETS)}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"sources": [], "budgets": dict(_DEFAULT_BUDGETS), "invalid": True}
    if isinstance(data, list):
        data = {"sources": data}
    if not isinstance(data, dict):
        return {"sources": [], "budgets": dict(_DEFAULT_BUDGETS), "invalid": True}
    sources = data.get("sources", data.get("source_configs", []))
    if not isinstance(sources, list):
        sources = []
    budgets = dict(_DEFAULT_BUDGETS)
    if isinstance(data.get("budgets"), dict):
        for key in budgets:
            value = data["budgets"].get(key)
            if isinstance(value, int) and value > 0:
                budgets[key] = value
    return {**data, "sources": [item for item in sources if isinstance(item, dict)], "budgets": budgets}


def configuration_fingerprint(sources_path: str | Path | None = None) -> str:
    """Return a stable digest used by the host when fingerprinting a run."""
    path = _sources_path(sources_path)
    try:
        raw = path.read_bytes()
    except OSError:
        raw = b"<unconfigured>"
    return hashlib.sha256(raw).hexdigest()


def _safe_url(url: str, *, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ResearchError("invalid_source_url")
    try:
        port = parts.port
    except ValueError:
        raise ResearchError("invalid_source_url") from None
    expected = 443 if parts.scheme == "https" else 80
    if port not in (None, expected):
        raise ResearchError("invalid_source_port")
    host = parts.hostname.lower().rstrip(".")
    host_for_url = f"[{host}]" if ":" in host else host
    netloc = host_for_url if port is None else f"{host_for_url}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def _host_allowed(host: str, allowed: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    values = [str(item).lower().strip().rstrip(".") for item in allowed if str(item).strip()]
    if not values:
        return True
    for value in values:
        if value.startswith("*.") and (host == value[2:] or host.endswith("." + value[2:])):
            return True
        if host == value:
            return True
    return False


def _mock_transport(client: httpx.AsyncClient) -> bool:
    transport = getattr(client, "_transport", None)
    return transport is not None and transport.__class__.__name__ == "MockTransport"


async def _validate_network_url(url: str, allowed_hosts: Iterable[str], client: httpx.AsyncClient) -> str:
    normalized = _safe_url(url)
    host = urlsplit(normalized).hostname or ""
    if not _host_allowed(host, allowed_hosts):
        raise ResearchError("attachment_outside_source")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            info = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(item[4][0]) for item in info]
        except OSError:
            # A MockTransport does not perform DNS.  Literal private IPs are
            # still rejected above; real clients must resolve successfully.
            if _mock_transport(client):
                addresses = []
            else:
                raise ResearchError("source_unreachable") from None
    if any(not address.is_global for address in addresses):
        raise ResearchError("non_public_source")
    return normalized


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._skip = 0
        self._link: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._skip += 1
            return
        if tag == "a" and self._skip == 0:
            values = dict(attrs)
            href = values.get("href") or ""
            if href:
                self._link = {"url": href, "name": "", "title": values.get("title") or ""}
                self.links.append(self._link)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"} and self._skip:
            self._skip -= 1
        if tag == "a":
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)
        if self._link is not None and text:
            self._link["name"] = (self._link.get("name") or "") + text


def _page_evidence_text(text: str) -> str:
    """Exclude adjacent-article navigation from an HTML evidence view.

    The links remain discovery candidates. This view also protects reads of
    older saved pages without changing their immutable snapshot or hash.
    """
    navigation = re.search(r"(?m)^[ \t]*(?:上一篇|下一篇|上一条|下一条)[ \t]*[:：]?[ \t]*$", text)
    return text[:navigation.start()].rstrip() if navigation else text


def _html_content(raw: bytes, content_type: str) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    encoding = "utf-8"
    header = raw[:4096]
    match = re.search(br"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", header, re.I)
    if match:
        try:
            encoding = match.group(1).decode("ascii")
        except UnicodeDecodeError:
            pass
    try:
        html = raw.decode(encoding, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")
    parser = _PageParser()
    parser.feed(html)
    for link in parser.links:
        title = link.pop("title", "")
        if title:
            link["name"] = title
    text = _page_evidence_text("\n".join(parser.parts).strip())
    return text, [{"page": 1, "text": text}], parser.links


def _path_get(value: Any, path: Any) -> Any:
    if not path:
        return value
    if isinstance(path, str):
        parts = [part for part in path.strip(".").split(".") if part]
    elif isinstance(path, list):
        parts = path
    else:
        return value
    for part in parts:
        if isinstance(value, dict):
            value = value.get(str(part))
        elif isinstance(value, list) and str(part).isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {str(key): _substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for key, replacement in variables.items():
        result = result.replace("{" + key + "}", str(replacement))
        result = result.replace("$" + key, str(replacement))
    return result


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _source_ref(config_id: str, url: str, explicit: Any = None) -> str:
    if isinstance(explicit, str) and explicit.strip() and len(explicit.strip()) <= 256:
        candidate = explicit.strip()
        if _IDENTIFIER_RE.fullmatch(candidate):
            return candidate
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    prefix = config_id if _IDENTIFIER_RE.fullmatch(config_id or "") else "source"
    return f"research_{prefix}_{digest}"


def _kind(url: str, content_type: str = "") -> str:
    path = urlsplit(url).path.lower()
    return "document" if path.endswith(_EXTENSIONS) or "pdf" in content_type or "word" in content_type else "page"


def _source_shape(source: dict[str, Any], version: dict[str, Any] | None = None,
                  relation: dict[str, Any] | None = None) -> dict[str, Any]:
    version = version or {}
    relation = relation or {}
    pages = _json(version.get("page_texts_json"), [])
    status = version.get("status") or source.get("status") or "candidate"
    if status == "parsed":
        status = "read"
    result: dict[str, Any] = {
        "source_ref": source.get("source_ref"),
        "source_version": version.get("version"),
        "title": version.get("title") or source.get("title") or "",
        "url": version.get("url") or source.get("canonical_url") or "",
        "published_at": version.get("published_at") or source.get("published_at"),
        "fetched_at": version.get("fetched_at"),
        "kind": version.get("kind") or source.get("kind") or "page",
        "status": status,
        "relation": relation.get("relation") or "unconfirmed",
        "relation_status": relation.get("relation_status") or "unconfirmed",
        "pages": len(pages) if pages else None,
        "parent_source_ref": version.get("parent_source_ref") or source.get("parent_source_ref"),
    }
    if version.get("error"):
        result["error"] = version["error"]
    return result


def _project_source_row(db: Any, project_id: int, source_ref: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]] | None:
    row = db.execute(
        """SELECT s.*, ps.latest_version, ps.status AS project_status, ps.relation,
                  ps.relation_status, ps.scope
             FROM research_sources s JOIN research_project_sources ps
               ON ps.source_ref=s.source_ref
            WHERE ps.project_id=? AND s.source_ref=?""",
        (project_id, source_ref),
    ).fetchone()
    if row is None:
        return None
    source = dict(row)
    version = None
    if source.get("latest_version"):
        version = _row(db.execute(
            "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
            (source_ref, source["latest_version"]),
        ).fetchone())
    relation = {"relation": source.get("relation"), "relation_status": source.get("relation_status"),
                "scope": source.get("scope")}
    return source, version, relation


def list_sources(db: Any, project_id: int) -> list[dict[str, Any]]:
    """List only sources authorized for the requested project."""
    rows = db.execute(
        """SELECT s.*, ps.latest_version, ps.status AS project_status, ps.relation,
                  ps.relation_status, ps.scope
             FROM research_sources s JOIN research_project_sources ps
               ON ps.source_ref=s.source_ref
            WHERE ps.project_id=? ORDER BY ps.updated_at DESC, s.source_ref LIMIT 50""",
        (int(project_id),),
    ).fetchall()
    values: list[dict[str, Any]] = []
    for raw in rows:
        source = dict(raw)
        version = None
        if source.get("latest_version"):
            version = _row(db.execute(
                "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                (source["source_ref"], source["latest_version"]),
            ).fetchone())
        relation = {"relation": source.get("relation"), "relation_status": source.get("relation_status"),
                    "scope": source.get("scope")}
        value = _source_shape(source, version, relation)
        if version is None:
            value["status"] = source.get("project_status") or source.get("status") or "candidate"
            value["url"] = source.get("canonical_url") or ""
            value["title"] = source.get("title") or ""
            value["published_at"] = source.get("published_at")
        values.append(value)
    return values


def read_saved_source(db: Any, project_id: int, source_ref: str, version: str,
                      *, page: int | None = None, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
    """Read a bounded fragment from an already persisted source version."""
    if isinstance(page, bool) or (page is not None and (not isinstance(page, int) or page < 1)):
        raise ValueError("invalid_page")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("invalid_offset")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8000:
        raise ValueError("invalid_limit")
    found = _project_source_row(db, int(project_id), str(source_ref))
    if found is None:
        raise LookupError("source_not_in_project")
    source, _latest, relation = found
    version_row = _row(db.execute(
        "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
        (str(source_ref), str(version)),
    ).fetchone())
    if version_row is None:
        raise LookupError("source_version_not_found")
    historical = db.execute(
        """SELECT relation,relation_status,scope FROM research_source_relations
            WHERE project_id=? AND source_ref=? AND source_version=?
            ORDER BY id DESC LIMIT 1""",
        (int(project_id), str(source_ref), str(version)),
    ).fetchone()
    if historical is not None:
        relation = dict(historical)
    pages = _json(version_row.get("page_texts_json"), [])
    if not pages and version_row.get("body_text"):
        pages = [{"page": None, "text": str(version_row.get("body_text") or "")}]
    selected: list[dict[str, Any]] = []
    if page is None:
        selected = pages[:1]
    else:
        selected = [item for item in pages if item.get("page") is not None and int(item.get("page")) == page]
    if not selected:
        raise LookupError("source_page_not_found")
    selected_page = selected[0].get("page")
    full_text = "\n".join(str(item.get("text") or "") for item in selected)
    fragments: list[dict[str, Any]] = []
    for item in selected:
        text = str(item.get("text") or "")
        fragments.append({"page": item.get("page"), "text": text[offset:offset + limit]})
    text = "\n".join(item["text"] for item in fragments)
    source_result = _source_shape(source, version_row, relation)
    source_result.update({"page_texts": fragments,
                          "scope": relation.get("scope") or version_row.get("scope") or "page"})
    result = {
        "source": dict(source_result),
        "text": text,
        "page": selected_page,
        "offset": offset,
        "total_chars": len(full_text),
        "has_more": offset + len(text) < len(full_text),
    }
    return result


def current_dependencies(db: Any, project_id: int, dependencies: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Compare saved research dependencies with current versions/relations.

    Candidates discovered during the dependency's own job are ignored unless
    their assessed relation or version is later changed by another job.
    """
    result: list[dict[str, Any]] = []
    if isinstance(dependencies, dict):
        dependency_values: Iterable[Any] = [dependencies]
    else:
        dependency_values = dependencies or []
    for dependency in dependency_values:
        if not isinstance(dependency, dict):
            continue
        source_ref = str(dependency.get("source_ref") or "")
        source_version = str(dependency.get("source_version") or dependency.get("version") or "")
        if not source_ref or not source_version:
            continue
        found = _project_source_row(db, int(project_id), source_ref)
        if found is None:
            result.append({"source_ref": source_ref, "source_version": source_version,
                           "current_source_version": None, "relation": None,
                           "relation_status": "unconfirmed", "changed": True,
                           "reason": "source_removed_or_unauthorized"})
            continue
        source, latest, relation = found
        current_version = (latest or {}).get("version")
        expected_relation = dependency.get("relation") or dependency.get("verified_relation")
        current_relation = relation.get("relation")
        current_status = relation.get("relation_status")
        changed = bool(current_version and current_version != source_version)
        relation_changed = bool(expected_relation and current_relation != expected_relation)
        ignored_job = dependency.get("job_id")
        if ignored_job is not None:
            try:
                ignored_job = int(ignored_job)
            except (TypeError, ValueError):
                ignored_job = None
        # A candidate created in this same job is not an external change.  A
        # later relation row still counts as a relation revision.
        latest_relation = db.execute(
            "SELECT * FROM research_source_relations WHERE project_id=? AND source_ref=? "
            "ORDER BY id DESC LIMIT 1", (int(project_id), source_ref)
        ).fetchone()
        latest_relation_dict = _row(latest_relation)
        if ignored_job is not None and latest_relation_dict and latest_relation_dict.get("job_id") == ignored_job:
            if current_version == source_version:
                changed = False
            if expected_relation and latest_relation_dict.get("relation") == expected_relation:
                relation_changed = False
        result.append({
            "source_ref": source_ref,
            "source_version": source_version,
            "current_source_version": current_version,
            "relation": current_relation,
            "relation_status": current_status,
            "changed": changed or relation_changed,
            "reason": "source_version_changed" if changed else ("relation_revised" if relation_changed else "unchanged"),
        })
    return result


def merge_project_research(db: Any, source_project_id: int, target_project_id: int) -> None:
    """Move research history when an existing collector merge moves projects.

    Research itself never calls this helper.  The collector's explicit merge
    path may call it before deleting the source project; copying relation rows
    first prevents the project foreign keys from cascading away evidence.
    """
    source_project_id = int(source_project_id)
    target_project_id = int(target_project_id)
    if source_project_id == target_project_id:
        return
    source_links = db.execute(
        "SELECT * FROM research_project_sources WHERE project_id=?",
        (source_project_id,),
    ).fetchall()
    for raw in source_links:
        link = dict(raw)
        target = db.execute(
            "SELECT * FROM research_project_sources WHERE project_id=? AND source_ref=?",
            (target_project_id, link["source_ref"]),
        ).fetchone()
        if target is None:
            db.execute(
                """INSERT INTO research_project_sources
                   (project_id,source_ref,latest_version,status,relation,relation_status,scope,
                    discovered_job_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (target_project_id, link["source_ref"], link["latest_version"], link["status"],
                 link["relation"], link["relation_status"], link["scope"], link["discovered_job_id"],
                 link["created_at"], link["updated_at"]),
            )
        else:
            existing = dict(target)
            verified = {"verified", "rejected"}
            choose_source = (existing.get("latest_version") is None or
                             (link.get("updated_at") or "") > (existing.get("updated_at") or ""))
            choose_relation = (link.get("relation_status") in verified and
                               existing.get("relation_status") not in verified)
            if choose_source or choose_relation:
                db.execute(
                    """UPDATE research_project_sources
                          SET latest_version=CASE WHEN ? THEN ? ELSE latest_version END,
                              status=CASE WHEN ? THEN ? ELSE status END,
                              relation=CASE WHEN ? THEN ? ELSE relation END,
                              relation_status=CASE WHEN ? THEN ? ELSE relation_status END,
                              scope=CASE WHEN ? THEN ? ELSE scope END,
                              updated_at=CASE WHEN ? THEN ? ELSE updated_at END
                        WHERE project_id=? AND source_ref=?""",
                    (choose_source, link["latest_version"], choose_source, link["status"],
                     choose_relation, link["relation"], choose_relation, link["relation_status"],
                     choose_relation, link["scope"], choose_source or choose_relation,
                     link["updated_at"], target_project_id, link["source_ref"]),
                )
    relations = db.execute(
        "SELECT * FROM research_source_relations WHERE project_id=? ORDER BY id",
        (source_project_id,),
    ).fetchall()
    for raw in relations:
        relation = dict(raw)
        db.execute(
            """INSERT INTO research_source_relations
               (project_id,job_id,source_ref,source_version,relation,relation_status,quote,reason,scope,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (target_project_id, relation["job_id"], relation["source_ref"], relation["source_version"],
             relation["relation"], relation["relation_status"], relation["quote"], relation["reason"],
             relation["scope"], relation["created_at"]),
        )
    db.execute("DELETE FROM research_source_relations WHERE project_id=?", (source_project_id,))
    db.execute("DELETE FROM research_project_sources WHERE project_id=?", (source_project_id,))


class ResearchSession:
    """One authorized, bounded research run for one project and job."""

    def __init__(self, db: Any, *, project_id: int, job_id: int, records: list[dict[str, Any]],
                 db_lock: Any = None, sources_path: str | Path | None = None,
                 client: httpx.AsyncClient | None = None):
        self.db = db
        self.project_id = int(project_id)
        self.job_id = int(job_id)
        self.records = [records] if isinstance(records, dict) else list(records or [])
        self.db_lock = db_lock
        self.sources_path = _sources_path(sources_path)
        self.config = _config_data(self.sources_path)
        self.configs = {str(item.get("id") or item.get("source_id") or ""): item
                        for item in self.config.get("sources", [])
                        if str(item.get("id") or item.get("source_id") or "")}
        self.client = client
        self._own_client = client is None
        self._events: list[dict[str, Any]] = []
        self._unresolved: list[str] = []
        self._served: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._used_refs: set[tuple[str, str]] = set()
        self._calls = 0
        self._download_bytes = 0
        self._chars = 0
        self._tool_calls = 0
        self._status = "not_started"
        self._stop_reason: str | None = None
        budgets = dict(_DEFAULT_BUDGETS)
        budgets.update(self.config.get("budgets") or {})
        self.budgets = budgets
        self._authorization_error: str | None = None
        project = self.db.execute("SELECT id FROM projects WHERE id=?", (self.project_id,)).fetchone()
        job = self.db.execute("SELECT project_id FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        if project is None:
            self._authorization_error = "project_not_found"
        elif job is None:
            self._authorization_error = "job_not_found"
        elif int(job["project_id"]) != self.project_id:
            self._authorization_error = "job_project_mismatch"

    async def close(self) -> None:
        if self._own_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    def __enter__(self) -> "ResearchSession":
        return self

    def __exit__(self, *_: Any) -> None:
        # The normal async owner should call close(); there is no synchronous
        # event-loop safe close to perform here.
        return None

    @contextmanager
    def _guard(self):
        if self.db_lock is not None and hasattr(self.db_lock, "__enter__"):
            with self.db_lock:
                yield
        else:
            yield

    def _commit(self) -> None:
        with self._guard():
            self.db.commit()

    def _event(self, tool: str, purpose: str, status: str, sources: list[str] | None = None,
               *, query: str | None = None, message: str | None = None) -> None:
        event: dict[str, Any] = {"at": _now(), "tool": tool, "purpose": purpose, "status": status,
                                 "sources": list(sources or [])}
        if query is not None:
            event["query"] = query
        if message:
            event["message"] = message
        self._events.append(event)

    def _investigation_sources(self) -> list[dict[str, Any]]:
        """Return sources discovered or actually served by this job only."""
        keys: list[tuple[str, str | None]] = []
        seen: set[tuple[str, str | None]] = set()
        # Used versions take priority so an old, still-cited version remains
        # visible even when the same source has since been refreshed.
        for source_ref, source_version in sorted(self._used_refs):
            key = (source_ref, source_version)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        discovered = self.db.execute(
            """SELECT source_ref FROM research_project_sources
                WHERE project_id=? AND discovered_job_id=?
                ORDER BY updated_at DESC, source_ref LIMIT 50""",
            (self.project_id, self.job_id),
        ).fetchall()
        for row in discovered:
            if any(source_ref == str(row["source_ref"]) for source_ref, _ in keys):
                continue
            key = (str(row["source_ref"]), None)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        values: list[dict[str, Any]] = []
        for source_ref, used_version in keys[:50]:
            found = _project_source_row(self.db, self.project_id, source_ref)
            if found is None:
                continue
            source, latest, current_relation = found
            if used_version is not None:
                version = self._source_version_row(source_ref, used_version)
                relation = self._relation(source_ref, used_version)
            else:
                version = latest
                relation = current_relation
            values.append(_source_shape(source, version, relation))
        return values

    def investigation(self, status: str | None = None, stop_reason: str | None = None,
                       unresolved: list[str] | None = None) -> dict[str, Any]:
        if status is not None:
            if status not in {"not_started", "running", "completed", "limited", "failed", "not_performed"}:
                raise ValueError("invalid_investigation_status")
            self._status = status
        if stop_reason is not None:
            self._stop_reason = str(stop_reason)
        if unresolved is not None:
            self._unresolved = [str(item) for item in unresolved]
        return {
            "policy_version": RESEARCH_POLICY_VERSION,
            "status": self._status,
            "stop_reason": self._stop_reason,
            "events": [dict(event) for event in self._events],
            "sources": self._investigation_sources() if self._authorization_error is None else [],
            "unresolved": list(self._unresolved),
        }

    def _budget_check(self, *, calls: int = 1, bytes_count: int = 0, chars: int = 0) -> None:
        if self._calls + calls > int(self.budgets.get("max_calls", _DEFAULT_BUDGETS["max_calls"])):
            raise ResearchError("budget_exhausted")
        if self._download_bytes + bytes_count > int(self.budgets.get("max_download_bytes", _DEFAULT_BUDGETS["max_download_bytes"])):
            raise ResearchError("budget_exhausted")
        if self._chars + chars > int(self.budgets.get("max_chars", _DEFAULT_BUDGETS["max_chars"])):
            raise ResearchError("budget_exhausted")

    async def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
        return self.client

    def _source_config(self, source_id: str) -> dict[str, Any] | None:
        return self.configs.get(source_id)

    def _allowed_hosts(self, cfg: dict[str, Any], endpoint: str | None = None) -> list[str]:
        values: list[str] = []
        for key in ("allowed_hosts", "allowed_domains", "domains", "hosts"):
            item = cfg.get(key)
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, list):
                values.extend(str(value) for value in item)
        if endpoint:
            host = urlsplit(endpoint).hostname
            if host:
                values.append(host)
        base = cfg.get("base_url")
        if isinstance(base, str):
            try:
                host = urlsplit(_safe_url(base)).hostname
                if host:
                    values.append(host)
            except ResearchError:
                pass
        return list(dict.fromkeys(values))

    async def _request(self, method: str, url: str, cfg: dict[str, Any], *, json_body: Any = None,
                       params: dict[str, Any] | None = None) -> tuple[bytes, str, str, int]:
        client = await self._client()
        endpoint = _safe_url(url)
        allowed_hosts = self._allowed_hosts(cfg, endpoint)
        current = endpoint
        redirects = 0
        while True:
            if redirects > int(self.budgets.get("max_redirects", 4)):
                raise ResearchError("redirect_limit")
            current = await _validate_network_url(current, allowed_hosts, client)
            self._budget_check()
            self._calls += 1
            headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
            try:
                async with client.stream(method, current, headers=headers, json=json_body,
                                         params=params if redirects == 0 else None,
                                         follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ResearchError("redirect_without_location")
                        next_url = urljoin(current, location)
                        current = await _validate_network_url(next_url, allowed_hosts, client)
                        if response.status_code == 303 or (response.status_code in {301, 302} and method == "POST"):
                            method, json_body = "GET", None
                        redirects += 1
                        continue
                    declared_size = response.headers.get("content-length", "")
                    if declared_size.isdigit():
                        self._budget_check(calls=0, bytes_count=int(declared_size))
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        self._budget_check(calls=0, bytes_count=len(chunk))
                        self._download_bytes += len(chunk)
                        chunks.append(chunk)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    return b"".join(chunks), content_type, current, response.status_code
            except httpx.HTTPError as exc:
                raise ResearchError("source_unreachable", str(exc)) from exc

    def _upsert_source(self, *, source_ref: str, source_id: str, url: str, title: str,
                       published_at: str | None, kind: str, status: str = "candidate",
                       parent_source_ref: str | None = None, scope: str | None = None,
                       job_id: int | None = None) -> None:
        now = _now()
        source_scope = scope or ("attachment" if parent_source_ref else "page")
        with self._guard():
            self.db.execute(
                """INSERT INTO research_sources
                   (source_ref,source_id,canonical_url,original_url,title,published_at,kind,status,
                    parent_source_ref,config_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_ref) DO UPDATE SET
                     title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE research_sources.title END,
                     published_at=COALESCE(excluded.published_at,research_sources.published_at),
                     kind=CASE WHEN research_sources.status='candidate' THEN excluded.kind ELSE research_sources.kind END,
                     status=CASE WHEN research_sources.status IN ('candidate','failed','unsupported','unreadable','auth_required')
                                 THEN excluded.status ELSE research_sources.status END,
                     updated_at=excluded.updated_at""",
                (source_ref, source_id, url, url, title, published_at, kind, status,
                 parent_source_ref, source_id, now, now),
            )
            self.db.execute(
                """INSERT INTO research_project_sources
                   (project_id,source_ref,status,relation,relation_status,scope,discovered_job_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id,source_ref) DO UPDATE SET
                     status=CASE WHEN research_project_sources.status='candidate' THEN excluded.status
                                 ELSE research_project_sources.status END,
                     discovered_job_id=excluded.discovered_job_id,
                     updated_at=excluded.updated_at""",
                (self.project_id, source_ref, status, "unconfirmed", "unconfirmed",
                source_scope, job_id, now, now),
            )
        self._commit()

    def _set_source_version(self, source_ref: str, version: str, status: str, *, make_current: bool = True) -> None:
        with self._guard():
            old = self.db.execute(
                "SELECT latest_version,relation_status FROM research_project_sources WHERE project_id=? AND source_ref=?",
                (self.project_id, source_ref),
            ).fetchone()
            if not make_current and old is not None and old["latest_version"] is not None:
                # Keep the last readable version as the project snapshot while
                # retaining this failed representation for diagnostics/history.
                self.db.execute(
                    "UPDATE research_sources SET status=?,updated_at=? WHERE source_ref=?",
                    (status, _now(), source_ref),
                )
            else:
                changed_version = old is not None and old["latest_version"] not in (None, version)
                if changed_version:
                    # A relation is a claim about a concrete immutable version;
                    # a new fetch must be assessed before it can be cited.
                    self.db.execute(
                        """UPDATE research_project_sources
                              SET latest_version=?,status=?,relation='unconfirmed',
                                  relation_status='unconfirmed',updated_at=?
                            WHERE project_id=? AND source_ref=?""",
                        (version, status, _now(), self.project_id, source_ref),
                    )
                else:
                    self.db.execute(
                        "UPDATE research_project_sources SET latest_version=?,status=?,updated_at=? WHERE project_id=? AND source_ref=?",
                        (version, status, _now(), self.project_id, source_ref),
                    )
            self.db.execute(
                "UPDATE research_sources SET status=?,updated_at=? WHERE source_ref=?",
                (status, _now(), source_ref),
            )
        self._commit()

    def _relation(self, source_ref: str, source_version: str | None = None) -> dict[str, Any]:
        if source_version:
            historical = self.db.execute(
                """SELECT relation,relation_status,scope FROM research_source_relations
                    WHERE project_id=? AND source_ref=? AND source_version=?
                    ORDER BY id DESC LIMIT 1""",
                (self.project_id, source_ref, source_version),
            ).fetchone()
            if historical is not None:
                return dict(historical)
            current = self.db.execute(
                "SELECT latest_version FROM research_project_sources WHERE project_id=? AND source_ref=?",
                (self.project_id, source_ref),
            ).fetchone()
            if current is None or current["latest_version"] != source_version:
                return {"relation": "unconfirmed", "relation_status": "unconfirmed", "scope": "page"}
        row = self.db.execute(
            "SELECT relation,relation_status,scope FROM research_project_sources WHERE project_id=? AND source_ref=?",
            (self.project_id, source_ref),
        ).fetchone()
        return dict(row) if row else {"relation": "unconfirmed", "relation_status": "unconfirmed", "scope": "page"}

    def _candidate_from_item(self, cfg: dict[str, Any], item: Any, endpoint: str) -> dict[str, Any] | None:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            return None
        raw_url = (item.get("url") or item.get("link") or item.get("href") or item.get("canonical_url")
                   or item.get("linkurl") or item.get("linkUrl"))
        if not isinstance(raw_url, str) or not raw_url.strip():
            return None
        try:
            url = _safe_url(raw_url, base=endpoint)
        except ResearchError:
            return None
        # Candidate links are checked before persistence as well as before any
        # fetch.  This keeps the list safe when a later read follows redirects.
        config_id = str(cfg.get("id") or cfg.get("source_id") or "")
        title = str(item.get("title") or item.get("title2") or item.get("name") or item.get("subject") or "").strip()
        published_at = (item.get("published_at") or item.get("publishedAt") or item.get("date")
                        or item.get("webdate") or item.get("infodate"))
        if published_at is not None:
            published_at = str(published_at).strip() or None
        source_ref = _source_ref(config_id, url, item.get("source_ref") or item.get("id"))
        existing = self.db.execute("SELECT source_id,canonical_url FROM research_sources WHERE source_ref=?",
                                   (source_ref,)).fetchone()
        if existing is not None and (existing["source_id"] != config_id or existing["canonical_url"] != url):
            source_ref = _source_ref(config_id, url)
        return {
            "source_ref": source_ref,
            "source_id": config_id,
            "url": url,
            "title": title,
            "published_at": published_at,
            "kind": _kind(url, str(item.get("mime_type") or item.get("content_type") or "")),
            "summary": str(item.get("summary") or item.get("snippet") or "").strip(),
        }

    async def _query_config(self, cfg: dict[str, Any], args: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
        search = cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg
        if isinstance(cfg.get("request"), dict):
            search = {**cfg, **cfg["request"], **(cfg.get("search") or {})}
        scan = None
        for key in ("list_scan", "list"):
            if isinstance(cfg.get(key), dict):
                scan = cfg[key]
                break
        if scan is not None and not any(search.get(key) for key in ("url", "endpoint", "search_url", "list_url")):
            search = {**search, **scan, "adapter": scan.get("adapter") or "list_scan"}
        adapter = str(search.get("adapter") or search.get("type") or search.get("method") or "get_list").lower()
        endpoint_value = search.get("url") or search.get("endpoint") or search.get("search_url") or search.get("list_url")
        if endpoint_value is None:
            endpoint_value = cfg.get("search_url") or cfg.get("list_url")
        if not isinstance(endpoint_value, str) or not endpoint_value.strip():
            return "no_configuration", [], "source_search_not_configured"
        try:
            endpoint = _safe_url(endpoint_value, base=cfg.get("base_url") if isinstance(cfg.get("base_url"), str) else None)
        except ResearchError as exc:
            return "blocked", [], exc.code
        variables = {"query": args["query"], "purpose": args["purpose"], "limit": args["limit"],
                     "offset": args["offset"], "page_number": args["offset"] // args["limit"] + 1,
                     "published_after": args.get("published_after") or "",
                     "published_before": args.get("published_before") or ""}
        method = "POST" if "post" in adapter or adapter in {"post_json_list", "json"} else "GET"
        body = search.get("body") or search.get("json") or search.get("payload")
        body = _substitute(body, variables) if body is not None else None
        query_field = str(search.get("query_field") or "query")
        if method == "POST":
            if not isinstance(body, dict):
                body = {}
            body.setdefault(query_field, args["query"])
            body.setdefault(str(search.get("limit_field") or "limit"), args["limit"])
            body.setdefault(str(search.get("offset_field") or "offset"), args["offset"])
            if args.get("published_after"):
                body.setdefault(str(search.get("published_after_field") or "published_after"), args["published_after"])
            if args.get("published_before"):
                body.setdefault(str(search.get("published_before_field") or "published_before"), args["published_before"])
        else:
            params = {}
            if isinstance(search.get("params"), dict):
                params.update(_substitute(search["params"], variables))
            params.setdefault(query_field, args["query"])
            params.setdefault(str(search.get("limit_field") or "limit"), args["limit"])
            params.setdefault(str(search.get("offset_field") or "offset"), args["offset"])
            if args.get("published_after"):
                params.setdefault(str(search.get("published_after_field") or "published_after"), args["published_after"])
            if args.get("published_before"):
                params.setdefault(str(search.get("published_before_field") or "published_before"), args["published_before"])
        try:
            raw, content_type, final_url, http_status = await self._request(method, endpoint, cfg,
                                                                               json_body=body if method == "POST" else None,
                                                                               params=params if method == "GET" else None)
        except ResearchError as exc:
            if exc.code == "budget_exhausted":
                self._status = "limited"
                self._stop_reason = exc.code
                return "limited", [], exc.code
            return ("blocked" if exc.code in {"non_public_source", "attachment_outside_source", "invalid_source_url",
                                               "invalid_source_port", "redirect_limit"} else "failed"), [], exc.code
        if http_status in {401, 403}:
            return "blocked", [], "auth_required"
        if http_status != 200:
            return "failed", [], f"http_{http_status}"
        # Some official portals expose a bounded HTML list instead of a JSON
        # endpoint.  Treat only the configured list page as a discovery scan;
        # the page body remains a candidate summary until the item is read.
        looks_html = "html" in content_type or raw.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
        if looks_html:
            _page_text, _pages, links = _html_content(raw, content_type)
            candidates: list[dict[str, Any]] = []
            allowed_hosts = self._allowed_hosts(cfg, endpoint)
            query_lower = args["query"].casefold()
            blocked_candidate = False
            # Navigation anchors precede result rows on official portals.
            # Bound the examined anchors separately from the returned result
            # count so a small limit does not hide every actual result.
            for link in links[:200]:
                candidate = self._candidate_from_item(cfg, {"url": link.get("url"), "title": link.get("name")}, final_url)
                if candidate is None:
                    continue
                haystack = (candidate.get("title") or "").casefold() + " " + candidate["url"].casefold()
                # A list page often contains the query in its navigation,
                # footer, or search form.  That page-wide text cannot make
                # every link a procurement candidate.  Require the bounded
                # link's own title or URL to carry the query.
                if query_lower not in haystack:
                    continue
                try:
                    await _validate_network_url(candidate["url"], allowed_hosts, await self._client())
                except ResearchError:
                    blocked_candidate = True
                    continue
                candidates.append(candidate)
                if len(candidates) >= args["limit"]:
                    break
            if candidates:
                return "success", candidates, None
            return ("blocked", [], "candidate_url_blocked") if blocked_candidate else ("no_results", [], None)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "parse_failed", [], "invalid_json"
        items = _path_get(payload, search.get("items_path") or search.get("result_path"))
        if items is None:
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                for key in ("items", "data", "results", "list", "rows", "records"):
                    if isinstance(payload.get(key), list):
                        items = payload[key]
                        break
        if not isinstance(items, list):
            return "parse_failed", [], "result_list_missing"
        candidates = []
        blocked_candidate = False
        allowed_hosts = self._allowed_hosts(cfg, endpoint)
        for item in items[:args["limit"]]:
            candidate = self._candidate_from_item(cfg, item, final_url)
            if candidate is None:
                continue
            try:
                await _validate_network_url(candidate["url"], allowed_hosts, await self._client())
            except ResearchError:
                blocked_candidate = True
                continue
            if not _publication_in_range(candidate.get("published_at"), args):
                continue
            candidates.append(candidate)
        if not candidates:
            return ("blocked", [], "candidate_url_blocked") if blocked_candidate else ("no_results", [], None)
        return "success", candidates, None

    async def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        args = dict(args)
        args["limit"] = min(args["limit"], int(self.budgets.get("max_results", _DEFAULT_BUDGETS["max_results"])))
        configured = [cfg for cfg in self.config.get("sources", [])
                      if cfg.get("enabled", True) is not False and
                      (not args["source_ids"] or str(cfg.get("id") or cfg.get("source_id")) in args["source_ids"])]
        if not configured:
            self._event("search_project_sources", args["purpose"], "no_configuration", query=args["query"],
                        message="no configured source matched source_ids")
            return {"status": "no_configuration", "sources": [],
                    "coverage": {"configured": 0, "queried": 0, "list_scan": False}}
        sources: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        statuses: list[str] = []
        queried = 0
        queried_configs: list[dict[str, Any]] = []
        max_results = int(self.budgets.get("max_results", _DEFAULT_BUDGETS["max_results"]))
        for cfg in configured:
            if len(sources) >= max_results:
                break
            query_args = dict(args)
            query_args["limit"] = min(args["limit"], max_results - len(sources))
            queried += 1
            queried_configs.append(cfg)
            status, candidates, error = await self._query_config(cfg, query_args)
            statuses.append(status)
            source_ids: list[str] = []
            for candidate in candidates:
                self._upsert_source(source_ref=candidate["source_ref"], source_id=candidate["source_id"],
                                    url=candidate["url"], title=candidate["title"],
                                    published_at=candidate.get("published_at"), kind=candidate["kind"],
                                    status="candidate", job_id=self.job_id)
                source = {key: candidate[key] for key in ("source_ref", "title", "url", "published_at", "kind")}
                source.update({"source_version": None, "fetched_at": None, "status": "candidate",
                               "relation": "unconfirmed", "relation_status": "unconfirmed", "pages": None,
                               "parent_source_ref": None})
                if candidate.get("summary"):
                    # This field is explicitly a search-only hint; it is never
                    # put into a version or evidence registry.
                    source["summary"] = candidate["summary"]
                sources.append(source)
                source_ids.append(candidate["source_ref"])
            if error:
                errors.append({"source_id": str(cfg.get("id") or cfg.get("source_id") or ""),
                               "status": status, "error": error})
        if "limited" in statuses:
            overall = "limited"
        elif sources:
            overall = "success"
        elif "blocked" in statuses:
            overall = "blocked"
        elif "parse_failed" in statuses:
            overall = "parse_failed"
        elif "failed" in statuses:
            overall = "failed"
        elif statuses and all(status == "no_configuration" for status in statuses):
            overall = "no_configuration"
        else:
            overall = "no_results"
        limited_scan = any(
            str((cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("adapter") or "").lower()
            in {"list_scan", "html_list", "bounded_list"}
            or isinstance(cfg.get("list_scan"), dict) or isinstance(cfg.get("list"), dict)
            or ((cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("list_url") is not None
                and not ((cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("url")
                         or (cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("endpoint")))
            for cfg in configured
        )
        origins = sorted({host for record in self.records
                          for url in (record.get("original_url"), record.get("canonical_url"))
                          if isinstance(url, str) and (host := urlsplit(url).hostname)})
        uncovered = [host for host in origins if not any(
            (allowed := self._allowed_hosts(cfg)) and _host_allowed(host, allowed)
            for cfg in queried_configs
        )]
        source_ids = [str(cfg.get("id") or cfg.get("source_id") or "") for cfg in queried_configs]
        names = [str(cfg.get("name") or cfg.get("id") or cfg.get("source_id") or "")
                 for cfg in queried_configs]
        coverage_note = f"本次检索仅限：{'、'.join(names)}。"
        if limited_scan:
            coverage_note += "包含有限列表扫描，不能代表站内完整检索。"
        if uncovered:
            coverage_note += f"未覆盖本项目公告平台：{'、'.join(uncovered)}。"
        if errors:
            coverage_note += "部分来源受限或失败：" + "; ".join(item["error"] for item in errors)
        self._event("search_project_sources", args["purpose"], overall,
                    [item["source_ref"] for item in sources], query=args["query"], message=coverage_note)
        return {"status": overall, "sources": sources,
                "coverage": {"configured": len(configured), "queried": queried,
                              "offset": args["offset"], "limit": args["limit"], "statuses": statuses,
                              "list_scan": limited_scan, "source_ids": source_ids,
                              "uncovered_origins": uncovered},
                "errors": errors}

    def _source_version_row(self, source_ref: str, version: str | None = None) -> dict[str, Any] | None:
        if version is None:
            found = self.db.execute(
                """SELECT v.* FROM research_source_versions v
                   JOIN research_project_sources ps ON ps.source_ref=v.source_ref
                  WHERE ps.project_id=? AND v.source_ref=? ORDER BY v.id DESC LIMIT 1""",
                (self.project_id, source_ref),
            ).fetchone()
        else:
            found = self.db.execute(
                """SELECT v.* FROM research_source_versions v
                   JOIN research_project_sources ps ON ps.source_ref=v.source_ref
                  WHERE ps.project_id=? AND v.source_ref=? AND v.version=?""",
                (self.project_id, source_ref, version),
            ).fetchone()
        return _row(found)

    def _serve_version(self, source: dict[str, Any], version: dict[str, Any], args: dict[str, Any], *,
                       register: bool = True) -> dict[str, Any]:
        pages = _json(version.get("page_texts_json"), [])
        if not pages and version.get("body_text"):
            pages = [{"page": None, "text": str(version.get("body_text") or "")}]
        page = args.get("page") or 0
        if page:
            selected = [item for item in pages if item.get("page") is not None and int(item.get("page")) == int(page)]
        else:
            selected = pages[:1]
        fragments: list[dict[str, Any]] = []
        for item in selected:
            text = str(item.get("text") or "")
            if version.get("kind") == "page":
                text = _page_evidence_text(text)
            fragments.append({"page": item.get("page"),
                              "text": text[args.get("offset", 0):args.get("offset", 0) + args.get("limit", 4000)]})
        text = "\n".join(fragment["text"] for fragment in fragments)
        if register:
            self._budget_check(calls=0, chars=len(text))
            self._chars += len(text)
        relation = self._relation(source["source_ref"], str(version.get("version") or ""))
        result = _source_shape(source, version, relation)
        result.update({"text": text, "page_texts": fragments,
                       "scope": relation.get("scope") or version.get("scope") or "page"})
        if not text and result.get("status") == "read":
            result["status"] = "no_content"
            result["error"] = "empty_fragment"
        if register and version.get("version"):
            key = (str(source["source_ref"]), str(version["version"]))
            self._used_refs.add(key)
            served = self._served.setdefault(key, [])
            for fragment in fragments:
                if fragment.get("text") is not None:
                    served.append({"page": fragment.get("page"), "text": fragment.get("text") or "",
                                   "scope": result.get("scope") or "page"})
        return result

    def _parse_fetched(self, raw: bytes, content_type: str, url: str, *, title: str,
                       parent_source_ref: str | None = None) -> tuple[str, str, list[dict[str, Any]], str, str | None, list[dict[str, str]]]:
        """Return status, kind, pages, body text, error and discovered links."""
        kind = _kind(url, content_type)
        links: list[dict[str, str]] = []
        if kind == "document" or raw.startswith(b"%PDF"):
            document = parse_document(raw, record_id=self.project_id, url=url, name=title, mime_type=content_type)
            status = str(document.get("status") or "unsupported")
            mapped = "read" if status == "parsed" else status
            pages = document.get("page_texts") if isinstance(document.get("page_texts"), list) else []
            text = str(document.get("text") or "")
            return mapped, "document", pages, text, document.get("error"), links
        looks_html = "html" in content_type or raw.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
        if not looks_html:
            return "unsupported", kind, [], "", "unsupported_content_type", links
        text, pages, links = _html_content(raw, content_type)
        # Keep the public-document attachment recognizer in the path so the
        # known onclick-based official portal links are discovered as well.
        try:
            discovered = discover_attachments(raw.decode("utf-8", errors="replace"), url)
        except (DocumentError, UnicodeError):
            discovered = []
        seen_links = {item.get("url") for item in links}
        for item in discovered:
            if item.get("url") not in seen_links:
                links.append({"url": item.get("url") or "", "name": item.get("name") or ""})
                seen_links.add(item.get("url"))
        if len(text) > 240_000:
            return "failed", "page", [], "", "text_limit", links
        if not text:
            return "unreadable", "page", pages, "", "empty_page", links
        return "read", "page", pages, text, None, links

    def _save_version(self, source: dict[str, Any], *, raw: bytes, url: str, content_type: str,
                      title: str, published_at: str | None, parent_source_ref: str | None = None) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest()
        status, kind, pages, text, error, links = self._parse_fetched(raw, content_type, url,
                                                                        title=title,
                                                                        parent_source_ref=parent_source_ref)
        version = digest
        existing = self.db.execute(
            "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
            (source["source_ref"], version),
        ).fetchone()
        if existing is None:
            now = _now()
            with self._guard():
                self.db.execute(
                    """INSERT INTO research_source_versions
                       (source_ref,version,title,url,published_at,fetched_at,kind,status,content_sha256,
                        body_text,page_texts_json,scope,parent_source_ref,error,created_at,created_job_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source["source_ref"], version, title or source.get("title") or "", url,
                     published_at or source.get("published_at"), now, kind, status, digest, text,
                     _json_text(pages), "attachment" if parent_source_ref else "page", parent_source_ref,
                     error, now, self.job_id),
                )
            self._commit()
            version_row = _row(self.db.execute(
                "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                (source["source_ref"], version),
            ).fetchone())
        else:
            version_row = dict(existing)
        self._set_source_version(source["source_ref"], version, status)
        # A page may advertise files.  Each file is a separate candidate and
        # remains scoped as an attachment until independently assessed.
        if links:
            cfg = self._source_config(str(source.get("config_id") or source.get("source_id") or "")) or {}
            allowed_hosts = self._allowed_hosts(cfg, url)
            for link in links[:100]:
                try:
                    child_url = _safe_url(link.get("url") or "", base=url)
                    if child_url in {url, source.get("canonical_url")}:
                        continue
                    name = str(link.get("name") or "").strip()
                    if name in {"首页", "登录", "我要报名", "招标采购", "招标公告", "采购公告", "中标公示", "中标结果"}:
                        continue
                    is_document = _kind(child_url) == "document" or bool(re.search(
                        r"download|attachment|downfile|attach|filedownload", urlsplit(child_url).path, re.I))
                    is_related_notice = len(name) > 6 and bool(re.search(
                        r"公告|公示|澄清|变更|补充通知|技术(?:要求|规格|参数|文件|规范)|"
                        r"交付(?:要求|时间|地点)|(?:物料|采购|设备)清单", name))
                    if not is_document and not is_related_notice:
                        continue
                    child_host = urlsplit(child_url).hostname or ""
                    if not _host_allowed(child_host, allowed_hosts):
                        continue
                    try:
                        if not ipaddress.ip_address(child_host).is_global:
                            continue
                    except ValueError:
                        # Hostnames are resolved immediately before a child
                        # request; only literal private addresses can be
                        # rejected at discovery time.
                        pass
                except ResearchError:
                    continue
                child_ref = _source_ref(str(source.get("config_id") or source.get("source_id") or ""), child_url)
                child_kind = _kind(child_url)
                self._upsert_source(source_ref=child_ref,
                                    source_id=str(source.get("source_id") or source.get("config_id") or ""),
                                    url=child_url, title=link.get("name") or "", published_at=None,
                                    kind=child_kind, status="candidate", parent_source_ref=source["source_ref"],
                                    scope="attachment" if child_kind == "document" else "page",
                                    job_id=self.job_id)
        return version_row or {}

    def _save_failed_version(self, source: dict[str, Any], *, raw: bytes, url: str,
                             status: str, error: str, content_type: str = "") -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest()
        version = digest
        existing = self.db.execute(
            "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
            (source["source_ref"], version),
        ).fetchone()
        if existing is None:
            now = _now()
            with self._guard():
                self.db.execute(
                    """INSERT INTO research_source_versions
                       (source_ref,version,title,url,published_at,fetched_at,kind,status,content_sha256,
                        body_text,page_texts_json,scope,parent_source_ref,error,created_at,created_job_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source["source_ref"], version, source.get("title") or "", url,
                     source.get("published_at"), now, _kind(url, content_type), status, digest,
                     "", "[]", "page", source.get("parent_source_ref"), error, now, self.job_id),
                )
            self._commit()
            existing = self.db.execute(
                "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                (source["source_ref"], version),
            ).fetchone()
        old_link = self.db.execute(
            "SELECT latest_version FROM research_project_sources WHERE project_id=? AND source_ref=?",
            (self.project_id, source["source_ref"]),
        ).fetchone()
        self._set_source_version(source["source_ref"], version, status,
                                 make_current=old_link is None or old_link["latest_version"] is None)
        return dict(existing) if existing is not None else {}

    async def _read(self, args: dict[str, Any]) -> dict[str, Any]:
        found = _project_source_row(self.db, self.project_id, args["source_ref"])
        if found is None:
            self._event("read_research_source", args["purpose"], "blocked",
                        [args["source_ref"]], message="source_not_in_project")
            return {"status": "blocked", "error": "source_not_in_project", "source_ref": args["source_ref"]}
        source, latest, relation = found
        if latest is not None and not args["refresh"]:
            value = self._serve_version(source, latest, args)
            status = value.get("status") or "failed"
            event_status = "success" if status == "read" and value.get("text") else status
            self._event("read_research_source", args["purpose"], event_status,
                        [args["source_ref"]], message="cache_hit")
            return value
        config_id = str(source.get("config_id") or source.get("source_id") or "")
        cfg = self._source_config(config_id) or {"allowed_hosts": [urlsplit(source["canonical_url"]).hostname or ""]}
        try:
            raw, content_type, final_url, http_status = await self._request("GET", source["canonical_url"], cfg)
        except ResearchError as exc:
            if exc.code == "budget_exhausted":
                status = "budget_exhausted"
            else:
                status = "blocked" if exc.code in {"non_public_source", "attachment_outside_source", "invalid_source_url",
                                                   "invalid_source_port", "redirect_limit"} else "failed"
            self._event("read_research_source", args["purpose"], status,
                        [args["source_ref"]], message=exc.code)
            if exc.code == "budget_exhausted":
                self._status = "limited"
                self._stop_reason = "budget_exhausted"
            return {"status": status, "error": exc.code, "source_ref": args["source_ref"]}
        if http_status in {401, 403}:
            version = self._save_failed_version(source, raw=raw, url=final_url,
                                                 status="auth_required", error="auth_required",
                                                 content_type=content_type)
            value = self._serve_version(source, version, args)
            self._event("read_research_source", args["purpose"], "auth_required", [args["source_ref"]])
            return value
        if http_status != 200:
            version = self._save_failed_version(source, raw=raw, url=final_url,
                                                 status="failed", error=f"http_{http_status}",
                                                 content_type=content_type)
            value = self._serve_version(source, version, args)
            self._event("read_research_source", args["purpose"], "failed", [args["source_ref"]],
                        message=f"http_{http_status}")
            return value
        version = self._save_version(source, raw=raw, url=final_url, content_type=content_type,
                                     title=source.get("title") or "", published_at=source.get("published_at"),
                                     parent_source_ref=source.get("parent_source_ref"))
        value = self._serve_version(source, version, args)
        event_status = "success" if value.get("status") == "read" and value.get("text") else value.get("status", "failed")
        self._event("read_research_source", args["purpose"], event_status, [args["source_ref"]],
                    message="refreshed" if args["refresh"] else None)
        return value

    def _served_quote(self, source_ref: str, version: str, quote: str) -> bool:
        if not quote:
            return False
        fragments = self._served.get((source_ref, version), [])
        return any(quote in str(fragment.get("text") or "") for fragment in fragments)

    def _identity_conflict(self, text: str) -> bool:
        """Reject a relation when a known project, lot, or buyer conflicts."""
        def values(keys: tuple[str, ...]) -> set[str]:
            found: set[str] = set()
            for record in self.records:
                for key in keys:
                    value = str(record.get(key) or "").strip()
                    if value:
                        found.add(value.casefold())
            return found

        def mismatch(found_values: list[str], known_values: set[str]) -> bool:
            if not found_values or not known_values:
                return False
            return any(value.casefold().strip() not in known_values for value in found_values)

        project_labels = r"(?:项目|採購|采购|招标|招標|公告|notice)[^\n:：]{0,12}(?:编号|編號|number|No\.?|号)"
        project_found = re.findall(
            project_labels + r"\s*[:：#]?\s*([A-Za-z0-9][A-Za-z0-9_./-]{2,})", text, flags=re.I,
        )
        if mismatch(project_found, values(("notice_number", "project_code", "tender_number", "procurement_number"))):
            return True

        lot_labels = r"(?:标段|標段|包号|包號|包件|lot|section)(?:\s*(?:number|no\.?|号|編號|编号))?"
        lot_found = re.findall(
            lot_labels + r"\s*[:：#-]?\s*([A-Za-z0-9][A-Za-z0-9_./-]{0,64})", text, flags=re.I,
        )
        if mismatch(lot_found, values(("lot_number", "package_number", "tender_section", "section_number", "lot"))):
            return True

        buyer_found = re.findall(
            r"(?:采购人|採購人|招标人|招標人|采购方|採購方|buyer|purchaser|procuring\s+entity)"
            r"\s*[:：#]?\s*([^\r\n,，;；]{2,80})", text, flags=re.I,
        )
        buyer_values = {value.casefold().strip(" \t:：#-") for value in buyer_found}
        known_buyers = values(("buyer", "buyer_name", "purchaser", "procuring_entity", "tenderer"))
        if buyer_values and known_buyers and not any(
            actual == expected or actual in expected or expected in actual
            for actual in buyer_values for expected in known_buyers
        ):
            return True
        return False

    def _same_procurement_identity(self, text: str) -> bool:
        """Require a concrete project identifier before verifying same procurement."""
        folded = text.casefold()
        for record in self.records:
            strong = [record.get(key) for key in
                       ("notice_number", "project_code", "tender_number", "procurement_number")]
            strong = [str(value).strip() for value in strong if str(value or "").strip()]
            if strong:
                if any(value.casefold() in folded for value in strong):
                    return True
                continue
            title = str(record.get("project_name") or record.get("title") or "").strip()
            buyer = next((str(record.get(key) or "").strip()
                          for key in ("buyer", "buyer_name", "purchaser", "procuring_entity")
                          if str(record.get(key) or "").strip()), "")
            # Names alone are deliberately weak.  A title and the purchasing
            # entity together are the minimum fallback when no notice number
            # was supplied by the current record.
            if title and buyer:
                if title.casefold() in folded and buyer.casefold() in folded:
                    return True
        return False

    def _related_project_identity(self, text: str) -> bool:
        """Require a code or a title-plus-buyer pair for related projects."""
        folded = text.casefold()
        for record in self.records:
            strong = [str(record.get(key) or "").strip() for key in
                       ("notice_number", "project_code", "tender_number", "procurement_number")]
            if any(value and value.casefold() in folded for value in strong):
                return True
            title = str(record.get("project_name") or record.get("title") or "").strip()
            buyer = next((str(record.get(key) or "").strip()
                          for key in ("buyer", "buyer_name", "purchaser", "procuring_entity")
                          if str(record.get(key) or "").strip()), "")
            if title and buyer and title.casefold() in folded and buyer.casefold() in folded:
                return True
        return False

    def _known_buyer_in_text(self, text: str) -> bool:
        folded = text.casefold()
        for record in self.records:
            for key in ("buyer", "buyer_name", "purchaser", "procuring_entity", "tenderer"):
                value = str(record.get(key) or "").strip()
                if value and value.casefold() in folded:
                    return True
        return False

    async def _assess(self, args: dict[str, Any]) -> dict[str, Any]:
        found = _project_source_row(self.db, self.project_id, args["source_ref"])
        if found is None:
            self._event("assess_source_relation", args["reason"], "blocked", [args["source_ref"]],
                        message="source_not_in_project")
            return {"status": "blocked", "error": "source_not_in_project", "source_ref": args["source_ref"]}
        source, latest_version_row, relation = found
        version = self._source_version_row(args["source_ref"], args["source_version"])
        if version is None:
            self._event("assess_source_relation", args["reason"], "failed", [args["source_ref"]],
                        message="source_version_not_found")
            return {"status": "failed", "error": "source_version_not_found", "source_ref": args["source_ref"]}
        if not self._served_quote(args["source_ref"], args["source_version"], args["quote"]):
            self._event("assess_source_relation", args["reason"], "rejected", [args["source_ref"]],
                        message="quote_not_in_served_fragment")
            return {"status": "rejected", "error": "quote_not_in_served_fragment",
                    "source_ref": args["source_ref"], "source_version": args["source_version"],
                    "relation": "unconfirmed", "relation_status": "rejected"}
        # Only the submitted quote may establish the relation.  Other served
        # fragments remain available for citation but cannot silently broaden
        # the model's asserted support.
        evidence_text = args["quote"]
        hard_conflict = self._identity_conflict(evidence_text)
        relation_name = args["relation"]
        identity_missing = (
            (relation_name == "same_procurement" and not self._same_procurement_identity(evidence_text))
            or (relation_name == "related_project" and not self._related_project_identity(evidence_text))
            or (relation_name == "buyer_background" and not self._known_buyer_in_text(evidence_text))
        )
        if relation_name in _VERIFIED_RELATIONS and not hard_conflict and not identity_missing:
            relation_status = "verified"
            status = "success"
        elif relation_name in {"unrelated", "conflict"} or hard_conflict:
            relation_status = "rejected"
            status = "rejected"
        else:
            relation_status = "unconfirmed"
            status = "unconfirmed"
        scope = relation.get("scope") or version.get("scope") or "page"
        with self._guard():
            self.db.execute(
                """INSERT INTO research_source_relations
                   (project_id,job_id,source_ref,source_version,relation,relation_status,quote,reason,scope,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (self.project_id, self.job_id, args["source_ref"], args["source_version"], relation_name,
                 relation_status, args["quote"], args["reason"], scope, _now()),
            )
            # A relation is attached to one immutable version.  Do not let a
            # late assessment of an older version make the current project
            # link appear verified for a newer, unassessed fetch.
            current_version = str((latest_version_row or {}).get("version") or "")
            if current_version == args["source_version"]:
                self.db.execute(
                    """UPDATE research_project_sources
                          SET relation=?,relation_status=?,scope=?,updated_at=?
                        WHERE project_id=? AND source_ref=?""",
                    (relation_name, relation_status, scope, _now(), self.project_id, args["source_ref"]),
                )
        self._commit()
        self._event("assess_source_relation", args["reason"], status, [args["source_ref"]],
                    message="identity_conflict" if hard_conflict else ("identity_not_established" if identity_missing else None))
        result = _source_shape(source, version, {"relation": relation_name,
                                                   "relation_status": relation_status, "scope": scope})
        result.update({"status": status, "quote": args["quote"], "reason": args["reason"],
                       "relation": relation_name, "relation_status": relation_status})
        return result

    async def _content(self, args: dict[str, Any]) -> dict[str, Any]:
        found = _project_source_row(self.db, self.project_id, args["source_ref"])
        if found is None:
            self._event("search_research_content", args["purpose"], "blocked", [args["source_ref"]],
                        query=args["query"], message="source_not_in_project")
            return {"status": "blocked", "error": "source_not_in_project", "source_ref": args["source_ref"]}
        source, _latest, relation = found
        version = self._source_version_row(args["source_ref"], args["source_version"])
        if version is None:
            self._event("search_research_content", args["purpose"], "failed", [args["source_ref"]],
                        query=args["query"], message="source_version_not_found")
            return {"status": "failed", "error": "source_version_not_found", "source_ref": args["source_ref"]}
        full_text = str(version.get("body_text") or "")
        if version.get("kind") == "page":
            full_text = _page_evidence_text(full_text)
        query = args["query"].casefold()
        matches: list[dict[str, Any]] = []
        pages = _json(version.get("page_texts_json"), [])
        for item in pages:
            page_text = str(item.get("text") or "")
            if version.get("kind") == "page":
                page_text = _page_evidence_text(page_text)
            haystack = page_text.casefold()
            start = 0
            while len(matches) < args["limit"]:
                index = haystack.find(query, start)
                if index < 0:
                    break
                begin = max(0, index - 280)
                end = min(len(page_text), index + len(query) + 280)
                matches.append({"page": item.get("page"), "text": page_text[begin:end],
                                "quote": page_text[index:index + len(args["query"])], "offset": begin})
                start = index + max(1, len(query))
            if len(matches) >= args["limit"]:
                break
        if not matches and full_text and query in full_text.casefold():
            index = full_text.casefold().find(query)
            matches.append({"page": None, "text": full_text[max(0, index - 280):index + len(args["query"]) + 280],
                            "quote": full_text[index:index + len(args["query"])], "offset": max(0, index - 280)})
        if not matches:
            status = "no_results"
        else:
            status = "success"
            key = (args["source_ref"], args["source_version"])
            chars = sum(len(str(match.get("text") or "")) for match in matches)
            self._budget_check(calls=0, chars=chars)
            self._chars += chars
            served = self._served.setdefault(key, [])
            self._used_refs.add(key)
            for match in matches:
                served.append({"page": match.get("page"), "text": match.get("text") or "",
                               "scope": relation.get("scope") or version.get("scope") or "fragment"})
        self._event("search_research_content", args["purpose"], status, [args["source_ref"]],
                    query=args["query"])
        result = _source_shape(source, version, relation)
        result.update({"status": status, "matches": matches, "text": "\n".join(item["text"] for item in matches),
                       "page_texts": [{"page": item.get("page"), "text": item.get("text") or ""} for item in matches],
                       "scope": relation.get("scope") or version.get("scope") or "fragment"})
        return result

    def evidence_sources(self) -> list[dict[str, Any]]:
        """Return only fragments that this session actually served to its caller."""
        values: list[dict[str, Any]] = []
        for source_ref, source_version in sorted(self._used_refs):
            fragments = self._served.get((source_ref, source_version), [])
            fragments = [fragment for fragment in fragments if str(fragment.get("text") or "")]
            if not fragments:
                continue
            found = _project_source_row(self.db, self.project_id, source_ref)
            version = self._source_version_row(source_ref, source_version)
            if found is None or version is None:
                continue
            source, _latest, _current_relation = found
            # The project link points at the latest version.  Evidence may
            # intentionally refer to an older immutable version, whose
            # relation must be looked up by that exact version.
            relation = self._relation(source_ref, source_version)
            # Deduplicate repeated cache reads while preserving page order.
            unique: list[dict[str, Any]] = []
            seen: set[tuple[Any, str]] = set()
            for fragment in fragments:
                key = (fragment.get("page"), str(fragment.get("text") or ""))
                if key not in seen:
                    seen.add(key)
                    unique.append({"page": fragment.get("page"), "text": fragment.get("text") or ""})
            values.append({
                "source_ref": source_ref,
                "source_version": source_version,
                "title": version.get("title") or source.get("title") or "",
                "url": version.get("url") or source.get("canonical_url") or "",
                "fetched_at": version.get("fetched_at"),
                "published_at": version.get("published_at") or source.get("published_at"),
                "relation": relation.get("relation") or "unconfirmed",
                "relation_status": relation.get("relation_status") or "unconfirmed",
                "page_texts": unique,
                "text": "\n".join(item["text"] for item in unique),
                "scope": relation.get("scope") or version.get("scope") or "page",
                "parent_source_ref": version.get("parent_source_ref") or source.get("parent_source_ref"),
            })
        return values

    def dependencies(self, source_refs: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Return verified source versions that can be attached to a report."""
        if isinstance(source_refs, str):
            requested = {source_refs}
        else:
            requested = {str(item) for item in source_refs} if source_refs is not None else None
        values: list[dict[str, Any]] = []
        for source_ref, source_version in sorted(self._used_refs):
            if requested is not None and source_ref not in requested:
                continue
            found = _project_source_row(self.db, self.project_id, source_ref)
            if found is None:
                continue
            source, _latest, _current_relation = found
            relation = self._relation(source_ref, source_version)
            if relation.get("relation_status") != "verified":
                continue
            # Never report a version that was not actually served by this run.
            if self._source_version_row(source_ref, source_version) is None:
                continue
            values.append({
                "source_ref": source_ref,
                "source_version": source_version,
                "relation": relation.get("relation"),
                "verified": True,
                "relation_status": "verified",
                "scope": relation.get("scope") or "page",
                "job_id": self.job_id,
            })
        return values

    async def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one validated research tool and return a safe result object."""
        if self._authorization_error is not None:
            self._status = "failed"
            self._stop_reason = self._authorization_error
            return {"status": "blocked", "error": self._authorization_error,
                    "policy_version": RESEARCH_POLICY_VERSION}
        try:
            normalized = validate_research_arguments(tool, args)
        except (TypeError, ValueError) as exc:
            self._status = "failed"
            self._event(tool if isinstance(tool, str) else "unknown", "", "invalid_arguments", message=str(exc))
            return {"status": "invalid_arguments", "error": str(exc),
                    "policy_version": RESEARCH_POLICY_VERSION}
        self._tool_calls += 1
        if self._tool_calls > int(self.budgets.get("max_calls", _DEFAULT_BUDGETS["max_calls"])):
            self._status = "limited"
            self._stop_reason = "budget_exhausted"
            self._event(tool, normalized.get("purpose", ""), "budget_exhausted",
                        [normalized.get("source_ref")] if normalized.get("source_ref") else None)
            return {"status": "budget_exhausted", "error": "budget_exhausted",
                    "policy_version": RESEARCH_POLICY_VERSION}
        self._status = "running"
        try:
            if tool == "search_project_sources":
                return await self._search(normalized)
            if tool == "read_research_source":
                return await self._read(normalized)
            if tool == "assess_source_relation":
                return await self._assess(normalized)
            return await self._content(normalized)
        except ResearchError as exc:
            if exc.code == "budget_exhausted":
                self._status = "limited"
                self._stop_reason = exc.code
            else:
                self._status = "failed"
            self._event(tool, normalized.get("purpose", ""), "budget_exhausted" if exc.code == "budget_exhausted" else "failed",
                        [normalized.get("source_ref")] if normalized.get("source_ref") else None,
                        query=normalized.get("query"), message=exc.code)
            return {"status": "budget_exhausted" if exc.code == "budget_exhausted" else "failed",
                    "error": exc.code, "policy_version": RESEARCH_POLICY_VERSION}
        except Exception:
            self._status = "failed"
            self._event(tool, normalized.get("purpose", ""), "failed",
                        [normalized.get("source_ref")] if normalized.get("source_ref") else None,
                        query=normalized.get("query"), message="research_internal_error")
            return {"status": "failed", "error": "research_internal_error",
                    "policy_version": RESEARCH_POLICY_VERSION}


__all__ = [
    "RESEARCH_POLICY_VERSION",
    "RESEARCH_TOOLS",
    "ResearchError",
    "ResearchSession",
    "configuration_fingerprint",
    "current_dependencies",
    "list_sources",
    "merge_project_research",
    "read_saved_source",
    "validate_research_arguments",
]
