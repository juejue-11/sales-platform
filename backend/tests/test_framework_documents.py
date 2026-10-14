from __future__ import annotations

import asyncio
import copy
import json
import hashlib
import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend.application.service import NotFoundError
from backend.main import create_app
from backend.tests.test_backend import Provider, make_service, row


def document():
    return {
        "id": "framework-doc", "record_id": 1, "name": "技术要求.pdf",
        "url": "https://example.test/spec.pdf", "status": "parsed", "pages": 2,
        "sha256": "saved-version", "fetched_at": "2026-10-12T08:00:00+08:00",
        "text": "第一页面采购破碎机。\n第二页隐藏技术要求。" + "详细要求" * 5000,
        "page_texts": [{"page": 1, "text": "第一页面采购破碎机。"},
                       {"page": 2, "text": "第二页隐藏技术要求。" + "详细要求" * 5000}],
        "items": [{"name": "破碎机", "quantity": "2", "unit": "台", "page": 1,
                   "quote": "第一页面采购破碎机。"}],
    }


def test_failed_refresh_preserves_successful_content_and_attempt(tmp_path, monkeypatch):
    responses = [[document()], [{"id": "framework-doc", "record_id": 1,
                                 "url": "https://example.test/spec.pdf", "name": "技术要求.pdf",
                                 "status": "failed", "error": "download_failed"}]]

    async def fetch(_record):
        return responses.pop(0)

    monkeypatch.setattr("backend.application.service.fetch_record_documents", fetch)
    service = make_service(tmp_path, [row()])
    try:
        asyncio.run(service.refresh_project_documents(1))
        after = asyncio.run(service.refresh_project_documents(1))["procurement"]
        saved = service.db.execute("SELECT * FROM procurement_documents WHERE id='framework-doc'").fetchone()
        assert saved["status"] == "parsed"
        assert saved["sha256"] == "saved-version"
        assert saved["text"] == document()["text"]
        assert any(item["name"] == "破碎机" for item in after["items"])
        assert any(item["status"] == "failed" for item in after["documents"])
        assert after["status"] == "partial"
        snapshot = service.document_snapshot(1, "framework-doc", sha256="saved-version")
        assert snapshot["text"] == document()["text"]
        assert snapshot["sha256"] == "saved-version"
        with pytest.raises(NotFoundError):
            service.document_snapshot(1, "framework-doc", sha256="wrong-version")
        with pytest.raises(NotFoundError):
            service.document_snapshot(2, "framework-doc", sha256="saved-version")
        client = TestClient(create_app(service))
        response = client.get("/api/projects/1/documents/framework-doc/snapshot?sha256=saved-version")
        assert response.status_code == 200
        assert response.json()["sha256"] == "saved-version"
        assert client.get("/api/projects/2/documents/framework-doc/snapshot").status_code == 404
    finally:
        service.close()


def test_agent_reuses_verified_collector_archive_and_saves_its_snapshot(tmp_path, monkeypatch):
    source_document = Document()
    source_document.add_paragraph("附件明确采购破碎机。")
    buffer = io.BytesIO()
    source_document.save(buffer)
    raw = buffer.getvalue()
    archive = tmp_path / "archive/1/v1-attachments/spec.docx"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    record = row()
    record["attachments"] = [{"name": "技术要求.docx", "url": "https://example.test/spec.docx",
                              "archive_path": "archive/1/v1-attachments/spec.docx", "sha256": digest}]

    async def fetch(_record):
        raise AssertionError("Verified collector bytes should not be downloaded again")

    class Agent:
        async def analyze(self, *, tool_callback, **_kwargs):
            view = await tool_callback({"record_id": 1})
            document = view["documents"][0]
            result = copy.deepcopy(Provider().payload)
            result["reasons"][0]["evidence"] = [{"record_id": 1, "document_id": document["id"],
                                                 "quote": "附件明确采购破碎机。"}]
            return {"content": json.dumps(result, ensure_ascii=False)}

    monkeypatch.setattr("backend.application.service.fetch_record_documents", fetch)
    service = make_service(tmp_path, [record], Agent())
    try:
        service.update_settings({"ai": {"enabled": True, "base_url": "https://mock.local/v1",
                                        "model": "mock", "api_key": "isolated-secret"}})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        detail = service.project_detail(1)
        assert detail["ai_status"] == "succeeded"
        evidence = detail["judgment"]["reasons"][0]["evidence"][0]
        assert "page" not in evidence
        snapshot = service.document_snapshot(1, evidence["document_id"], sha256=digest)
        assert "附件明确采购破碎机。" in snapshot["text"]
    finally:
        service.close()


def test_agent_reads_pages_without_repeated_download_and_retains_read_citations(tmp_path, monkeypatch):
    calls = []

    async def fetch(_record):
        calls.append(1)
        return [document()]

    class Agent:
        async def analyze(self, *, tool_callback, **_kwargs):
            directory = await tool_callback({"record_id": 1})
            assert len(json.dumps(directory, ensure_ascii=False)) < 16000
            first = await tool_callback({"record_id": 1, "document_id": "framework-doc", "page": 1})
            assert first["documents"][0]["page_texts"][0]["text"] == "第一页面采购破碎机。"
            await tool_callback({"record_id": 1, "document_id": "framework-doc", "page": 2, "limit": 100})
            result = copy.deepcopy(Provider().payload)
            result["reasons"][0]["evidence"] = [{"record_id": 1, "document_id": "framework-doc",
                                                 "page": 1, "quote": "第一页面采购破碎机。"}]
            return {"content": json.dumps(result, ensure_ascii=False), "usage": {"model_calls": 2}}

    monkeypatch.setattr("backend.application.service.fetch_record_documents", fetch)
    service = make_service(tmp_path, [row()], Agent())
    try:
        service.update_settings({"ai": {"enabled": True, "base_url": "https://mock.local/v1",
                                        "model": "mock", "api_key": "isolated-secret"}})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        assert service.project_detail(1)["ai_status"] == "succeeded"
        assert len(calls) == 1
        citation = service.project_detail(1)["judgment"]["reasons"][0]["evidence"][0]
        assert citation["document_sha256"] == "saved-version"
        assert citation["page"] == 1
    finally:
        service.close()
