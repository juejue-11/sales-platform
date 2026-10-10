from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

import backend.service as service_module
from backend.ai import AIError, PiSDKProvider, validate_judgment
from backend.briefing import build_briefing
from backend.service import ScreeningService


NODE = shutil.which("node")


def _record(record_id: int = 174, *, attachments: list[dict] | None = None) -> dict:
    return {
        "id": record_id,
        "source_id": "procurement-test",
        "kind": "tender",
        "title": "钢材采购公告",
        "project_name": "钢材采购项目",
        "buyer": "某矿业公司",
        "body_text": "本项目招标内容、范围及规模详见附件《物料清单附件.pdf》。",
        "equipment": ["圆锥破碎机"],
        "facts": [],
        "inferences": [],
        "published_at": "2026-10-08",
        "deadline_at": "2099-12-31 09:00",
        "archive_path": f"archive/{record_id}/v1.html",
        "current_version": 1,
        "attachments": attachments or [{"name": "物料清单附件.pdf", "url": "https://example.test/list.pdf",
                                          "status": "unreadable", "sha256": "old-hash"}],
    }


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetcher):
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(_record(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(service_module, "fetch_record_documents", fetcher)
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=object())
    asyncio.run(service.import_file())
    return service


def test_refresh_persists_documents_without_replacing_source_body_or_calling_ai(tmp_path: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    async def fetch(record):
        calls.append(record)
        return [{
            "id": "doc-material-174", "record_id": record["id"], "name": "物料清单附件.pdf",
            "url": "https://example.test/list.pdf", "status": "parsed", "pages": 36,
            "sha256": "new-hash", "fetched_at": "2026-10-10T00:00:00Z", "mime_type": "application/pdf",
            "byte_size": 123, "text": "普碳中板；4mm；Q235B；5.0t；本溪市；合同签订后，供货商按通知供货量三日内供完",
            "page_texts": [{"page": 1, "text": "普碳中板；4mm；Q235B；5.0t；本溪市"}],
            "items": [{"name": "普碳中板", "specification": "4mm", "material": "Q235B",
                        "quantity": "5.0", "unit": "t", "delivery_period": "合同签订后，供货商按通知供货量三日内供完",
                        "delivery_location": "本溪市", "source_document_id": "doc-material-174",
                        "record_id": 174, "page": 1, "quote": "普碳中板；4mm；Q235B；5.0t；本溪市"}],
        }]

    service = _service(tmp_path, monkeypatch, fetch)
    try:
        result = asyncio.run(service.refresh_project_documents(174))
        procurement = result["procurement"]
        assert procurement["status"] == "complete"
        assert procurement["items"][0]["name"] == "普碳中板"
        assert procurement["scope_summary"] == "共1项物料，包括普碳中板；各项规格、材质和数量见明细。"
        assert "4mm" not in procurement["scope_summary"]
        assert procurement["delivery_location"] == "本溪市"
        assert "三日内供完" in procurement["delivery_period"]
        assert procurement["documents"][0]["id"] == "doc-material-174"
        assert "text" not in procurement["documents"][0]
        assert "items" not in procurement["documents"][0]
        row = service.db.execute("SELECT body_text,current_version FROM records WHERE id=174").fetchone()
        assert row["body_text"].startswith("本项目招标内容")
        assert row["current_version"] == 1
        assert calls and calls[0]["attachments"][0]["name"] == "物料清单附件.pdf"
        assert service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        service.close()


def test_briefing_does_not_promote_attachment_filename_and_uses_parsed_facts():
    record = _record()
    source_only = build_briefing(record)
    assert source_only["scope_excerpt"] == ""
    facts = {"items": [{"name": "普碳中板"}], "scope_summary": "普碳中板（4mm / Q235B）（共143项）",
             "delivery_period": "按通知供货量三日内供完", "delivery_location": "本溪市"}
    enriched = build_briefing(record, procurement=facts)
    assert "普碳中板" in enriched["scope_excerpt"]
    assert "物料清单附件.pdf" not in enriched["scope_excerpt"]
    assert "三日内供完" in enriched["delivery_excerpt"]
    assert "本溪市" in enriched["delivery_excerpt"]


def test_unparsed_document_never_claims_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def fetch(record):
        return [{"id": "doc-unreadable", "record_id": record["id"], "name": "清单.pdf",
                 "url": "https://example.test/list.pdf", "status": "unreadable", "error": "ocr_required"}]

    service = _service(tmp_path, monkeypatch, fetch)
    try:
        procurement = asyncio.run(service.refresh_project_documents(174))["procurement"]
        assert procurement["status"] == "unavailable"
        assert "附件未解析" in procurement["missing_fields"]
        assert procurement["items"] == []
    finally:
        service.close()


def test_refresh_keeps_only_latest_document_version_and_ignores_refresh_times(tmp_path: Path,
                                                                                monkeypatch: pytest.MonkeyPatch):
    state = {"version": 1}

    async def fetch(record):
        if state["version"] == 3:
            return []
        return [{"id": "doc-same-id", "record_id": record["id"], "name": "清单.pdf", "status": "parsed",
                 "sha256": f"hash-{state['version']}", "fetched_at": f"2026-10-10T00:0{state['version']}:00Z",
                 "text": f"钢板版本{state['version']}", "items": [{
                     "name": f"钢板版本{state['version']}", "quantity": "5", "unit": "t",
                     "specification": "4mm", "material": "Q235B", "delivery_period": "三日内供完",
                     "delivery_location": "本溪市", "source_document_id": "doc-same-id", "record_id": 174,
                     "page": 1, "quote": f"钢板版本{state['version']}",
                 }]}]

    service = _service(tmp_path, monkeypatch, fetch)
    try:
        first = asyncio.run(service.refresh_project_documents(174))["procurement"]
        assert len(first["items"]) == 1
        with service.db:
            service.db.execute(
                """INSERT INTO judgments(project_id,created_at,model,input_fingerprint,stale,decision,summary,demand_summary,
                   reasons_json,unknowns_json,next_actions_json,options_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (174, "2026-10-10T00:00:00+08:00", "test", "fp", 0, "verify", "x", "x", "[]", "[]", "[]", "[]"),
            )
        state["version"] = 2
        second = asyncio.run(service.refresh_project_documents(174))["procurement"]
        assert len(second["items"]) == 1
        assert second["items"][0]["name"] == "钢板版本2"
        assert len(second["documents"]) == 1
        assert service.db.execute("SELECT count(*) FROM procurement_documents WHERE record_id=174").fetchone()[0] == 2
        assert service.db.execute("SELECT count(*) FROM procurement_documents WHERE record_id=174 AND is_current=1").fetchone()[0] == 1
        assert service.db.execute("SELECT stale FROM judgments WHERE project_id=174 ORDER BY id DESC LIMIT 1").fetchone()[0] == 1

        # A repeat with the same semantic version only changes fetch/update
        # timestamps and must not stale a fresh judgment.
        with service.db:
            service.db.execute("UPDATE judgments SET stale=0 WHERE project_id=174")
        same = asyncio.run(service.refresh_project_documents(174))["procurement"]
        assert len(same["items"]) == 1
        assert service.db.execute("SELECT stale FROM judgments WHERE project_id=174 ORDER BY id DESC LIMIT 1").fetchone()[0] == 0

        # Removing the attachment from the current fetch removes it from the
        # aggregate while retaining both historical rows.
        state["version"] = 3
        removed = asyncio.run(service.refresh_project_documents(174))["procurement"]
        assert removed["documents"] == []
        assert removed["status"] == "unavailable"
        assert service.db.execute("SELECT count(*) FROM procurement_documents WHERE record_id=174").fetchone()[0] == 2
        assert service.db.execute("SELECT count(*) FROM procurement_documents WHERE record_id=174 AND is_current=1").fetchone()[0] == 0
    finally:
        service.close()


def test_document_evidence_requires_server_document_and_exact_quote():
    project = {"records": [{"id": 174, "body_text": "正文只说详见附件。"}]}
    document = {"id": "doc-material-174", "record_id": 174, "text": "普碳中板；4mm；Q235B；5.0t",
                "name": "物料清单附件.pdf", "url": "https://example.test/material-list.pdf", "sha256": "server-hash",
                "page_texts": [{"page": 1, "text": "其他内容"},
                                {"page": 7, "text": "普碳中板；4mm；Q235B；5.0t"}]}
    payload = {"decision": "consider", "summary": "核对附件明细", "demand_summary": "附件列出钢材",
               "reasons": [{"text": "附件有明细", "evidence": [{"record_id": 174,
                              "document_id": "doc-material-174",
                              "quote": "普碳中板；4mm；Q235B；5.0t"}]}],
               "unknowns": [], "next_actions": [], "options": []}
    result = validate_judgment(payload, project, "test", "fp", document_sources=[document])
    evidence = result["reasons"][0]["evidence"][0]
    assert evidence["document_id"] == "doc-material-174"
    assert evidence["document_name"] == "物料清单附件.pdf"
    assert evidence["source_url"] == "https://example.test/material-list.pdf"
    assert evidence["document_sha256"] == "server-hash"
    assert evidence["page"] == 7
    forged = json.loads(json.dumps(payload, ensure_ascii=False))
    forged["reasons"][0]["evidence"][0]["document_id"] = "doc-forged"
    with pytest.raises(AIError, match="document is not trusted"):
        validate_judgment(forged, project, "test", "fp", document_sources=[document])


def test_agent_fetch_is_part_of_same_job_fingerprint_and_does_not_requeue(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch):
    async def fetch(record):
        return [{"id": "doc-agent", "record_id": record["id"], "name": "清单.pdf", "status": "parsed",
                 "sha256": "agent-hash", "text": "钢板 5t 本溪市", "items": [{
                     "name": "钢板", "quantity": "5", "unit": "t", "specification": "4mm",
                     "material": "Q235B", "delivery_period": "三日内供完", "delivery_location": "本溪市",
                     "source_document_id": "doc-agent", "record_id": 174, "page": 1, "quote": "钢板 5t 本溪市",
                 }]}]

    class Agent:
        async def analyze(self, *, tool_callback, **kwargs):
            data = await tool_callback({"record_id": 174})
            assert data["documents"][0]["id"] == "doc-agent"
            return {"content": json.dumps({"decision": "verify", "summary": "核对附件", "demand_summary": "有钢材明细",
                                             "reasons": [{"text": "附件列明钢板", "evidence": [{
                                                 "record_id": 174, "document_id": "doc-agent",
                                                 "quote": "钢板 5t 本溪市"}]}], "unknowns": [], "next_actions": [],
                                             "options": []}, ensure_ascii=False),
                    "usage": {"model_calls": 1, "tool_calls": 1, "input_tokens": 1, "output_tokens": 1}}

    service = _service(tmp_path, monkeypatch, fetch)
    service.provider = Agent()
    try:
        service.update_settings({"business": {"capabilities": "测试能力"},
                                 "ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                                        "api_key": "secret-value"}})
        job_id = service.queue_project(174)["job_id"]
        asyncio.run(service.run_job(job_id))
        assert service.summary(174)["ai_status"] == "succeeded"
        assert service.project_detail(174)["judgment"]["stale"] is False
        assert service.db.execute("SELECT count(*) FROM jobs WHERE project_id=174").fetchone()[0] == 1
    finally:
        service.close()


def test_source_change_during_agent_document_fetch_stales_old_snapshot(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(record):
        started.set()
        await release.wait()
        return [{"id": "doc-concurrent", "record_id": record["id"], "name": "清单.pdf", "status": "parsed",
                 "sha256": "concurrent-hash", "text": "钢板 5t 本溪市", "items": [{
                     "name": "钢板", "quantity": "5", "unit": "t", "specification": "4mm", "material": "Q235B",
                     "delivery_period": "三日内供完", "delivery_location": "本溪市", "source_document_id": "doc-concurrent",
                     "record_id": 174, "page": 1, "quote": "钢板 5t 本溪市",
                 }]}]

    class Agent:
        async def analyze(self, *, tool_callback, **kwargs):
            data = await tool_callback({"record_id": 174})
            assert data["documents"][0]["id"] == "doc-concurrent"
            return {"content": json.dumps({"decision": "verify", "summary": "核对附件", "demand_summary": "有钢材明细",
                                             "reasons": [{"text": "附件列明钢板", "evidence": [{
                                                 "record_id": 174, "document_id": "doc-concurrent",
                                                 "quote": "钢板 5t 本溪市"}]}], "unknowns": [], "next_actions": [],
                                             "options": []}, ensure_ascii=False),
                    "usage": {"model_calls": 1, "tool_calls": 1, "input_tokens": 1, "output_tokens": 1}}

    service = _service(tmp_path, monkeypatch, fetch)
    service.provider = Agent()
    try:
        service.update_settings({"business": {"capabilities": "测试能力"},
                                 "ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                                        "api_key": "secret-value"}})
        job_id = service.db.execute("SELECT id FROM jobs WHERE project_id=174 ORDER BY id DESC LIMIT 1").fetchone()[0]

        async def scenario():
            running = asyncio.create_task(service.run_job(int(job_id)))
            await asyncio.wait_for(started.wait(), timeout=2)
            changed = _record()
            changed["body_text"] = "正文在附件抓取期间发生变化。"
            service.source_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            await service.import_file()
            release.set()
            await running

        asyncio.run(scenario())
        detail = service.project_detail(174)
        assert detail["judgment"]["stale"] is True
        assert detail["judgment"]["reasons"][0]["evidence"][0]["document_id"] == "doc-concurrent"
        assert detail["ai_decision"] is None
    finally:
        service.close()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the duplex protocol test")
def test_protocol2_fetch_tool_round_trip_and_protocol1_is_selected_without_callback(tmp_path: Path,
                                                                                     monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "duplex-runner.cjs"
    runner.write_text(
        """
        const readline = require('readline');
        const emit = value => process.stdout.write(JSON.stringify(value) + '\\n');
        const rl = readline.createInterface({input: process.stdin});
        let first = true;
        rl.on('line', line => {
          const value = JSON.parse(line);
          if (first) {
            first = false;
            if (value.protocol !== 2) process.exit(9);
            emit({type:'tool_request', id:'t1', tool:'fetch_project_documents', arguments:{record_id:174}});
          } else {
            if (value.type !== 'tool_response' || value.ok !== true || value.data.procurement.status !== 'complete') process.exit(8);
            emit({type:'result', engine:'pi_sdk', content:'{"decision":"verify"}', usage:{model_calls:1,tool_calls:1,input_tokens:1,output_tokens:1}});
            rl.close();
          }
        });
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")
    calls: list[dict] = []

    async def callback(arguments):
        calls.append(arguments)
        return {"documents": [], "procurement": {"status": "complete", "items": [{"name": "x"}]}}

    async def scenario():
        provider = PiSDKProvider()
        return await provider.analyze(base_url="https://mock.local/v1", model="mock", api_key="secret",
                                      messages=[{"role": "user", "content": "x"}], context={},
                                      tool_callback=callback)

    result = asyncio.run(scenario())
    assert result.content == '{"decision":"verify"}'
    assert calls == [{"record_id": 174}]


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the duplex protocol test")
def test_protocol2_tool_failure_uses_runner_safe_enum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "duplex-failure.cjs"
    runner.write_text(
        """
        const readline = require('readline');
        const emit = value => process.stdout.write(JSON.stringify(value) + '\\n');
        const rl = readline.createInterface({input: process.stdin});
        let first = true;
        rl.on('line', line => {
          const value = JSON.parse(line);
          if (first) {
            first = false;
            emit({type:'tool_request', id:'bad-record', tool:'fetch_project_documents', arguments:{record_id:999}});
          } else {
            if (value.type !== 'tool_response' || value.ok !== false || value.error !== 'record_not_in_project') process.exit(8);
            emit({type:'result', engine:'pi_sdk', content:'{"decision":"verify"}', usage:{model_calls:1,tool_calls:1,input_tokens:1,output_tokens:1}});
            rl.close();
          }
        });
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")

    async def callback(_arguments):
        raise AIError("工具无权限")

    async def scenario():
        return await PiSDKProvider().analyze(base_url="https://mock.local/v1", model="mock", api_key="secret",
                                             messages=[{"role": "user", "content": "x"}], context={},
                                             tool_callback=callback)

    assert asyncio.run(scenario()).content == '{"decision":"verify"}'
