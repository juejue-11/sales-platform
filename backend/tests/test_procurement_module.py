from __future__ import annotations

import copy

from backend.procurement import build_procurement_summary, normalize_document, procurement_fingerprint


def _item(*, quantity: str, source: str = "") -> dict[str, str]:
    return {
        "name": "煤矿井下压裂泵",
        "specification": "A",
        "material": "steel",
        "quantity": quantity,
        "unit": "set",
        "delivery_period": "30 days",
        "delivery_location": "site",
        "quote": f"pump {quantity} set",
        "source_kind": source,
    }


def test_summary_preserves_body_attachment_conflict_and_does_not_mutate_inputs():
    record_rows = [{
        "id": 1,
        "kind": "tender",
        "body_text": "采购煤矿井下压裂泵2套。交货期：合同签订之日起30个日历天。交货地点：甲方指定地点。",
        "source_payload_json": "{}",
    }]
    documents = [{
        "id": "doc-1", "record_id": 1, "name": "list.pdf", "url": "https://example.test/list.pdf",
        "status": "parsed", "pages": 1, "sha256": "hash", "fetched_at": "old",
        "mime_type": "application/pdf", "byte_size": 10, "text": "", "page_texts": [],
        "items": [_item(quantity="1")], "error": "", "is_current": True, "updated_at": "old",
    }]
    before_records = copy.deepcopy(record_rows)
    before_documents = copy.deepcopy(documents)

    result = build_procurement_summary(record_rows, documents)

    assert [item["source_kind"] for item in result["items"]] == ["attachment", "announcement"]
    assert {item["quantity"] for item in result["items"]} == {"1", "2"}
    assert result["status"] == "partial"
    assert result["attachment_note"]
    assert record_rows == before_records
    assert documents == before_documents


def test_procurement_fingerprint_ignores_refresh_timestamps_but_tracks_facts():
    first = {
        "status": "complete",
        "scope_summary": "pump",
        "items": [_item(quantity="1")],
        "delivery_period": "30 days",
        "delivery_location": "site",
        "missing_fields": [],
        "scope_quote": "quote",
        "attachment_status": "available",
        "attachment_note": "",
        "updated_at": "2026-10-10T00:00:00Z",
        "documents": [{
            "id": "doc-1", "record_id": 1, "name": "list.pdf", "url": "https://example.test/list.pdf",
            "status": "parsed", "pages": 1, "sha256": "hash", "mime_type": "application/pdf",
            "byte_size": 10, "error": "", "fetched_at": "2026-10-10T00:00:00Z",
            "updated_at": "2026-10-10T00:00:00Z",
        }],
    }
    second = copy.deepcopy(first)
    second["updated_at"] = "2026-10-11T00:00:00Z"
    second["documents"][0]["fetched_at"] = "2026-10-11T00:00:00Z"
    second["documents"][0]["updated_at"] = "2026-10-11T00:00:00Z"

    assert procurement_fingerprint(first) == procurement_fingerprint(second)
    second["items"][0]["quantity"] = "2"
    assert procurement_fingerprint(first) != procurement_fingerprint(second)


def test_normalize_document_preserves_input_and_reasserts_provenance():
    raw = {
        "id": "invalid document id",
        "record_id": 999,
        "status": "unexpected",
        "name": "list.pdf",
        "url": "https://example.test/list.pdf",
        "pages": "2",
        "items": [{**_item(quantity="1"), "source_document_id": "other", "record_id": 999, "page": "0"}],
        "page_texts": [{"page": "0", "text": "page"}],
    }
    before = copy.deepcopy(raw)

    normalized = normalize_document(raw, 7)

    assert raw == before
    assert normalized is not None
    assert normalized["status"] == "failed"
    assert normalized["id"].startswith("doc-")
    assert normalized["items"][0]["record_id"] == 7
    assert normalized["items"][0]["source_document_id"] == normalized["id"]
    assert normalized["items"][0]["page"] == 1

