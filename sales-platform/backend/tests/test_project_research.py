from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Sequence

import httpx
import pytest

from backend.storage.db import connect
from backend.research.project_research import (
    RESEARCH_POLICY_VERSION,
    ResearchSession,
    current_dependencies,
    list_sources,
    merge_project_research,
    read_saved_source,
    validate_research_arguments,
)


def _setup(tmp_path: Path, *, payload: dict, handler):
    db = connect(tmp_path / "research.sqlite3")
    db.execute("INSERT INTO projects(id,created_at,updated_at) VALUES (1,'x','x')")
    db.execute("INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES (1,1,'running','x','x')")
    db.commit()
    source_path = tmp_path / "sources.json"
    source_path.write_text(json.dumps(payload), encoding="utf-8")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    session = ResearchSession(db, project_id=1, job_id=1,
                              records=[{"notice_number": "P-100"}],
                              sources_path=source_path, client=client)
    return db, session, client


def _small_pdf(pages: Sequence[str]) -> bytes:
    """Build a tiny standards-compliant PDF without a test-only generator."""
    page_count = len(pages)
    font_object = 3 + 2 * page_count
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{ ' '.join(f'{3 + 2 * index} 0 R' for index in range(page_count)) }] "
        f"/Count {page_count} >>".encode(),
    ]
    for index, value in enumerate(pages):
        page_object = 3 + 2 * index
        content_object = page_object + 1
        escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_object} 0 R >> >> "
            f"/Contents {content_object} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def _small_docx(text: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_validate_research_arguments_strict_bounds():
    base = {"purpose": "find", "query": "term"}
    assert validate_research_arguments("search_project_sources", base)["limit"] == 10
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {**base, "limit": True})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {**base, "unknown": 1})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {**base, "published_after": "2026-10-13",
                                                                "published_before": "2026-10-12"})
    with pytest.raises(ValueError):
        validate_research_arguments("read_research_source", {
            "source_ref": "bad id", "purpose": "read", "offset": 0, "limit": 4000,
        })
    with pytest.raises(ValueError):
        validate_research_arguments("read_research_source", {
            "source_ref": "source", "purpose": "read", "refresh": 1,
        })


def test_validate_research_arguments_rejects_types_dates_and_upper_bounds():
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {"purpose": True, "query": "term"})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {"purpose": "find", "query": "term",
                                                                "published_after": "not-a-date"})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {"purpose": "find", "query": "term", "limit": 51})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {"purpose": "find", "query": "term", "offset": 10_001})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {"purpose": "find", "query": "term",
                                                                "source_ids": ["source"] * 21})
    with pytest.raises(ValueError):
        validate_research_arguments("search_project_sources", {
            "purpose": "p" * 4001, "query": "term",
        })

    with pytest.raises(ValueError):
        validate_research_arguments("read_research_source", {
            "source_ref": "source", "purpose": "read", "page": True,
        })
    with pytest.raises(ValueError):
        validate_research_arguments("read_research_source", {
            "source_ref": "source", "purpose": "read", "page": 100_001,
        })
    with pytest.raises(ValueError):
        validate_research_arguments("read_research_source", {
            "source_ref": "source", "purpose": "read", "offset": 240_001,
        })
    with pytest.raises(ValueError):
        validate_research_arguments("read_research_source", {
            "source_ref": "source", "purpose": "read", "limit": 4001,
        })

    with pytest.raises(ValueError):
        validate_research_arguments("assess_source_relation", {
            "source_ref": "source", "source_version": "version", "relation": "unrelated",
            "quote": "q" * 4001,
        })
    with pytest.raises(ValueError):
        validate_research_arguments("search_research_content", {
            "source_ref": "source", "source_version": "version", "query": "q", "purpose": "search",
            "limit": 21,
        })

    payloads = {
        "search_project_sources": {"purpose": "find", "query": "term"},
        "read_research_source": {"source_ref": "source", "purpose": "read"},
        "assess_source_relation": {"source_ref": "source", "source_version": "version",
                                    "relation": "unrelated", "quote": "q"},
        "search_research_content": {"source_ref": "source", "source_version": "version",
                                     "query": "q", "purpose": "search"},
    }
    for tool, payload in payloads.items():
        with pytest.raises(ValueError):
            validate_research_arguments(tool, {**payload, "unexpected": "field"})


