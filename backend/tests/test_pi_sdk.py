from __future__ import annotations

import asyncio
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from backend.ai import AIError, PiSDKProvider, _safe_error_code, _safe_progress_event
from backend.service import ScreeningService
from backend.utils import json_text, sha256_text


NODE = shutil.which("node")


def _runner_source() -> str:
    return textwrap.dedent(
        r"""
        const chunks = [];
        for await (const chunk of process.stdin) chunks.push(chunk);
        const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        const mode = request.config.model;
        const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");

        if (mode === "timeout") {
          setInterval(() => {}, 1000);
          await new Promise(() => {});
        }
        if (mode === "error") {
          emit({type: "error", code: "MODEL_ERROR", message: "request-key secret-value"});
          process.exit(0);
        }
        if (mode === "secret") {
          emit({type: "progress", stage: "started", label: "DO_NOT_STORE"});
          emit({type: "result", engine: "pi_sdk", content: JSON.stringify({secret: "secret-value"}),
            usage: {model_calls: 1, tool_calls: 0, input_tokens: 1, output_tokens: 1}});
          process.exit(0);
        }
        emit({type: "progress", stage: "started", label: "DO_NOT_STORE"});
        emit({type: "progress", stage: "tool_start", tool: "read_project_records", label: "MALICIOUS"});
        emit({type: "progress", stage: "tool_end", tool: "read_project_records", is_error: true, label: "MALICIOUS"});
        emit({type: "progress", stage: "completed", label: "MALICIOUS"});
        emit({type: "result", engine: "pi_sdk", content: JSON.stringify({decision: "verify"}),
          usage: {model_calls: 1, tool_calls: 1, input_tokens: 5, output_tokens: 7}});
        process.exit(0);
        """
    )


