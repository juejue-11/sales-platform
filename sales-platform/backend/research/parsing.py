"""Pure source and content parsing helpers for research."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from ..documents.public_documents import DocumentError, discover_attachments, parse_document
from .config import IDENTIFIER_RE, json_text, json_value


EXTENSIONS = (".pdf", ".doc", ".docx", ".xlsx", ".xls", ".zip")


class PageParser(HTMLParser):
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


def page_evidence_text(text: str) -> str:
    """Remove adjacent-article navigation from a bounded evidence view."""
    navigation = re.search(
        r"(?m)^[ \t]*(?:\u4e0a\u4e00\u7bc7|\u4e0b\u4e00\u7bc7|\u4e0a\u4e00\u6761|\u4e0b\u4e00\u6761)"
        r"[ \t]*[:\uff1a]?[ \t]*$",
        text,
    )
    if navigation:
        return text[:navigation.start()].rstrip()
    return text


def html_content(raw: bytes, content_type: str) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
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
    parser = PageParser()
    parser.feed(html)
    for link in parser.links:
        title = link.pop("title", "")
        if title:
            link["name"] = title
    text = page_evidence_text("\n".join(parser.parts).strip())
    return text, [{"page": 1, "text": text}], parser.links


def path_get(value: Any, path: Any) -> Any:
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


def substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {str(key): substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for key, replacement in variables.items():
        result = result.replace("{" + key + "}", str(replacement))
        result = result.replace("$" + key, str(replacement))
    return result


def row(value: Any) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


def source_ref(config_id: str, url: str, explicit: Any = None) -> str:
    if isinstance(explicit, str) and explicit.strip() and len(explicit.strip()) <= 256:
        candidate = explicit.strip()
        if IDENTIFIER_RE.fullmatch(candidate):
            return candidate
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    prefix = config_id if IDENTIFIER_RE.fullmatch(config_id or "") else "source"
    return f"research_{prefix}_{digest}"


def kind(url: str, content_type: str = "") -> str:
    path = urlsplit(url).path.lower()
    return "document" if path.endswith(EXTENSIONS) or "pdf" in content_type or "word" in content_type else "page"


def source_shape(source: dict[str, Any], version: dict[str, Any] | None = None,
                 relation: dict[str, Any] | None = None) -> dict[str, Any]:
    version = version or {}
    relation = relation or {}
    pages = json_value(version.get("page_texts_json"), [])
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


def parse_fetched(raw: bytes, content_type: str, url: str, *, title: str,
                  record_id: int, parent_source_ref: str | None = None) -> tuple[
                      str, str, list[dict[str, Any]], str, str | None, list[dict[str, str]]
                  ]:
    """Parse a fetched response while retaining document attachments."""
    parsed_kind = kind(url, content_type)
    links: list[dict[str, str]] = []
    if parsed_kind == "document" or raw.startswith(b"%PDF"):
        document = parse_document(raw, record_id=record_id, url=url, name=title, mime_type=content_type)
        status = str(document.get("status") or "unsupported")
        mapped = "read" if status == "parsed" else status
        pages = document.get("page_texts") if isinstance(document.get("page_texts"), list) else []
        text = str(document.get("text") or "")
        return mapped, "document", pages, text, document.get("error"), links
    looks_html = "html" in content_type or raw.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
    if not looks_html:
        return "unsupported", parsed_kind, [], "", "unsupported_content_type", links
    text, pages, links = html_content(raw, content_type)
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


__all__ = [
    "EXTENSIONS",
    "PageParser",
    "html_content",
    "kind",
    "page_evidence_text",
    "parse_fetched",
    "path_get",
    "row",
    "source_ref",
    "source_shape",
    "substitute",
]