def test_publication_dates_include_full_day_and_compare_timezones(tmp_path: Path):
    def handler(request):
        return httpx.Response(200, json={"items": [
            {"id": "day", "url": "/day", "published_at": "2026-10-12 23:59:59"},
            {"id": "next", "url": "/next", "published_at": "2026-10-13 00:00:00"},
            {"id": "offset", "url": "/offset", "published_at": "2026-10-12T12:00:00+08:00"},
        ]})

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        found = await session.execute("search_project_sources", {"purpose": "find", "query": "term",
                                       "published_after": "2026-10-12", "published_before": "2026-10-12"})
        assert {source["source_ref"] for source in found["sources"]} == {"day", "offset"}
        found = await session.execute("search_project_sources", {"purpose": "find", "query": "term",
                                       "published_after": "2026-10-12T05:00:00Z"})
        assert "offset" not in {source["source_ref"] for source in found["sources"]}
        await client.aclose()

    asyncio.run(run())


def test_discovery_ids_do_not_alias_other_source_or_url(tmp_path: Path):
    db, session, client = _setup(tmp_path, payload={"sources": [
        {"id": source_id, "base_url": "https://official.test", "allowed_hosts": ["official.test"],
         "search": {"adapter": "post_json_list", "url": f"https://official.test/{source_id}", "items_path": "items"}}
        for source_id in ("first", "second")
    ]}, handler=lambda request: httpx.Response(200, json={"items": [
        {"id": "shared-external-id", "title": request.url.path, "url": request.url.path + "/notice"}
    ]}))

    async def run():
        result = await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        sources = result["sources"]
        assert len(sources) == 2 and sources[0]["source_ref"] != sources[1]["source_ref"]
        for source in sources:
            stored = db.execute("SELECT canonical_url FROM research_sources WHERE source_ref=?", (source["source_ref"],)).fetchone()
            assert stored["canonical_url"] == source["url"]
        await client.aclose()

    asyncio.run(run())


def test_read_sources_are_unique_and_skip_page_navigation(tmp_path: Path):
    def handler(request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "notice", "url": "/notice", "title": "P-100"}]})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=(
            "<html><body>采购编号：P-100<a href='/notice'>首页</a><a href='/login'>我要报名</a>"
            "<a href='/list.pdf'>技术清单附件</a></body></html>".encode("utf-8")))

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        await session.execute("read_research_source", {"purpose": "read", "source_ref": "notice"})
        sources = session.investigation()["sources"]
        assert len(sources) == 2
        assert len({(source["source_ref"], source["source_version"]) for source in sources}) == 2
        assert {source["title"] for source in sources} == {"P-100", "技术清单附件"}
        await client.aclose()

    asyncio.run(run())


def test_search_read_relation_and_exact_evidence(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "notice", "url": "/notice", "title": "same name"}]})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content="<html><body>采购编号：P-100；交付周期三日。<a href='/list.pdf'>附件</a></body></html>".encode())

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        found = await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        assert found["status"] == "success"
        # Search metadata is a candidate and has no citable version.
        assert found["sources"][0]["source_version"] is None
        read = await session.execute("read_research_source", {
            "source_ref": "notice", "purpose": "read", "limit": 4000,
        })
        assert read["status"] == "read"
        assert "交付周期三日" in read["text"]
        assert session.evidence_sources()[0]["relation_status"] == "unconfirmed"
        relation = await session.execute("assess_source_relation", {
            "source_ref": "notice", "source_version": read["source_version"],
            "relation": "same_procurement", "quote": "采购编号：P-100", "reason": "matching id",
        })
        assert relation["relation_status"] == "verified"
        assert session.dependencies()[0]["source_version"] == read["source_version"]
        saved = read_saved_source(db, 1, "notice", read["source_version"], limit=4000)
        assert "采购编号：P-100" in saved["text"]
        assert saved["source"]["source_ref"] == "notice"
        assert saved["total_chars"] == len(saved["text"])
        assert saved["has_more"] is False
        assert list_sources(db, 1)[0]["source_ref"] == "notice"
        assert session.investigation()["policy_version"] == RESEARCH_POLICY_VERSION
        await client.aclose()

    asyncio.run(run())


