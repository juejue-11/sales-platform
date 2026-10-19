"""Exercise facts, saved evidence and archives through the existing HTTP API."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx

import backend.application.service as service_module
from backend.application.service import ScreeningService
from backend.main import create_app


def test_procurement_facts_round_trip_without_model_or_source_mutation(tmp_path, monkeypatch):
    body = (
        "采购范围：破碎机2套。\n"
        "交货期：合同签订后30天。\n"
        "交货地点：甲方指定地点。\n"
        "投标人资格要求：须具备有效防爆合格证。\n"
        "联系方式：联系人张工，电话010-12345678。"
    )
    page_text = (
        "采购范围：破碎机2套。\n"
        "交货期：合同签订后40天。\n"
        "交货地点：甲方指定地点。\n"
        "投标人资格要求：须具备有效防爆合格证。\n"
        "联系方式：联系人张工，电话010-12345678。"
    )
    archived = b"<html><body>original procurement notice</body></html>"
    archive_path = tmp_path / "archive/7/v1.html"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(archived)
    archive_digest = hashlib.sha256(archived).hexdigest()
    record = {
        "id": 7, "source_id": "fact-api-test", "kind": "tender",
        "title": "破碎机采购公告", "project_name": "破碎机采购公告",
        "buyer": "测试采购单位", "body_text": body, "equipment": [],
        "facts": [{"text": "采购范围：破碎机2套。", "evidence": "采购范围：破碎机2套。"}],
        "inferences": [], "published_at": "2026-10-13", "deadline_at": "2099-12-31",
        "original_url": "https://example.test/notice/7", "current_version": 1,
        "archive_path": "archive/7/v1.html", "content_sha256": archive_digest,
        "attachments": [{"name": "技术要求.pdf", "url": "https://example.test/spec.pdf"}],
    }
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    source_before = source.read_bytes()

    async def fetch_documents(saved_record):
        assert saved_record["id"] == 7
        return [{
            "id": "doc-fact-api", "record_id": 7, "name": "技术要求.pdf",
            "url": "https://example.test/spec.pdf", "status": "parsed", "pages": 2,
            "sha256": "saved-doc-version", "mime_type": "application/pdf",
            "text": page_text, "page_texts": [{"page": 2, "text": page_text}],
            "items": [{"name": "破碎机", "quantity": "2", "unit": "套",
                       "specification": "TK240A", "material": "", "page": 2,
                       "delivery_period": "合同签订后40天", "delivery_location": "甲方指定地点",
                       "quote": "采购范围：破碎机2套。"}],
        }]

    class NoModel:
        async def complete(self, **kwargs):
            raise AssertionError("Procurement reads must not invoke a model")

    monkeypatch.setattr(service_module, "fetch_record_documents", fetch_documents)
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=NoModel())
    service.update_settings({"ai": {"enabled": False}, "import": {"auto_import": False}})

    async def exercise():
        await service.import_file()
        app = create_app(service)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            before = await client.get("/api/projects/7")
            assert before.status_code == 200
            assert before.json()["records"][0]["body_text"] == body
            refreshed = await client.post("/api/projects/7/documents/refresh", json={})
            assert refreshed.status_code == 200
            detail = (await client.get("/api/projects/7")).json()
            procurement = detail["procurement"]
            facts = procurement["facts"]
            assert {fact["field"] for fact in facts} >= {"delivery_period", "delivery_location", "qualification", "contact"}
            assert any(fact["field"] == "delivery_period" and fact["status"] == "conflict" for fact in facts)
            material = procurement["items"]
            assert len(material) == 1, "An announcement summary must not double-count its attachment row"
            assert {evidence["source_kind"] for evidence in material[0]["sources"]} == {"announcement", "attachment"}
            for fact in facts:
                for evidence in fact["sources"]:
                    assert evidence["record_id"] == 7
                    quote = "".join(evidence["quote"].split())
                    assert quote
                    if evidence["source_kind"] == "announcement":
                        assert quote in "".join(body.split())
                    else:
                        assert evidence["document_id"] == "doc-fact-api"
                        assert evidence["page"] == 2
                        assert evidence["sha256"] == "saved-doc-version"
                        assert quote in "".join(page_text.split())
            snapshot = await client.get("/api/projects/7/documents/doc-fact-api/snapshot", params={"sha256": "saved-doc-version"})
            assert snapshot.status_code == 200
            assert snapshot.json()["page_texts"] == [{"page": 2, "text": page_text}]
            assert (await client.get("/api/projects/7/documents/doc-fact-api/snapshot", params={"sha256": "other-version"})).status_code == 404
            archive = await client.get("/api/records/7/archive")
            assert archive.status_code == 200
            assert archive.content == archived
            assert detail["records"][0]["body_text"] == body
            assert service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0

    try:
        asyncio.run(exercise())
        assert source.read_bytes() == source_before
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == archive_digest
    finally:
        service.close()
