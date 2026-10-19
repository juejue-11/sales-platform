from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path

import httpx
import pytest

from backend.analysis.ai import AIError, PiSDKProvider, validate_judgment
from backend.main import create_app
from backend.application.service import ScreeningService
from backend.tests.test_backend import Provider, configure_ai, make_service, row


SOURCE_REF = "research_123"
SOURCE_VERSION = "a" * 64
READ_QUOTE = "合同生效后60天内交货"


def research_source():
    return {
        "source_ref": SOURCE_REF, "source_version": SOURCE_VERSION, "project_id": 1,
        "title": "破碎机采购澄清", "url": "https://bid.ansteel.cn/clarification.html",
        "fetched_at": "2026-10-12T12:00:00+08:00", "relation": "same_procurement",
        "relation_status": "verified", "text": READ_QUOTE,
        "page_texts": [{"page": 1, "text": READ_QUOTE}],
    }


def research_judgment():
    value = copy.deepcopy(Provider().payload)
    value["reasons"] = [{"text": "澄清给出交货期。", "evidence": [{
        "source_ref": SOURCE_REF, "source_version": SOURCE_VERSION, "quote": READ_QUOTE,
        "page": 1, "title": "model-spoof", "source_url": "https://evil.example/",
    }]}]
    return value


def test_research_citation_uses_server_version_metadata():
    value = validate_judgment(research_judgment(), {"id": 1, "records": [row()]}, "mock", "fp",
                              research_sources=[research_source()])
    evidence = value["reasons"][0]["evidence"][0]
    assert evidence["title"] == "破碎机采购澄清"
    assert evidence["source_url"] == "https://bid.ansteel.cn/clarification.html"
    assert evidence["page"] == 1 and evidence["quote"] == READ_QUOTE
    assert "record_id" not in evidence and "document_id" not in evidence
    assert SOURCE_VERSION in evidence["snapshot_url"]


@pytest.mark.parametrize("alteration", [
    {"source_ref": "forged"}, {"source_version": "b" * 64}, {"quote": "未返回的完整文档内容"},
    {"page": 2}, {"page": True}, {"record_id": 1}, {"document_id": "spoof"},
])
def test_research_citation_rejects_unread_and_mixed_identifiers(alteration):
    value = research_judgment()
    value["reasons"][0]["evidence"][0].update(alteration)
    with pytest.raises(AIError):
        validate_judgment(value, {"id": 1, "records": [row()]}, "mock", "fp",
                          research_sources=[research_source()])


@pytest.mark.parametrize("alteration", [
    {"project_id": 2}, {"relation_status": "unconfirmed"}, {"relation_status": "rejected"},
    {"relation": "conflict"}, {"relation": "unrelated"},
])
def test_research_citation_requires_authorized_verified_relationship(alteration):
    source = research_source()
    source.update(alteration)
    with pytest.raises(AIError):
        validate_judgment(research_judgment(), {"id": 1, "records": [row()]}, "mock", "fp",
                          research_sources=[source])


def test_research_citation_cannot_join_separately_read_fragments():
    source = research_source()
    source["text"] = ""
    source["page_texts"] = [{"page": 1, "text": "合同生效后"}, {"page": 1, "text": "60天内交货"}]
    with pytest.raises(AIError, match="not read"):
        validate_judgment(research_judgment(), {"id": 1, "records": [row()]}, "mock", "fp",
                          research_sources=[source])


def test_single_call_is_not_presented_as_external_investigation(tmp_path):
    service = make_service(tmp_path, [row()])
    try:
        configure_ai(service)
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        detail = service.project_detail(1)
        assert detail["judgment"]["investigation"]["status"] == "not_performed"
        assert detail["judgment"]["stale"] is False
        assert service.queue_project(1)["status"] == "succeeded"
    finally:
        service.close()


class ResearchProvider(Provider):
    """Exercises the trusted host loop without invoking a remote model."""

    async def analyze(self, *, tool_callback, **kwargs):
        found = await tool_callback({"purpose": "核实交付要求", "query": "P-100"},
                                    tool="search_project_sources")
        source_ref = found["sources"][0]["source_ref"]
        read = await tool_callback({"purpose": "读取澄清原文", "source_ref": source_ref},
                                   tool="read_research_source")
        version = read["source_version"]
        relation = await tool_callback({"source_ref": source_ref, "source_version": version,
                                       "relation": "same_procurement", "quote": "采购编号：P-100",
                                       "reason": "采购编号一致"}, tool="assess_source_relation")
        assert relation["relation_status"] == "verified"
        matches = await tool_callback({"purpose": "查找交付条款", "source_ref": source_ref,
                                      "source_version": version, "query": "60天"},
                                     tool="search_research_content")
        assert matches["status"] in {"success", "read", "found"}, matches
        value = copy.deepcopy(self.payload)
        value["reasons"] = [{"text": "澄清明确交付期。", "evidence": [{
            "source_ref": source_ref, "source_version": version, "quote": READ_QUOTE, "page": 1,
        }]}]
        value["summary"] = "交付要求已核实，型号仍需确认。"
        return {"content": json.dumps(value, ensure_ascii=False), "usage": {"model_calls": 2, "tool_calls": 4}}