def test_list_scan_does_not_promote_unrelated_navigation_links(tmp_path: Path):
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=(
                "<html><body>P-100 查询页 "
                "<a href='/home'>首页</a>"
                "<a href='/notice-p100'>P-100 公告</a>"
                "<a href='/login'>登录</a>"
                "</body></html>"
            ).encode(),
        )

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "list", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "list_scan", "url": "https://official.test/list"},
    }]}, handler=handler)

    async def run():
        found = await session.execute("search_project_sources", {"purpose": "scan", "query": "P-100"})
        assert found["status"] == "success"
        assert [source["url"] for source in found["sources"]] == ["https://official.test/notice-p100"]
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("extension", ["pdf", "docx"])
def test_document_bytes_are_read_and_exact_quote_can_be_verified(tmp_path: Path, extension: str):
    body = (_small_pdf(["Notice number: P-100 PDF procurement"])
            if extension == "pdf" else _small_docx("Notice number: P-100 DOCX procurement"))
    content_type = ("application/pdf" if extension == "pdf"
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{
                "id": "document", "url": f"/docs/sample.{extension}", "title": "sample document",
            }]})
        return httpx.Response(200, headers={"content-type": content_type}, content=body)

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        found = await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        assert found["status"] == "success"
        read = await session.execute("read_research_source", {"source_ref": "document", "purpose": "read"})
        assert read["status"] == "read"
        assert read["kind"] == "document"
        assert "P-100" in read["text"]
        relation = await session.execute("assess_source_relation", {
            "source_ref": "document", "source_version": read["source_version"],
            "relation": "same_procurement", "quote": "P-100", "reason": "document identifier",
        })
        assert relation["relation_status"] == "verified"
        evidence = session.evidence_sources()
        assert evidence and any("P-100" in page["text"] for page in evidence[0]["page_texts"])
        await client.aclose()

    asyncio.run(run())


def test_saved_source_snapshot_paginates_one_page_and_rejects_missing_page(tmp_path: Path):
    body = _small_pdf([
        "P-100 first page has enough text for an offset snapshot",
        "P-100 second page is independently addressable",
    ])

    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "multipage", "url": "/docs/multipage.pdf"}]})
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=body)

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        read = await session.execute("read_research_source", {"source_ref": "multipage", "purpose": "read"})
        assert read["status"] == "read"
        full = read_saved_source(db, 1, "multipage", read["source_version"], page=1, offset=0, limit=8000)
        assert full["page"] == 1
        assert full["offset"] == 0
        assert full["total_chars"] == len(full["text"])
        assert full["has_more"] is False

        offset = 7
        limit = 11
        snapshot = read_saved_source(db, 1, "multipage", read["source_version"],
                                     page=1, offset=offset, limit=limit)
        assert snapshot["source"]["source_ref"] == "multipage"
        assert snapshot["page"] == 1
        assert snapshot["offset"] == offset
        assert snapshot["total_chars"] == full["total_chars"]
        assert snapshot["text"] == full["text"][offset:offset + limit]
        assert snapshot["has_more"] is (offset + limit < full["total_chars"])
        with pytest.raises(LookupError):
            read_saved_source(db, 1, "multipage", read["source_version"], page=3)
        await client.aclose()

    asyncio.run(run())


