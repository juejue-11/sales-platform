"""Pure procurement document normalization and metadata helpers.

This module has no database, network, or service dependency. Persistence and
fetch orchestration remain in backend.application.service.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.utils import json_text, json_value, sha256_text

DOCUMENT_STATUSES = {"parsed", "unsupported", "auth_required", "failed", "unreadable"}
PROCUREMENT_FIELDS = ("name", "specification", "material", "quantity", "unit", "delivery_period",
                      "delivery_location", "code", "standard", "note", "source_document_id", "record_id",
                      "page", "quote", "source_kind")

def document_json(value: Any, default: Any) -> Any:
    """Decode a persisted document field without trusting its shape."""
    if isinstance(value, (list, dict)):
        return value
    parsed = json_value(value, default)
    return parsed if isinstance(parsed, type(default)) else default


def safe_document_id(record_id: int, raw: dict[str, Any]) -> str:
    candidate = str(raw.get("id") or "").strip()
    if candidate and len(candidate) <= 160 and re.fullmatch(r"[A-Za-z0-9_.:/-]+", candidate):
        return candidate
    seed = ":".join(str(raw.get(key) or "") for key in ("url", "name", "sha256"))
    return f"doc-{sha256_text(f'{record_id}:{seed}')[:24]}"


def normalize_document(raw: Any, record_id: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    document_id = safe_document_id(record_id, raw)
    status = str(raw.get("status") or "failed").strip()
    if status not in DOCUMENT_STATUSES:
        status = "failed"
    try:
        pages = max(0, int(raw.get("pages") or 0))
    except (TypeError, ValueError):
        pages = 0
    try:
        byte_size = max(0, int(raw.get("byte_size") or 0))
    except (TypeError, ValueError):
        byte_size = 0
    text = str(raw.get("text") or "")
    # The parser itself has a lower cap; this protects the DB and tool
    # response if a future parser returns an unexpectedly large value.
    text = text[:500_000]
    page_texts: list[dict[str, Any]] = []
    for page_value in raw.get("page_texts", []) if isinstance(raw.get("page_texts"), list) else []:
        if not isinstance(page_value, dict):
            continue
        try:
            page = max(1, int(page_value.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        page_texts.append({"page": page, "text": str(page_value.get("text") or "")[:200_000]})
        if len(page_texts) >= 200:
            break
    items: list[dict[str, Any]] = []
    for item_value in raw.get("items", []) if isinstance(raw.get("items"), list) else []:
        if not isinstance(item_value, dict):
            continue
        item = {key: item_value.get(key, "") for key in PROCUREMENT_FIELDS}
        for key in ("name", "specification", "material", "quantity", "unit", "delivery_period",
                    "delivery_location", "code", "standard", "note", "source_document_id", "quote"):
            item[key] = str(item.get(key) or "")[:20_000]
        # Parsed rows are always attachment facts, even when an older
        # parser payload omitted the optional discriminator.
        item["source_kind"] = "attachment"
        # The server document is the authority for provenance; never let
        # a parser payload point an item at an unrelated document id.
        item["source_document_id"] = document_id
        # The fetched document's record is the only trusted record
        # provenance.  A parser row cannot redirect evidence to another
        # record through a user-controlled ``record_id`` field.
        item["record_id"] = int(record_id)
        try:
            item["page"] = max(1, int(item.get("page") or 1))
        except (TypeError, ValueError):
            item["page"] = 1
        items.append(item)
        if len(items) >= 2000:
            break
    return {
        "id": document_id, "record_id": int(record_id),
        "name": str(raw.get("name") or "")[:2000], "url": str(raw.get("url") or "")[:4000],
        "status": status, "pages": pages, "sha256": str(raw.get("sha256") or "")[:128],
        "fetched_at": str(raw.get("fetched_at") or "")[:100],
        "mime_type": str(raw.get("mime_type") or "")[:200], "byte_size": byte_size,
        "text": text, "page_texts": page_texts, "items": items,
        "error": str(raw.get("error") or "")[:240],
    }

def document_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "record_id": int(row["record_id"]), "name": row["name"] or "",
        "url": row["url"] or "", "status": row["status"] if row["status"] in DOCUMENT_STATUSES else "failed",
        "pages": int(row["pages"] or 0), "sha256": row["sha256"] or "", "fetched_at": row["fetched_at"],
        "mime_type": row["mime_type"] or "", "byte_size": int(row["byte_size"] or 0),
        "text": row["text"] or "", "page_texts": document_json(row["page_texts_json"], []),
        "items": document_json(row["items_json"], []), "error": row["error"] or "",
        "is_current": bool(row["is_current"]),
        "updated_at": row["updated_at"],
    }

def document_public_metadata(document: dict[str, Any]) -> dict[str, Any]:
    return {key: document.get(key) for key in (
        "id", "record_id", "name", "url", "status", "pages", "sha256", "fetched_at", "mime_type",
        "byte_size", "error", "updated_at",
    )}


def procurement_fingerprint(procurement: dict[str, Any] | None) -> str:
    """Hash semantic procurement facts while ignoring refresh timestamps."""
    value = procurement if isinstance(procurement, dict) else {}
    # Provenance is intentionally excluded from the existing semantic
    # fingerprint.  A re-fetch can retain the same procurement meaning while
    # changing parser quote/page metadata; that should not stale an otherwise
    # current judgment.  Item values and their existing notes remain tracked,
    # so quantity/specification/conflict changes still invalidate it.
    items = []
    provenance_notes = {
        "来源摘录缺失，无法定位原文片段。",
        "交付字段来源摘录疑似混入目录或其他段落，保留原值待核对。",
    }
    for item in value.get("items", []) if isinstance(value.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        semantic_item = {key: value for key, value in item.items() if key not in {"sources", "status"}}
        if "note" in semantic_item:
            notes = [part for part in str(semantic_item.get("note") or "").split("；")
                     if part and part not in provenance_notes]
            if notes:
                semantic_item["note"] = "；".join(notes)
            else:
                semantic_item.pop("note", None)
        items.append(semantic_item)
    documents = []
    for document in value.get("documents", []) if isinstance(value.get("documents"), list) else []:
        if not isinstance(document, dict):
            continue
        documents.append({key: document.get(key) for key in (
            "id", "record_id", "name", "url", "status", "pages", "sha256", "mime_type", "byte_size", "error",
        )})
    return sha256_text(json_text({
        "status": value.get("status"), "scope_summary": value.get("scope_summary"),
        "items": items, "delivery_period": value.get("delivery_period"),
        "delivery_location": value.get("delivery_location"), "missing_fields": value.get("missing_fields", []),
        "scope_quote": value.get("scope_quote"), "attachment_status": value.get("attachment_status"),
        "attachment_note": value.get("attachment_note"),
        "documents": documents,
    }))


def compact_attachment_note(quote: str, *, auth_required: bool = False) -> str:
    """Keep access evidence readable while grounding it in the body."""
    compact = re.sub(r"\s+", " ", str(quote or "")).strip()
    actions = (("注册", "注册"), ("登录", "登录"), ("登陆", "登录"),
               ("购买", "购买"), ("审核", "审核"))
    found = list(dict.fromkeys(label for token, label in actions if token in compact))
    if found:
        return "公告说明：招标文件需" + "、".join(found) + "后下载，请核对公告中的获取条件。"
    elif auth_required:
        return "附件服务返回需要登录或授权。"
    return "公告说明文件获取受限，请核对公告中的获取条件。"


def attachment_access_state(record_rows: list[Any], documents: list[dict[str, Any]],
                             refresh: Any | None,
                             restriction_quotes: list[str]) -> tuple[str, str]:
    """Classify attachment availability from observed evidence only.

    A missing public attachment is a normal ``none_public`` outcome.  It
    becomes ``restricted`` only when the body or a fetch result contains a
    concrete access constraint; parser/network failures remain ``failed``.
    """

    source_attachment_count = 0
    payload_statuses: list[str] = []
    for row in record_rows:
        payload = json_value(row["source_payload_json"], {})
        attachments = payload.get("attachments", []) if isinstance(payload, dict) else []
        if not isinstance(attachments, list):
            continue
        source_attachment_count += len(attachments)
        for raw in attachments:
            if isinstance(raw, dict):
                payload_statuses.append(str(raw.get("status") or "").strip().lower())

    refresh_status = str(refresh["status"] or "").strip().lower() if refresh is not None else ""
    refresh_message = str(refresh["message"] or "").strip() if refresh is not None else ""
    if refresh_status == "failed":
        return "failed", f"附件刷新失败：{refresh_message or '未提供错误原因'}"

    document_statuses = [str(document.get("status") or "").strip().lower() for document in documents]
    if "parsed" in document_statuses:
        if any(status in {"failed", "unreadable", "unsupported"} for status in document_statuses):
            return "available", "部分附件已解析，另有附件解析失败。"
        return "available", ""
    if "auth_required" in document_statuses or "auth_required" in payload_statuses:
        note = compact_attachment_note(
            restriction_quotes[0] if restriction_quotes else "", auth_required=True,
        )
        return "restricted", note
    if any(status in {"failed", "unreadable", "unsupported"} for status in document_statuses + payload_statuses):
        return "failed", "附件存在实际获取或解析失败。"

    if restriction_quotes:
        return "restricted", compact_attachment_note(restriction_quotes[0])
    if refresh is not None:
        if source_attachment_count:
            return "none_public", "本次刷新未发现可公开解析的附件。"
        return "none_public", "本次刷新未发现公开附件。"
    return "not_checked", ""
