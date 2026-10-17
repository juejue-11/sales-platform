"""Pure procurement summary aggregation with source provenance.

The caller supplies records, normalized documents, and the latest refresh row.
This module performs no SQL, network access, model calls, or source mutation.
Existing summary fields remain available; ``facts`` and item ``sources`` add
the shared evidence view used by detail and list clients.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core.utils import sha256_text
from .facts import (
    FACT_FIELDS,
    FACT_STATUSES,
    _delivery_value_status,
    evidence_source,
    extract_announcement_facts,
    extract_text_evidence,
)
from .model import PROCUREMENT_FIELDS, attachment_access_state, document_public_metadata


_ITEM_IDENTITY_FIELDS = (
    "name", "specification", "material", "quantity", "unit", "code", "standard",
)
_FACT_ORDER = {field: index for index, field in enumerate(FACT_FIELDS)}
_MISSING_QUOTE_NOTE = "来源摘录缺失，无法定位原文片段。"
_DELIVERY_CONFLICT_NOTE = "交付字段存在多个来源值，已保留全部来源并标记冲突。"
_DELIVERY_SCOPE_NOTE = "多个物料的交付值适用范围无法从当前摘录确定，已标记不确定，请核对原文。"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalized_identity_value(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _quantity_identity(value: Any) -> str:
    """Normalize equivalent decimal spellings without changing display text."""

    text = _normalized_identity_value(value)
    if not text:
        return ""
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    if not number.is_finite():
        return text
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _item_identity_value(field: str, value: Any) -> str:
    return _quantity_identity(value) if field == "quantity" else _normalized_identity_value(value)


def _item_identity(item: Mapping[str, Any]) -> tuple[str, ...]:
    """Identity for a material demand, including every technical field."""

    return tuple(_item_identity_value(field, item.get(field)) for field in _ITEM_IDENTITY_FIELDS)


def _item_name_quantity_identity(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _normalized_identity_value(item.get("name")),
        _quantity_identity(item.get("quantity")),
        _normalized_identity_value(item.get("unit")),
    )


def _source_key(source: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(source.get(key, "") for key in ("source_kind", "record_id", "document_id", "page", "quote", "sha256"))


def _merge_sources(target: list[dict[str, Any]], additions: Iterable[Mapping[str, Any]]) -> None:
    by_key = {_source_key(source): source for source in target if isinstance(source, Mapping)}
    for source in additions:
        if not isinstance(source, Mapping):
            continue
        value = dict(source)
        key = _source_key(value)
        if key not in by_key:
            target.append(value)
            by_key[key] = value
    target.sort(key=lambda source: (
        0 if source.get("source_kind") == "announcement" else 1,
        _safe_int(source.get("record_id")),
        str(source.get("document_id") or ""),
        _safe_int(source.get("page")),
        str(source.get("quote") or ""),
        str(source.get("sha256") or ""),
    ))


def _append_note(item: dict[str, Any], note: str) -> None:
    note = _text(note)
    if not note:
        return
    old = _text(item.get("note"))
    if note not in old.split("；"):
        item["note"] = "；".join(part for part in (old, note) if part)


def _item_status(item: Mapping[str, Any]) -> str:
    status = str(item.get("status") or "extracted")
    return status if status in FACT_STATUSES else "extracted"


def _attachment_page_set(document: Mapping[str, Any]) -> set[int]:
    values: set[int] = set()
    page_texts = document.get("page_texts")
    if not isinstance(page_texts, list):
        return values
    for page_value in page_texts:
        if not isinstance(page_value, Mapping):
            continue
        page = _safe_int(page_value.get("page"))
        if page > 0:
            values.add(page)
    return values


def _real_attachment_page(document: Mapping[str, Any], raw_item: Mapping[str, Any], page_set: set[int]) -> int | None:
    """Return a page only when it is backed by real page_texts."""

    page = _safe_int(raw_item.get("page"))
    return page if page > 0 and page in page_set else None


def _normalise_item_fields(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    item = {key: raw_item.get(key, "") for key in PROCUREMENT_FIELDS}
    for key in ("name", "specification", "material", "quantity", "unit", "delivery_period",
                "delivery_location", "code", "standard", "note", "source_document_id", "quote"):
        item[key] = str(item.get(key) or "")
    return item


def _attachment_item(document: Mapping[str, Any], raw_item: Mapping[str, Any], page_set: set[int]) -> dict[str, Any]:
    item = _normalise_item_fields(raw_item)
    document_id = str(document.get("id") or "")
    record_id = _safe_int(document.get("record_id"))
    item["source_kind"] = "attachment"
    item["source_document_id"] = document_id
    item["record_id"] = record_id
    # Keep the old public item shape and its legacy page fallback.
    item["page"] = max(1, _safe_int(raw_item.get("page"), 1))
    quote = str(item.get("quote") or "")
    source = evidence_source(
        source_kind="attachment", record_id=record_id, document_id=document_id,
        page=_real_attachment_page(document, raw_item, page_set), quote=quote,
        sha256=document.get("sha256"),
    )
    item["sources"] = [source]
    item["status"] = "extracted" if quote else "uncertain"
    if not quote:
        _append_note(item, _MISSING_QUOTE_NOTE)
    # Preserve a polluted parser value and surface the uncertainty.  The
    # summary never replaces it with a value from the announcement.
    if any(token in str(item.get(field) or "") for field in ("delivery_period", "delivery_location")
           for token in ("附件目录", "附件说明", "表格逐行摘录", "资格要求", "资质要求", "联系人信息", "采购范围", "招标范围", "目录如下", "货物质量标准", "主要技术性能指标", "技术质量要求", "技术要求", "合同模板", "图纸", "附件3")):
        item["status"] = "uncertain"
        _append_note(item, "交付字段来源摘录疑似混入目录或其他段落，保留原值待核对。")
    return item


def _merge_item_metadata(target: dict[str, Any], candidate: Mapping[str, Any]) -> None:
    """Merge provenance and preserve scalar delivery disagreements."""

    _merge_sources(target["sources"], candidate.get("sources", []))
    if _item_status(candidate) == "uncertain":
        target["status"] = "uncertain"
    for field in ("delivery_period", "delivery_location"):
        old = _text(target.get(field))
        new = _text(candidate.get(field))
        if old and new and old != new:
            values = list(dict.fromkeys(part for part in (old, new) if part))
            target[field] = "；".join(values)
            old_quality = _delivery_value_status(field, old)
            new_quality = _delivery_value_status(field, new)
            polluted = old_quality in {"skip", "uncertain"} or new_quality in {"skip", "uncertain"}
            if polluted:
                target["status"] = "uncertain"
            elif target.get("status") != "uncertain":
                target["status"] = "conflict"
            if polluted:
                _append_note(target, "交付字段来源摘录疑似混入目录或其他段落，保留原值待核对。")
            else:
                _append_note(target, _DELIVERY_CONFLICT_NOTE)
        elif not old and new:
            target[field] = candidate.get(field, "")
    for note in str(candidate.get("note") or "").split("；"):
        _append_note(target, note)


def _body_item(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    item = _normalise_item_fields(raw_item)
    item["source_kind"] = "announcement"
    item["source_document_id"] = ""
    item["page"] = 0
    record_id = _safe_int(raw_item.get("record_id"))
    item["record_id"] = record_id
    quote = str(item.get("quote") or "")
    item["sources"] = [evidence_source(source_kind="announcement", record_id=record_id, quote=quote)]
    item["status"] = "extracted" if quote else "uncertain"
    if not quote:
        _append_note(item, _MISSING_QUOTE_NOTE)
    return item


def _body_attachment_compatible(body: Mapping[str, Any], attachment: Mapping[str, Any]) -> bool:
    # Announcement extraction intentionally has no technical specification or
    # material fields.  Any field explicitly present in the announcement must
    # still agree with the attachment.
    if _item_name_quantity_identity(body) != _item_name_quantity_identity(attachment):
        return False
    for field in ("specification", "material", "code", "standard"):
        body_value = _normalized_identity_value(body.get(field))
        attachment_value = _normalized_identity_value(attachment.get(field))
        if body_value and body_value != attachment_value:
            return False
    return True


def _copy_conflict_note(name: str) -> str:
    return f"正文与附件明细的{name}数量或单位不一致，已保留双方来源。"


def _document_fragments(document: Mapping[str, Any]) -> list[tuple[int | None, str]]:
    page_texts = document.get("page_texts")
    result: list[tuple[int | None, str]] = []
    if isinstance(page_texts, list):
        for page_value in page_texts:
            if not isinstance(page_value, Mapping):
                continue
            text = str(page_value.get("text") or "")
            if not text.strip():
                continue
            page = _safe_int(page_value.get("page"))
            result.append((page if page > 0 else None, text))
    if result:
        return result
    text = str(document.get("text") or "")
    return [(None, text)] if text.strip() else []


def _fact_candidate(
    *, field: str, value: Any, source: Mapping[str, Any], status: str = "extracted", note: str = "",
    scope_key: Any = None,
) -> dict[str, Any] | None:
    if field not in FACT_FIELDS:
        return None
    value_text = _text(value)
    if not value_text:
        return None
    candidate = {"field": field, "value": value_text[:4000],
                 "status": status if status in FACT_STATUSES else "extracted", "source": dict(source)}
    if _text(note):
        candidate["note"] = _text(note)
    if scope_key is not None:
        candidate["_scope_key"] = tuple(scope_key) if isinstance(scope_key, (list, tuple)) else str(scope_key)
    return candidate


def _fact_key(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return str(candidate.get("field") or ""), _normalized_identity_value(candidate.get("value"))


def _merge_fact_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        key = _fact_key(raw)
        if not key[0] or not key[1]:
            continue
        current = grouped.get(key)
        if current is None:
            current = {"field": key[0], "value": str(raw.get("value") or ""),
                       "status": str(raw.get("status") or "extracted"), "sources": []}
            grouped[key] = current
        if raw.get("_scope_key") is not None:
            current.setdefault("_scope_keys", set()).add(raw.get("_scope_key"))
        _merge_sources(current["sources"], [raw.get("source", {})])
        status = str(raw.get("status") or "extracted")
        if status == "conflict" or current["status"] == "conflict":
            current["status"] = "conflict"
        elif status == "uncertain" or current["status"] == "uncertain":
            current["status"] = "uncertain"
        note = _text(raw.get("note"))
        if note:
            old = _text(current.get("note"))
            notes = [part for part in old.split("；") if part]
            if note not in notes:
                notes.append(note)
            current["note"] = "；".join(notes)
    result = list(grouped.values())
    for fact in result:
        fact["id"] = f"fact-{fact['field']}-{sha256_text(str(fact.get('value') or ''))[:16]}"
    return sorted(result, key=lambda fact: (
        _FACT_ORDER.get(str(fact.get("field") or ""), len(_FACT_ORDER)),
        _normalized_identity_value(fact.get("value")),
    ))


def _mark_delivery_conflicts(facts: list[dict[str, Any]]) -> set[tuple[str, str]]:
    values: dict[str, set[str]] = {"delivery_period": set(), "delivery_location": set()}
    for fact in facts:
        field = str(fact.get("field") or "")
        # Missing quotes and polluted parser excerpts remain visible as
        # uncertain evidence, but cannot turn a concrete date/location into a
        # false scalar conflict.
        if field in values and str(fact.get("status") or "") != "uncertain":
            values[field].add(_normalized_identity_value(fact.get("value")))
    conflicts: set[tuple[str, str]] = set()
    for field, field_values in values.items():
        if len(field_values) <= 1:
            continue
        field_facts = [fact for fact in facts
                       if str(fact.get("field") or "") == field
                       and str(fact.get("status") or "") != "uncertain"]
        source_kinds = {
            str(source.get("source_kind") or "")
            for fact in field_facts
            for source in fact.get("sources", [])
            if isinstance(source, Mapping)
        }
        # A single body scalar compared with a single attachment scalar is a
        # true cross-source conflict.  Multiple values from one side may be
        # scoped to different material rows; preserve them but mark scope
        # uncertain instead of asserting a global contradiction.
        source_value_counts = {
            kind: sum(1 for fact in field_facts if any(
                isinstance(source, Mapping) and source.get("source_kind") == kind
                for source in fact.get("sources", [])
            ))
            for kind in source_kinds
        }
        scope_sets = [set(fact.get("_scope_keys", set())) for fact in field_facts]
        same_scoped_demand = any(
            left and right and left.intersection(right)
            for index, left in enumerate(scope_sets)
            for right in scope_sets[index + 1:]
        )
        cross_source_scalar = source_kinds == {"announcement", "attachment"} and all(
            source_value_counts.get(kind, 0) == 1 for kind in source_kinds
        )
        if same_scoped_demand or cross_source_scalar:
            for fact in field_facts:
                fact["status"] = "conflict"
                old = _text(fact.get("note"))
                notes = [part for part in old.split("；") if part]
                if _DELIVERY_CONFLICT_NOTE not in notes:
                    notes.append(_DELIVERY_CONFLICT_NOTE)
                fact["note"] = "；".join(notes)
                conflicts.add((field, _normalized_identity_value(fact.get("value"))))
            continue
        for fact in field_facts:
            if fact.get("status") != "conflict":
                fact["status"] = "uncertain"
            old = _text(fact.get("note"))
            notes = [part for part in old.split("；") if part]
            if _DELIVERY_SCOPE_NOTE not in notes:
                notes.append(_DELIVERY_SCOPE_NOTE)
            fact["note"] = "；".join(notes)
    return conflicts


def _add_attachment_row_facts(candidates: list[dict[str, Any]], item: Mapping[str, Any], document: Mapping[str, Any], page_set: set[int]) -> None:
    """Retain parser-row delivery values when text has no mappable label."""

    document_id = str(document.get("id") or "")
    record_id = _safe_int(document.get("record_id"))
    page = _real_attachment_page(document, item, page_set)
    quote = str(item.get("quote") or "")
    for field in ("delivery_period", "delivery_location"):
        value = _text(item.get(field))
        if not value:
            continue
        # A parser row quote is usable evidence only when it actually
        # contains the field value.  Otherwise retain the old item field but
        # avoid claiming an unrelated material quote proves delivery terms.
        if quote and "".join(value.split()) not in "".join(quote.split()):
            continue
        status = "extracted" if quote else "uncertain"
        note = "" if quote else _MISSING_QUOTE_NOTE
        delivery_status = _delivery_value_status(field, value)
        if delivery_status == "skip":
            continue
        if delivery_status == "uncertain":
            status = "uncertain"
            note = "交付字段来源摘录疑似混入目录或其他段落，保留原值待核对。"
        candidate = _fact_candidate(
            field=field, value=value,
            source=evidence_source(source_kind="attachment", record_id=record_id, document_id=document_id,
                                    page=page, quote=quote, sha256=document.get("sha256")),
            status=status, note=note, scope_key=_item_identity(item),
        )
        if candidate:
            candidates.append(candidate)


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return dict(item)


def build_procurement_summary(
    record_rows: Iterable[Any],
    documents: list[dict[str, Any]],
    refresh: Any | None = None,
) -> dict[str, Any]:
    record_rows = list(record_rows)
    body_facts: list[dict[str, Any]] = []
    restriction_quotes: list[str] = []
    fact_candidates: list[dict[str, Any]] = []
    for row in record_rows:
        record_id = _safe_int(row["id"])
        body = str(row["body_text"] or "")
        fact = extract_announcement_facts({"id": record_id, "kind": row["kind"], "body_text": body})
        body_facts.append(fact)
        quote = str(fact.get("restriction_quote") or "").strip()
        if quote and quote not in restriction_quotes:
            restriction_quotes.append(quote)
        fact_candidates.extend(extract_text_evidence(
            body, record_id=record_id, source_kind="announcement", kind=str(row["kind"] or "tender"),
        ))

    public_documents = [document_public_metadata(document) for document in documents]
    parsed_documents = [document for document in documents if document.get("status") == "parsed"]
    unresolved_documents = [document for document in documents if document.get("status") != "parsed"]
    attachment_items: list[dict[str, Any]] = []
    attachment_by_identity: dict[tuple[str, ...], dict[str, Any]] = {}
    for document in parsed_documents:
        page_set = _attachment_page_set(document)
        for raw_item in document.get("items", []) if isinstance(document.get("items"), list) else []:
            if not isinstance(raw_item, Mapping):
                continue
            candidate = _attachment_item(document, raw_item, page_set)
            identity = _item_identity(candidate)
            existing = attachment_by_identity.get(identity)
            if existing is None:
                attachment_by_identity[identity] = candidate
                attachment_items.append(candidate)
            else:
                _merge_item_metadata(existing, candidate)
            _add_attachment_row_facts(fact_candidates, raw_item, document, page_set)
        for page, text in _document_fragments(document):
            fact_candidates.extend(extract_text_evidence(
                text, record_id=document.get("record_id"), source_kind="attachment",
                document_id=document.get("id"), page=page, sha256=document.get("sha256"),
            ))

    # Merge an equivalent announcement source into one attachment row.  An
    # ambiguous generic row (same name/qty with distinct specifications) stays
    # separate so technical rows cannot collapse.
    items: list[dict[str, Any]] = list(attachment_items)
    conflicts: list[str] = []
    for fact in body_facts:
        for raw_item in fact.get("items", []) if isinstance(fact.get("items"), list) else []:
            if not isinstance(raw_item, Mapping):
                continue
            item = _body_item(raw_item)
            base = _item_name_quantity_identity(item)
            body_identity = _item_identity(item)
            compatible = [candidate for candidate in attachment_items if _body_attachment_compatible(item, candidate)]
            if len(compatible) == 1:
                target = compatible[0]
                _merge_item_metadata(target, item)
                continue
            same_body = [candidate for candidate in items
                         if candidate.get("source_kind") == "announcement"
                         and _item_identity(candidate) == _item_identity(item)]
            if same_body:
                _merge_item_metadata(same_body[0], item)
                continue
            same_name = [candidate for candidate in attachment_items
                         if _normalized_identity_value(candidate.get("name")) == base[0]]
            same_values = [candidate for candidate in same_name if _item_name_quantity_identity(candidate) == base]
            if same_name and not same_values:
                conflict = _copy_conflict_note(base[0])
                conflicts.append(conflict)
                for candidate in same_name:
                    candidate["status"] = "conflict"
                    _append_note(candidate, conflict)
                item["status"] = "conflict"
                _append_note(item, conflict)
            items.append(item)

    facts = _merge_fact_candidates(fact_candidates)
    delivery_conflicts = _mark_delivery_conflicts(facts)
    if delivery_conflicts:
        for item in items:
            for field in ("delivery_period", "delivery_location"):
                value = _normalized_identity_value(item.get(field))
                if value and (field, value) in delivery_conflicts:
                    if item.get("status") != "uncertain":
                        item["status"] = "conflict"
                    _append_note(item, _DELIVERY_CONFLICT_NOTE)

    # Scope keys only support conflict reasoning while facts are assembled;
    # they are implementation metadata and are not part of the public API.
    delivery_uncertainties = False
    for fact in facts:
        fact.pop("_scope_keys", None)
        if (fact.get("field") in {"delivery_period", "delivery_location"}
                and fact.get("status") == "uncertain"):
            delivery_uncertainties = True
    item_uncertainties = any(_item_status(item) == "uncertain" for item in items)

    attachment_status, attachment_note = attachment_access_state(
        record_rows, documents, refresh, restriction_quotes,
    )
    if conflicts:
        conflict_note = "；".join(dict.fromkeys(conflicts))
        attachment_note = f"{attachment_note}；{conflict_note}".strip("；")
    if delivery_conflicts:
        attachment_note = f"{attachment_note}；{_DELIVERY_CONFLICT_NOTE}".strip("；")

    names: list[str] = []
    for item in items:
        name = _text(item.get("name"))
        if name and name not in names:
            names.append(name)
    if names:
        preview_names = names[:8]
        has_announcement = any(item.get("source_kind") == "announcement" for item in items)
        if has_announcement:
            quantified: list[str] = []
            for name in preview_names:
                values = list(dict.fromkeys(
                    f"{_text(item.get('quantity'))}{_text(item.get('unit'))}"
                    for item in items if _text(item.get("name")) == name
                    and _text(item.get("quantity")) and _text(item.get("unit"))
                ))
                quantified.append(f"{name}（{'、'.join(values)}）" if values else name)
            preview = "、".join(quantified)
        else:
            preview = "、".join(preview_names)
        suffix = "等" if len(names) > 8 else ""
        if has_announcement and any(
                item.get("source_kind") == "announcement"
                and not _text(item.get("specification"))
                and not _text(item.get("material"))
                for item in items):
            scope_summary = f"共{len(items)}项物料，包括{preview}{suffix}；详细规格和材质待核对原文或招标文件。"
        else:
            scope_summary = f"共{len(items)}项物料，包括{preview}{suffix}；各项规格、材质和数量见明细。"
    elif items:
        scope_summary = f"共{len(items)}项物料；各项规格、材质和数量见明细。"
    else:
        scope_summary = ""

    body_periods = [_text(fact.get("delivery_period")) for fact in body_facts if _text(fact.get("delivery_period"))]
    body_locations = [_text(fact.get("delivery_location")) for fact in body_facts if _text(fact.get("delivery_location"))]
    fact_periods = [_text(fact.get("value")) for fact in facts if fact.get("field") == "delivery_period"]
    fact_locations = [_text(fact.get("value")) for fact in facts if fact.get("field") == "delivery_location"]
    periods = list(dict.fromkeys(
        [_text(item.get("delivery_period")) for item in attachment_items if _text(item.get("delivery_period"))]
        + body_periods + fact_periods
    ))
    locations = list(dict.fromkeys(
        [_text(item.get("delivery_location")) for item in attachment_items if _text(item.get("delivery_location"))]
        + body_locations + fact_locations
    ))
    missing: list[str] = []
    if not items:
        missing.append("明细")
    else:
        if any(not _text(item.get("quantity")) or not _text(item.get("unit")) for item in items):
            missing.append("数量/单位")
        if any(not _text(item.get("specification")) and not _text(item.get("material")) for item in items):
            missing.append("规格/材质")
    if not periods:
        missing.append("交期")
    if not locations:
        missing.append("交地")
    if unresolved_documents:
        missing.append("附件未解析")
    if attachment_status == "failed" and not unresolved_documents:
        missing.append("附件未解析")
    if attachment_status == "restricted" and not unresolved_documents:
        missing.append("附件受限")
    missing = list(dict.fromkeys(missing))

    attempted = refresh is not None
    has_body_facts = bool((items and any(item.get("source_kind") == "announcement" for item in items))
                          or body_periods or body_locations)
    if has_body_facts:
        status = ("complete" if not missing and not conflicts and not delivery_conflicts
                  and not delivery_uncertainties and not item_uncertainties
                  and attachment_status not in {"failed", "restricted"} else "partial")
    elif not documents:
        status = "unavailable" if attempted else "not_checked"
    elif not parsed_documents:
        status = "unavailable"
    elif unresolved_documents or missing or delivery_conflicts or delivery_uncertainties or item_uncertainties:
        status = "partial"
    else:
        status = "complete"
    updated_values = [_text(document.get("updated_at")) for document in documents if _text(document.get("updated_at"))]
    if refresh and refresh["attempted_at"]:
        updated_values.append(str(refresh["attempted_at"]))
    updated_at = max(updated_values) if updated_values else None
    scope_quotes = [str(fact.get("scope_quote") or "").strip() for fact in body_facts if _text(fact.get("scope_quote"))]

    return {
        "status": status,
        "scope_summary": scope_summary,
        "items": [_public_item(item) for item in items],
        "delivery_period": "；".join(periods)[:4000],
        "delivery_location": "；".join(locations)[:4000],
        "missing_fields": missing,
        "documents": public_documents,
        "updated_at": updated_at,
        "attachment_status": attachment_status,
        "attachment_note": attachment_note,
        "scope_quote": scope_quotes[0] if scope_quotes else "",
        "facts": facts,
    }


__all__ = ["build_procurement_summary"]