def test_download_budget_closes_stream_before_consuming_all_bytes(tmp_path: Path):
    class Stream(httpx.AsyncByteStream):
        consumed = 0
        closed = False

        async def __aiter__(self):
            for _ in range(20):
                self.consumed += 1
                yield b"x" * 100

        async def aclose(self):
            self.closed = True

    stream = Stream()

    def handler(request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "large", "url": "/large.pdf"}]})
        return httpx.Response(200, headers={"content-type": "application/pdf"}, stream=stream)

    db, session, client = _setup(tmp_path, payload={"budgets": {"max_download_bytes": 256}, "sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "large"})
        result = await session.execute("read_research_source", {"source_ref": "large", "purpose": "read"})
        assert result["error"] == "budget_exhausted"
        assert stream.closed and stream.consumed < 20
        assert db.execute("SELECT count(*) FROM research_source_versions").fetchone()[0] == 0
        await client.aclose()

    asyncio.run(run())


def test_download_byte_budget_stops_before_persisting_oversize_version(tmp_path: Path):
    body = _small_pdf(["P-100 " + "x" * 500])
    paths: list[str] = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "large", "url": "/docs/large.pdf"}]})
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=body)

    db, session, client = _setup(tmp_path, payload={
        "budgets": {"max_download_bytes": 128},
        "sources": [{
            "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
            "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
        }],
    }, handler=handler)

    async def run():
        found = await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        assert found["status"] == "success"
        result = await session.execute("read_research_source", {"source_ref": "large", "purpose": "read"})
        assert result["status"] == "budget_exhausted"
        assert result["error"] == "budget_exhausted"
        assert session.investigation()["status"] == "limited"
        assert paths == ["/api", "/docs/large.pdf"]
        assert db.execute("SELECT count(*) FROM research_source_versions").fetchone()[0] == 0
        await client.aclose()

    asyncio.run(run())


def test_conflicting_identifier_cannot_be_verified(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "other", "url": "/other", "title": "same name"}]})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content="<html><body>采购编号：P-999；项目背景。</body></html>".encode())

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "same name"})
        read = await session.execute("read_research_source", {"source_ref": "other", "purpose": "read"})
        relation = await session.execute("assess_source_relation", {
            "source_ref": "other", "source_version": read["source_version"],
            "relation": "same_procurement", "quote": "采购编号：P-999", "reason": "conflict test",
        })
        assert relation["relation_status"] == "rejected"
        assert session.dependencies() == []
        await client.aclose()

    asyncio.run(run())


def test_same_site_and_same_title_do_not_verify_procurement_without_identity(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "same-title", "url": "/same-title", "title": "同名项目"}]})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content="<html><body>同名项目，设备采购。</body></html>".encode())

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "同名项目"})
        read = await session.execute("read_research_source", {"source_ref": "same-title", "purpose": "read"})
        relation = await session.execute("assess_source_relation", {
            "source_ref": "same-title", "source_version": read["source_version"],
            "relation": "same_procurement", "quote": "同名项目", "reason": "same title only",
        })
        assert relation["relation_status"] == "unconfirmed"
        await client.aclose()

    asyncio.run(run())


def test_known_lot_or_buyer_conflict_rejects_same_procurement(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "conflict", "url": "/conflict"}]})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b"<html><body>Notice number: P-100; Lot: L2; Buyer: Buyer B</body></html>")

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)
    session.records = [{"notice_number": "P-100", "lot_number": "L1", "buyer": "Buyer A"}]

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        read = await session.execute("read_research_source", {"source_ref": "conflict", "purpose": "read"})
        relation = await session.execute("assess_source_relation", {
            "source_ref": "conflict", "source_version": read["source_version"],
            "relation": "same_procurement", "quote": "Notice number: P-100; Lot: L2; Buyer: Buyer B",
            "reason": "identity conflict",
        })
        assert relation["relation_status"] == "rejected"
        assert session.dependencies() == []
        await client.aclose()

    asyncio.run(run())


