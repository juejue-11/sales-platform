from __future__ import annotations

import json

import pytest

from backend.documents.document_access import (
    DEFAULT_DOCUMENT_LIMIT,
    MAX_DOCUMENT_LIMIT,
    DocumentAccessError,
    build_document_view,
    validate_document_access_arguments,
)


def _documents() -> list[dict[str, object]]:
    return [
        {
            "id": "doc-1",
            "record_id": 1,
            "name": "技术要求.pdf",
            "url": "https://example.test/1.pdf",
            "status": "parsed",
            "pages": 2,
            "sha256": "hash-1",
            "text": "第一页采购破碎机。\n\n第二页交付要求。",
            "page_texts": [
                {"page": 1, "text": "第一页采购破碎机。"},
                {"page": 2, "text": "第二页交付要求。"},
            ],
            "items": [{"name": "破碎机", "quote": "第一页采购破碎机。"}],
        },
        {
            "id": "doc-2",
            "record_id": 1,
            "name": "长文件.pdf",
            "url": "https://example.test/2.pdf",
            "status": "parsed",
            "pages": 1,
            "sha256": "hash-2",
            "text": "长文件正文" + "技术要求" * 8_000,
            "page_texts": [{"page": 1, "text": "长文件正文" + "技术要求" * 8_000}],
        },
        {
            "id": "doc-other-record",
            "record_id": 2,
            "name": "其他项目.pdf",
            "url": "https://example.test/other.pdf",
            "status": "parsed",
            "pages": 1,
            "sha256": "hash-other",
            "text": "其他项目正文",
            "page_texts": [{"page": 1, "text": "其他项目正文"}],
        },
    ]


def test_directory_omits_long_text_and_keeps_short_compatibility() -> None:
    result = build_document_view(_documents(), {"record_id": 1})
    assert result["mode"] == "directory"
    values = {item["id"]: item for item in result["documents"]}
    assert values["doc-1"]["text"] == "第一页采购破碎机。\n\n第二页交付要求。"
    assert values["doc-1"]["page_texts"][0]["text"] == "第一页采购破碎机。"
    assert "text" not in values["doc-2"]
    assert "page_texts" not in values["doc-2"]
    assert "doc-other-record" not in values
    assert len(json.dumps(result, ensure_ascii=False)) < 16_000


def test_page_and_query_reads_are_continuous_bounded_fragments() -> None:
    page = build_document_view(
        _documents(), {"record_id": 1, "document_id": "doc-1", "page": 2, "limit": 5}
    )["documents"][0]
    assert page["text"] == "第二页交付"
    assert page["page_texts"] == [{"page": 2, "text": "第二页交付"}]
    assert page["read_window"]["returned_chars"] == 5

    query = build_document_view(
        _documents(), {"record_id": 1, "document_id": "doc-1", "query": "破碎机", "limit": 20}
    )["documents"][0]
    assert query["text"] == "破碎机。"
    assert query["page_texts"] == [{"page": 1, "text": "破碎机。"}]
    assert query["read_window"]["matched"] is True


def test_invalid_bounds_and_cross_document_reads_are_rejected() -> None:
    with pytest.raises(DocumentAccessError) as error:
        validate_document_access_arguments({"limit": MAX_DOCUMENT_LIMIT + 1})
    assert error.value.code == "invalid_request"
    with pytest.raises(DocumentAccessError):
        validate_document_access_arguments({"page": 0})
    with pytest.raises(DocumentAccessError):
        validate_document_access_arguments({"document_id": "doc-1", "query": ""})

    with pytest.raises(DocumentAccessError) as error:
        build_document_view(_documents(), {"record_id": 2, "document_id": "doc-1", "page": 1})
    assert error.value.code == "record_not_in_project"
    with pytest.raises(DocumentAccessError) as error:
        build_document_view(_documents(), {"document_id": "doc-other-record", "page": 1})
    assert error.value.code == "record_not_in_project"
    with pytest.raises(DocumentAccessError):
        build_document_view(_documents(), {"query": "正文", "limit": 100})


def test_offsets_are_capped_and_do_not_cross_page_boundaries() -> None:
    result = build_document_view(
        _documents(), {"record_id": 1, "document_id": "doc-1", "page": 1, "offset": 3, "limit": 50}
    )["documents"][0]
    assert result["text"] == "采购破碎机。"
    assert result["page_texts"] == [{"page": 1, "text": "采购破碎机。"}]
    assert result["read_window"]["source_offset"] == 3

    with pytest.raises(DocumentAccessError):
        validate_document_access_arguments({"limit": DEFAULT_DOCUMENT_LIMIT, "offset": 500_001})


def test_directory_reports_metadata_omissions_when_metadata_budget_is_reached() -> None:
    documents = [
        {
            "id": f"doc-{index}",
            "record_id": 1,
            "name": "名称" * 1_000,
            "url": "https://example.test/" + ("u" * 1_000),
            "status": "failed",
            "error": "错误" * 1_000,
        }
        for index in range(30)
    ]
    result = build_document_view(documents, {"record_id": 1})
    assert len(json.dumps(result, ensure_ascii=False)) < 16_000
    assert result["directory"]["truncated"] is True
    assert result["directory"]["omitted_documents"] > 0