def _bridge_args(mode: str) -> dict:
    return {
        "base_url": "https://mock.local/v1",
        "model": mode,
        "api_key": "secret-value",
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        "context": {"project": {}, "company_capability_card": {}, "related_projects": []},
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_pi_sdk_bridge_consumes_jsonl_and_sanitizes_activity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "runner.mjs"
    runner.write_text(_runner_source(), encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")
    events: list[dict] = []

    async def scenario():
        provider = PiSDKProvider()
        return await provider.analyze(**_bridge_args("normal"), on_event=events.append)

    result = asyncio.run(scenario())
    assert result.content == '{"decision":"verify"}'
    assert result.usage == {"model_calls": 1, "tool_calls": 1, "input_tokens": 5, "output_tokens": 7}
    assert [event["stage"] for event in events] == ["started", "tool_start", "tool_end"]
    assert events[1]["label"] == "读取项目资料开始"
    assert events[2]["label"] == "读取项目资料失败"
    assert events[2]["_is_error"] is True
    assert all("DO_NOT_STORE" not in json.dumps(event, ensure_ascii=False) for event in events)
    assert all("MALICIOUS" not in json.dumps(event, ensure_ascii=False) for event in events)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_pi_sdk_bridge_redacts_runtime_errors_and_rejects_secret_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "runner.mjs"
    runner.write_text(_runner_source(), encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")

    async def run(mode: str):
        return await PiSDKProvider().analyze(**_bridge_args(mode))

    with pytest.raises(AIError) as runtime_error:
        asyncio.run(run("error"))
    assert "secret-value" not in str(runtime_error.value)
    assert "request-key" not in str(runtime_error.value)

    with pytest.raises(AIError) as result_error:
        asyncio.run(run("secret"))
    assert "secret-value" not in str(result_error.value)
    assert "受限凭据" in str(result_error.value)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_pi_sdk_bridge_terminates_timed_out_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "runner.mjs"
    runner.write_text(_runner_source(), encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")

    async def scenario():
        provider = PiSDKProvider()
        return await provider.analyze(**_bridge_args("timeout"), limits={"timeout_ms": 120})

    with pytest.raises(AIError, match="超时"):
        asyncio.run(scenario())


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_pi_sdk_bridge_deadline_covers_child_that_does_not_read_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "no-read.mjs"
    runner.write_text(
        "process.stdin.pause(); setInterval(() => {}, 1000);\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")
    large_args = _bridge_args("normal")
    large_args["messages"][-1]["content"] = "x" * (3 * 1024 * 1024)

    async def scenario():
        return await PiSDKProvider().analyze(**large_args, limits={"timeout_ms": 120})

    with pytest.raises(AIError, match="超时"):
        asyncio.run(scenario())


def test_pi_sdk_error_codes_are_allowlisted():
    assert _safe_error_code("MODEL_ERROR") == "MODEL_ERROR"
    assert _safe_error_code("INVALID_INPUT") == "INVALID_INPUT"
    assert _safe_error_code("MODEL_CONFIG") == "MODEL_CONFIG"
    assert _safe_error_code("INTERNAL") == "INTERNAL"
    assert _safe_error_code("model-key-secret") == "RUNTIME_ERROR"


def _record(record_id: int, body: str, *, project_key: str, notice_number: str,
            related_record_id: int | None = None) -> dict:
    return {
        "id": record_id,
        "source_id": "bridge-test",
        "kind": "tender",
        "title": f"破碎机采购公告 {record_id}",
        "project_name": f"破碎机项目 {record_id}",
        "buyer": "某矿业公司",
        "notice_number": notice_number,
        "body_text": body,
        "equipment": ["破碎机"],
        "facts": [{"text": body, "evidence": body}],
        "inferences": [],
        "published_at": "2026-10-08",
        "deadline_at": "2099-12-31 09:00",
        "project_key": project_key,
        "related_record_id": related_record_id,
        "archive_path": f"archive/{record_id}/v1.html",
        "current_version": 1,
    }


class ProgressProvider:
    def __init__(self, quote: str = "采购破碎机，截止时间：2099-12-31 09:00。"):
        self.context: dict | None = None
        self.quote = quote

    async def analyze(self, *, context, on_event, **kwargs):
        self.context = context
        await on_event({"stage": "started", "label": "secret model text"})
        await on_event({"stage": "tool_start", "tool": "read_project_records", "label": "secret"})
        await on_event({"stage": "tool_end", "tool": "read_project_records", "is_error": True, "label": "secret"})
        return {
            "content": json.dumps({
                "decision": "consider",
                "summary": "关联资料支持继续核实",
                "demand_summary": "项目涉及破碎机采购",
                "reasons": [{"text": "关联公告正文有采购需求", "evidence": [
                    {"record_id": 1, "quote": self.quote},
                ]}],
                "unknowns": [], "next_actions": ["核实采购批次"], "options": [],
            }, ensure_ascii=False),
            "usage": {"model_calls": 1, "tool_calls": 1, "input_tokens": 20, "output_tokens": 30},
        }


def test_agent_run_persists_safe_activity_and_allows_included_related_citation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("SCREENING_AI_ENGINE", raising=False)
    source = tmp_path / "records.jsonl"
    records = [
        _record(1, "采购破碎机，截止时间：2099-12-31 09:00。", project_key="k1", notice_number="A-1"),
        _record(2, "本批次与关联公告需核实。", project_key="k2", notice_number="B-2", related_record_id=1),
    ]
    source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    provider = ProgressProvider()
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=provider)
    try:
        asyncio.run(service.import_file())
        service.update_settings({
            "business": {"capabilities": "可供应破碎机"},
            "ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                   "api_key": "secret-value"},
        })
        job_id = service.queue_project(2)["job_id"]
        asyncio.run(service.run_job(job_id))

        detail = service.project_detail(2)
        agent_run = detail["agent_run"]
        assert agent_run["engine"] == "pi_sdk"
        assert agent_run["status"] == "succeeded"
        assert agent_run["error"] is None
        assert agent_run["usage"] == {"model_calls": 1, "tool_calls": 1, "input_tokens": 20, "output_tokens": 30}
        assert agent_run["limits"] == {"max_turns": 6, "max_tool_calls": 12, "timeout_ms": 180000,
                                        "max_output_tokens": 4096}
        labels = [step["label"] for step in agent_run["steps"]]
        assert labels == ["智能体已启动", "读取项目资料开始", "读取项目资料失败", "方案已通过校验"]
        assert all("secret" not in json.dumps(step, ensure_ascii=False).lower() for step in agent_run["steps"])
        assert all("_is_error" not in step for step in agent_run["steps"])
        assert provider.context is not None
        related = provider.context["related_projects"]
        assert [project["id"] for project in related] == [1]
        assert related[0]["records"][0]["record_id"] == 1
        assert detail["judgment"]["reasons"][0]["evidence"][0]["record_id"] == 1

        # The related project is part of the saved input.  Updating its source
        # invalidates this project's active conclusion while retaining history.
        records[0]["body_text"] = "采购破碎机，截止时间：2099-12-31 09:00。关联资料已更新。"
        source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
        asyncio.run(service.import_file())
        # A configured service immediately requeues stale work; the durable
        # judgment itself remains explicitly stale while that fresh job waits.
        assert service.summary(2)["ai_status"] == "queued"
        assert service.project_detail(2)["judgment_history"]
        assert service.project_detail(2)["judgment_history"][0]["stale"] is True
    finally:
        service.close()


def test_new_human_related_link_stales_existing_judgment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCREENING_AI_ENGINE", raising=False)
    source = tmp_path / "records.jsonl"
    records = [
        _record(1, "采购破碎机，截止时间：2099-12-31 09:00。", project_key="k1", notice_number="A-1"),
        _record(2, "另一批次采购破碎机，截止时间：2099-12-31 09:00。", project_key="k2", notice_number="B-2"),
    ]
    source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=ProgressProvider())
    try:
        asyncio.run(service.import_file())
        service.update_settings({
            "business": {"capabilities": "可供应破碎机"},
            "ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                   "api_key": "secret-value"},
        })
        job_id = service.queue_project(1)["job_id"]
        asyncio.run(service.run_job(job_id))
        assert service.summary(1)["ai_status"] == "succeeded"
        service.link_projects(1, 2, "same_project", "人工确认关联")
        assert service.summary(1)["ai_status"] == "stale"
        assert service.project_detail(1)["judgment_history"][0]["stale"] is True
    finally:
        service.close()


def test_project_merge_combines_two_gate_overrides_and_keeps_judgment_history(tmp_path: Path):
    source = tmp_path / "records.jsonl"
    first = _record(1, "采购破碎机，截止时间：2099-12-31 09:00。", project_key="k1", notice_number="A-1")
    second = _record(2, "另一批次采购破碎机，截止时间：2099-12-31 09:00。", project_key="k2", notice_number="B-2")
    first["title"] = second["title"] = "破碎机采购公告"
    source.write_text(json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=ProgressProvider())
    try:
        asyncio.run(service.import_file())
        service.gate_action(1, "restore", "目标项目人工备注")
        service.gate_action(2, "restore", "来源项目人工备注")
        with service.db:
            for project_id, fingerprint in ((1, "fp-target"), (2, "fp-source")):
                service.db.execute(
                    """INSERT INTO judgments(project_id,created_at,model,input_fingerprint,stale,decision,summary,demand_summary,
                       reasons_json,unknowns_json,next_actions_json,options_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (project_id, "2026-10-09T00:00:00+08:00", "test", fingerprint, 0,
                     "verify", "历史判断", "待核实", "[]", "[]", "[]", "[]"),
                )

        second["notice_number"] = "A-1"
        second["related_record_id"] = 1
        source.write_text(json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False), encoding="utf-8")
        asyncio.run(service.import_file())

        assert service.dashboard()["total_projects"] == 1
        gates = service.db.execute("SELECT project_id,reason FROM project_gate ORDER BY project_id").fetchall()
        assert len(gates) == 1 and gates[0]["project_id"] == 1
        assert "目标项目人工备注" in gates[0]["reason"]
        assert "来源项目人工备注" in gates[0]["reason"]
        history = service.db.execute("SELECT project_id,count(*) AS count FROM judgments GROUP BY project_id").fetchall()
        assert len(history) == 1 and history[0]["project_id"] == 1 and history[0]["count"] == 2
    finally:
        service.close()


def test_related_context_truncates_body_and_tail_change_stales_judgment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCREENING_AI_ENGINE", raising=False)
    source = tmp_path / "records.jsonl"
    full_body = "采购破碎机，截止时间：2099-12-31 09:00。" + ("正文内容" * 10000) + "TAIL-ONE"
    records = [
        _record(1, full_body, project_key="k1", notice_number="A-1"),
        _record(2, "本批次与关联公告需核实。", project_key="k2", notice_number="B-2", related_record_id=1),
    ]
    source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    provider = ProgressProvider()
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=provider)
    try:
        asyncio.run(service.import_file())
        service.update_settings({
            "business": {"capabilities": "可供应破碎机"},
            "ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                   "api_key": "secret-value"},
        })
        job_id = service.queue_project(2)["job_id"]
        asyncio.run(service.run_job(job_id))
        assert provider.context is not None
        related_record = provider.context["related_projects"][0]["records"][0]
        assert len(related_record["body_text"]) < 31_000
        assert "[正文已截断" in related_record["body_text"]
        assert "TAIL-ONE" not in related_record["body_text"]
        assert related_record["body_text_sha256"] == sha256_text(full_body)
        assert len(json_text(provider.context).encode("utf-8")) < 4 * 1024 * 1024
        old_fingerprint = service.project_detail(2)["judgment"]["input_fingerprint"]

        records[0]["body_text"] = full_body[:-8] + "TAIL-TWO"
        source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
        asyncio.run(service.import_file())
        assert service.summary(2)["ai_status"] == "queued"
        assert service.project_detail(2)["judgment_history"][0]["stale"] is True
        current_project = service.project_detail(2)
        current_context, current_related = service._analysis_context(2, service._section("business"))
        current_fingerprint = service._analysis_fingerprint(
            current_project, service._section("business"), service._ai_config(), current_related
        )
        assert current_fingerprint != old_fingerprint
        assert current_context["related_projects"][0]["records"][0]["body_text_sha256"] == sha256_text(records[0]["body_text"])
    finally:
        service.close()


def test_truncation_marker_cannot_be_used_as_related_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCREENING_AI_ENGINE", raising=False)
    source = tmp_path / "records.jsonl"
    full_body = "采购破碎机，截止时间：2099-12-31 09:00。" + ("正文内容" * 10000) + "TAIL"
    records = [
        _record(1, full_body, project_key="k1", notice_number="A-1"),
        _record(2, "本批次与关联公告需核实。", project_key="k2", notice_number="B-2", related_record_id=1),
    ]
    source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    marker = "\n[正文已截断，不能引用截断部分以外的内容]"
    service = ScreeningService(
        db_path=tmp_path / "screening.sqlite3", source_path=source, archive_root=tmp_path,
        provider=ProgressProvider(quote=marker),
    )
    try:
        asyncio.run(service.import_file())
        service.update_settings({
            "business": {"capabilities": "可供应破碎机"},
            "ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                   "api_key": "secret-value"},
        })
        job_id = service.queue_project(2)["job_id"]
        asyncio.run(service.run_job(job_id))
        assert service.summary(2)["ai_status"] == "failed"
        assert service.project_detail(2)["judgment"] is None
        assert "evidence quote does not match source record" in (service.db.execute(
            "SELECT error FROM jobs WHERE id=?", (job_id,)
        ).fetchone()[0] or "")
    finally:
        service.close()


def test_settings_engine_and_sdk_ready_are_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCREENING_AI_ENGINE", raising=False)
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(_record(1, "采购破碎机。", project_key="k1", notice_number="A-1"), ensure_ascii=False), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=ProgressProvider())
    try:
        before = service.get_settings()["ai"]
        before_section = service._section("ai")
        service.update_settings({"ai": {"engine": "single_call", "sdk_ready": not before["sdk_ready"]}})
        after = service.get_settings()["ai"]
        assert after["engine"] == "pi_sdk"
        assert after["sdk_ready"] == before["sdk_ready"]
        assert service._section("ai") == before_section
    finally:
        service.close()


def test_runtime_completed_event_is_not_public_until_service_validation():
    assert _safe_progress_event({"type": "progress", "stage": "completed", "label": "untrusted"}) is None