def test_related_and_buyer_relations_require_quote_support(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "relation", "url": "/relation"}]})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b"<html><body>Unrelated material. Alpha project for Buyer A. Buyer A is purchaser.</body></html>")

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)
    session.records = [{"project_name": "Alpha project", "buyer": "Buyer A"}]

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "Alpha"})
        read = await session.execute("read_research_source", {"source_ref": "relation", "purpose": "read"})
        weak_related = await session.execute("assess_source_relation", {
            "source_ref": "relation", "source_version": read["source_version"], "relation": "related_project",
            "quote": "Unrelated material", "reason": "weak related claim",
        })
        weak_buyer = await session.execute("assess_source_relation", {
            "source_ref": "relation", "source_version": read["source_version"], "relation": "buyer_background",
            "quote": "Unrelated material", "reason": "weak buyer claim",
        })
        strong_related = await session.execute("assess_source_relation", {
            "source_ref": "relation", "source_version": read["source_version"], "relation": "related_project",
            "quote": "Alpha project for Buyer A", "reason": "title and buyer",
        })
        strong_buyer = await session.execute("assess_source_relation", {
            "source_ref": "relation", "source_version": read["source_version"], "relation": "buyer_background",
            "quote": "Buyer A", "reason": "known buyer",
        })
        assert weak_related["relation_status"] == "unconfirmed"
        assert weak_buyer["relation_status"] == "unconfirmed"
        assert strong_related["relation_status"] == "verified"
        assert strong_buyer["relation_status"] == "verified"
        await client.aclose()

    asyncio.run(run())


def test_redirect_private_ip_and_cross_project_access_are_blocked(tmp_path: Path):
    seen: list[str] = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "redirect", "url": "/redirect"}]})
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "redirect"})
        result = await session.execute("read_research_source", {"source_ref": "redirect", "purpose": "read"})
        assert result["status"] == "blocked"
        assert "127.0.0.1" not in "\n".join(seen)
        with pytest.raises(LookupError):
            read_saved_source(db, 2, "redirect", "missing")
        await client.aclose()

    asyncio.run(run())


def test_budget_stop_is_distinct_from_source_block(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "budget", "url": "/budget"}]})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><body>body</body></html>")

    db, session, client = _setup(tmp_path, payload={"budgets": {"max_calls": 1}, "sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        found = await session.execute("search_project_sources", {"purpose": "find", "query": "budget"})
        assert found["status"] == "success"
        result = await session.execute("read_research_source", {"source_ref": "budget", "purpose": "read"})
        assert result["status"] == "budget_exhausted"
        assert session.investigation()["status"] == "limited"
        await client.aclose()

    asyncio.run(run())


def test_refresh_keeps_old_version_and_same_version_uses_cache(tmp_path: Path):
    state = {"version": 1, "calls": 0, "fail": False}

    def handler(request: httpx.Request):
        state["calls"] += 1
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "versioned", "url": "/versioned"}]})
        if state["fail"]:
            return httpx.Response(500, text="temporary failure")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=f"<html><body>version-{state['version']}</body></html>".encode())

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "version"})
        first = await session.execute("read_research_source", {"source_ref": "versioned", "purpose": "read"})
        calls_after_first = state["calls"]
        again = await session.execute("read_research_source", {"source_ref": "versioned", "purpose": "read"})
        assert again["source_version"] == first["source_version"]
        assert state["calls"] == calls_after_first
        state["version"] = 2
        second = await session.execute("read_research_source", {
            "source_ref": "versioned", "purpose": "refresh", "refresh": True,
        })
        assert second["source_version"] != first["source_version"]
        assert db.execute("SELECT count(*) FROM research_source_versions WHERE source_ref='versioned'").fetchone()[0] == 2
        assert read_saved_source(db, 1, "versioned", first["source_version"])["text"] == "version-1"
        assert current_dependencies(db, 1, [{"source_ref": "versioned", "source_version": first["source_version"], "job_id": 1}])[0]["changed"]
        state["fail"] = True
        failed = await session.execute("read_research_source", {
            "source_ref": "versioned", "purpose": "failed refresh", "refresh": True,
        })
        assert failed["status"] == "failed"
        latest = db.execute("SELECT latest_version FROM research_project_sources WHERE project_id=1 AND source_ref='versioned'").fetchone()[0]
        assert latest == second["source_version"]
        await client.aclose()

    asyncio.run(run())