def make_research_service(tmp_path, records, *, provider=None):
    source_path = tmp_path / "records.jsonl"
    source_path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    config_path = tmp_path / "sources.json"
    config_path.write_text(json.dumps({"sources": [{
        "id": "official", "base_url": "https://official.test", "allowed_hosts": ["official.test"],
        "search": {"adapter": "post_json_list", "url": "https://official.test/api", "items_path": "items"},
    }]}), encoding="utf-8")
    state = {"text": f"采购编号：P-100；{READ_QUOTE}。"}

    def handler(request):
        if request.url.path == "/api":
            return httpx.Response(200, json={"items": [{"id": "notice", "url": "/notice", "title": "采购交付澄清"}]})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=f"<html><body>{state['text']}</body></html>".encode("utf-8"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source_path,
                               archive_root=tmp_path, provider=provider or ResearchProvider(),
                               research_sources_path=config_path, research_client=client)
    asyncio.run(service.import_file())
    configure_ai(service)
    return service, client, state


def test_real_host_investigation_preserves_versions_and_only_cited_dependencies(tmp_path, monkeypatch):
    from backend.research.project_research import ResearchSession

    monkeypatch.setenv("SCREENING_AI_ENGINE", "pi_sdk")
    source_record = row()
    source_record["notice_number"] = "P-100"
    service, client, state = make_research_service(tmp_path, [source_record])
    try:
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        detail = service.project_detail(1)
        assert detail["judgment"] is not None, service.status()["jobs"]
        report = detail["judgment"]
        assert report["stale"] is False
        assert report["investigation"]["status"] == "completed"
        assert len(report["investigation"]["events"]) == 4
        assert service.queue_project(1)["status"] == "succeeded"
        evidence = report["reasons"][0]["evidence"][0]
        saved = service.research_source_snapshot(1, evidence["source_ref"], evidence["source_version"], page=1)
        assert READ_QUOTE in saved["text"]

        job_id = service.queue_project(1, force=True)["job_id"]
        session = ResearchSession(service.db, project_id=1, job_id=job_id, records=[source_record],
                                  sources_path=service.state.research_sources_path, client=client)
        # A candidate search alone is not a new input version.
        asyncio.run(session.execute("search_project_sources", {"purpose": "再次查找", "query": "P-100"}))
        assert service.project_detail(1)["judgment"]["stale"] is False
        state["text"] = "采购编号：P-100；变更为合同生效后90天内交货。"
        refreshed = asyncio.run(session.execute("read_research_source", {
            "purpose": "检查变更", "source_ref": evidence["source_ref"], "refresh": True,
        }))
        assert refreshed["source_version"] != evidence["source_version"]
        assert service.project_detail(1)["judgment"]["stale"] is True
        assert READ_QUOTE in service.research_source_snapshot(1, evidence["source_ref"], evidence["source_version"])["text"]
    finally:
        asyncio.run(client.aclose())
        service.close()


def test_research_history_routes_enforce_project_scope(tmp_path):
    service = make_service(tmp_path, [row(), row(2)])
    try:
        configure_ai(service)
        job_id = service.queue_project(1)["job_id"]
        run_id = service.agent_runs.new_agent_run(job_id, 1)
        with service.db:
            service.db.execute("UPDATE agent_runs SET investigation_json=? WHERE id=?", (
                json.dumps({"status": "limited", "policy_version": "test", "events": [], "sources": [],
                            "unresolved": ["交付要求未确认"], "stop_reason": "来源读取失败"}), run_id))

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),
                                         base_url="http://local.test") as client:
                history = await client.get("/api/projects/1/research/runs")
                assert history.status_code == 200 and history.json()["total"] == 1
                assert history.json()["items"][0]["investigation"]["status"] == "limited"
                assert (await client.get(f"/api/projects/2/research/runs/{run_id}")).status_code == 404
                assert (await client.get(f"/api/projects/1/research/runs/{run_id}")).status_code == 200
                assert (await client.get("/api/projects/1/research/sources/forged/versions/fake")).status_code == 404
                assert (await client.get("/api/projects/1/research/sources/forged/versions/fake?limit=99999")).status_code == 422

        asyncio.run(scenario())
    finally:
        service.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the subprocess bridge")
def test_python_bridge_dispatches_research_tools_and_preserves_attachment_callback(tmp_path, monkeypatch):
    runner = tmp_path / "research-runner.mjs"
    runner.write_text("""
import readline from 'node:readline';
const lines = readline.createInterface({input:process.stdin})[Symbol.asyncIterator]();
const request = JSON.parse((await lines.next()).value);
const emit = value => process.stdout.write(JSON.stringify(value)+'\\n');
const requests = [
 ['search_project_sources',{purpose:'核实交付要求',query:'破碎机采购澄清'}],
 ['read_research_source',{source_ref:'research_123',purpose:'读取澄清原文'}],
 ['assess_source_relation',{source_ref:'research_123',source_version:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',relation:'same_procurement',quote:'采购编号ABC-2026',reason:'采购编号相同'}],
 ['search_research_content',{source_ref:'research_123',source_version:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',query:'交货',purpose:'查找交期原文'}],
 ['fetch_project_documents',{record_id:1}],
];
for (const [index,[tool,args]] of requests.entries()) {
 emit({type:'tool_request',id:String(index),tool,arguments:args});
 const response=JSON.parse((await lines.next()).value);
 if (!response.ok) throw Error('callback dispatch failed');
}
emit({type:'result',engine:'pi_sdk',content:'{}',usage:{model_calls:1,tool_calls:5,input_tokens:1,output_tokens:1}});
process.exit(0);
""", encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    seen = []

    async def callback(arguments, *, tool):
        seen.append((tool, arguments))
        return {"status": "ok"}

    asyncio.run(PiSDKProvider().analyze(base_url="https://mock.local/v1", model="mock", api_key="local-test-key",
                                      messages=[{"role": "user", "content": "调查项目"}], context={},
                                      tool_callback=callback))
    assert [tool for tool, _ in seen] == ["search_project_sources", "read_research_source", "assess_source_relation",
                                         "search_research_content", "fetch_project_documents"]