def test_investigation_is_job_scoped_and_merge_preserves_transaction(tmp_path: Path):
    db, session, client = _setup(tmp_path, payload={"sources": []},
                                 handler=lambda request: httpx.Response(200, text=""))
    assert session.investigation()["sources"] == []
    db.execute("INSERT INTO projects(id,created_at,updated_at) VALUES (2,'x','x')")
    db.execute("INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES (2,2,'running','x','x')")
    db.execute("INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES (3,1,'running','x','x')")
    db.execute("""INSERT INTO research_sources
                 (source_ref,source_id,canonical_url,original_url,title,kind,status,config_id,created_at,updated_at)
                 VALUES ('merge-source','official','https://official.test/a','https://official.test/a','A','page','read','official','x','x')""")
    db.execute("""INSERT INTO research_project_sources
                 (project_id,source_ref,status,relation,relation_status,scope,discovered_job_id,created_at,updated_at)
                 VALUES (1,'merge-source','read','same_procurement','verified','page',1,'x','x')""")
    db.commit()
    client3 = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    session3 = ResearchSession(db, project_id=1, job_id=3, records=[], sources_path=tmp_path / "sources.json",
                               client=client3)
    assert session3.investigation()["sources"] == []
    merge_project_research(db, 1, 2)
    assert db.execute("SELECT count(*) FROM research_project_sources WHERE project_id=2").fetchone()[0] == 1
    db.rollback()
    assert db.execute("SELECT count(*) FROM research_project_sources WHERE project_id=2").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM research_project_sources WHERE project_id=1").fetchone()[0] == 1
    asyncio.run(client3.aclose())
    asyncio.run(client.aclose())


def test_navigation_titles_remain_candidates_and_cannot_be_served_as_evidence(tmp_path: Path):
    def handler(request: httpx.Request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "notice", "url": "/notice"}]})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=(
            "<article>采购编号：P-100；本次采购钢材。</article>"
            "<div class='article-bottom'><em>上一篇</em>"
            "<a href='/other'>另一公告采购破碎机</a></div>".encode()))

    db, session, client = _setup(tmp_path, payload={"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}, handler=handler)

    async def run():
        await session.execute("search_project_sources", {"purpose": "find", "query": "P-100"})
        read = await session.execute("read_research_source", {"source_ref": "notice", "purpose": "read"})
        assert "本次采购钢材" in read["text"]
        assert "另一公告采购破碎机" not in read["text"]
        assert any(item["title"] == "另一公告采购破碎机" for item in session.investigation()["sources"])
        # Old immutable snapshots may contain page navigation. The bounded
        # reading view must exclude it even when serving that stored version.
        old_text = "采购编号：P-100；本次采购钢材。\n上一篇\n另一公告采购破碎机"
        db.execute("UPDATE research_source_versions SET body_text=?,page_texts_json=? WHERE source_ref='notice'",
                   (old_text, json.dumps([{"page": 1, "text": old_text}], ensure_ascii=False)))
        db.commit()
        cached = await session.execute("read_research_source", {"source_ref": "notice", "purpose": "cached"})
        assert "另一公告采购破碎机" not in cached["text"]
        search = await session.execute("search_research_content", {
            "source_ref": "notice", "source_version": read["source_version"],
            "query": "另一公告采购破碎机", "purpose": "check boundary",
        })
        assert search["matches"] == []

    try:
        asyncio.run(run())
    finally:
        asyncio.run(client.aclose())
        db.close()
