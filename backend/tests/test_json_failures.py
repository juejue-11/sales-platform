import asyncio
import json
import shutil
import textwrap
from pathlib import Path

import httpx
import pytest

import backend.application.service as service_module
from backend.analysis.ai import (AIError, PiSDKProvider, _safe_error_code, _safe_error_metadata,
                                 build_messages, correction_messages, validate_judgment)
from backend.analysis.provider_finalizer import (
    StrictFinalizerResult,
    _YamlOutputError,
    _yaml_to_json,
    deepseek_formatter_endpoint,
    strict_format_report,
)
from backend.application.service import ScreeningService


NODE = shutil.which("node")

VALID_YAML = r"""---
decision: |-
  consider
summary: |-
  summary "quoted" \ slash
demand_summary: |-
  demand
analysis_meta:
  company_match: |-
    not_evaluated
report:
  version: 1
  sections:
    - id: |-
        section-1
      title: |-
        title
      blocks:
        - type: |-
            paragraph
          text: |-
            line "quoted" \ slash
            next line
          evidence:
            - record_id: 1
              quote: |-
                line "quoted" \ slash
                next line
  opportunities: []
  gaps: []
options: []
...
"""


def _bridge_args(model: str) -> dict:
    return {
        "base_url": "https://mock.local/v1",
        "model": model,
        "api_key": "secret-value",
        "messages": [{"role": "user", "content": "u"}],
        "context": {"project": {}, "company_capability_card": {}, "related_projects": []},
    }


def _runtime_error_runner() -> str:
    return textwrap.dedent(
        r"""
        const chunks = [];
        for await (const chunk of process.stdin) chunks.push(chunk);
        const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
        emit({
          type: "error",
          code: request.config.model,
          message: "Expecting value at raw provider payload secret-value",
          metadata: {
            position: 17,
            line: 1,
            column: 18,
            eof: true,
            diagnostic_path: "diagnostics/run-123.json",
            raw_response: "must-not-cross-the-runtime-boundary"
          }
        });
        process.exit(0);
        """
    )


def _runtime_bad_result_runner() -> str:
    return textwrap.dedent(
        r"""
        let buffered = "";
        process.stdin.on("data", (chunk) => {
          buffered += chunk.toString("utf8");
          if (!buffered.includes("\n")) return;
          JSON.parse(buffered.trim());
          process.stdin.removeAllListeners("data");
          process.stdin.pause();
          process.stdout.write(JSON.stringify({
            type: "result",
            engine: "pi_sdk",
            content: "{\"report\":" ,
            usage: {model_calls: 1, tool_calls: 0, input_tokens: 1, output_tokens: 1}
          }) + "\n", () => process.exit(0));
        });
        """
    )


def _runtime_empty_result_runner(finish_reason: str | None) -> str:
    payload = {
        "type": "result", "engine": "pi_sdk", "content": " \t ",
        "finish_reason": finish_reason,
        "usage": {"model_calls": 1, "tool_calls": 0, "input_tokens": 1, "output_tokens": 2},
    }
    return ("process.stdin.once('data', () => process.stdout.write("
            + json.dumps(json.dumps(payload)) + " + '\\n', () => process.exit(0)));")


@pytest.mark.parametrize("finish_reason", ["stop", None, "length"])
@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_empty_runtime_draft_requires_confirmed_normal_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish_reason: str | None
):
    runner = tmp_path / "empty-result.mjs"
    runner.write_text(_runtime_empty_result_runner(finish_reason), encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")
    if finish_reason != "stop":
        with pytest.raises(AIError, match="结果协议无效"):
            asyncio.run(PiSDKProvider().analyze(**_bridge_args("test")))
        return
    result = asyncio.run(PiSDKProvider().analyze(**_bridge_args("test")))
    assert result.content == ""
    assert result.usage["model_calls"] == 1
    with pytest.raises(AIError) as caught:
        validate_judgment(result.content, {}, "test", "fingerprint")
    assert caught.value.code == "MODEL_FORMAT_ERROR"


def _runtime_semantic_then_malformed_runner() -> str:
    return textwrap.dedent(
        r'''
        import { readFileSync, writeFileSync } from "node:fs";
        let buffered = "";
        process.stdin.on("data", (chunk) => {
          buffered += chunk.toString("utf8");
          if (!buffered.includes("\n")) return;
          JSON.parse(buffered.trim());
          const statePath = process.env.SCREENING_SEQUENCE_STATE;
          let count = 0;
          try {
            count = Number.parseInt(readFileSync(statePath, "utf8"), 10) || 0;
          } catch (_) {}
          count += 1;
          writeFileSync(statePath, String(count), "utf8");
          const content = count === 1
            ? JSON.stringify({decision: "consider", summary: "semantic", demand_summary: "demand"})
            : (process.env.SCREENING_SECOND_RESPONSE === "semantic"
              ? JSON.stringify({decision: "consider", summary: "semantic", demand_summary: "demand"})
              : "{\"report\":");
          process.stdout.write(JSON.stringify({
            type: "result",
            engine: "pi_sdk",
            content,
            usage: {model_calls: 1, tool_calls: 0, input_tokens: 11, output_tokens: 17}
          }) + "\n", () => process.exit(0));
        });
        '''
    )


def _runtime_malformed_then_second_runner() -> str:
    return textwrap.dedent(
        r'''
        import { readFileSync, writeFileSync } from "node:fs";
        let buffered = "";
        process.stdin.on("data", (chunk) => {
          buffered += chunk.toString("utf8");
          if (!buffered.includes("\n")) return;
          JSON.parse(buffered.trim());
          const source = "source project body with concrete deadline 2099-12-31";
          const valid = {
            decision: "consider",
            summary: "summary",
            demand_summary: "demand",
            analysis_meta: {company_match: "not_evaluated"},
            report: {
              version: 1,
              sections: [{id: "s", title: "Facts", blocks: [{
                type: "paragraph", text: source,
                evidence: [{record_id: 1, quote: source}]
              }]}],
              opportunities: [], gaps: []
            },
            options: []
          };
          const countPath = process.env.SCREENING_SEQUENCE_STATE;
          let count = 0;
          try { count = Number.parseInt(readFileSync(countPath, "utf8"), 10) || 0; } catch (_) {}
          count += 1;
          writeFileSync(countPath, String(count), "utf8");
          const content = count === 1 || process.env.SCREENING_SECOND_RESPONSE === "malformed"
            ? "{\"report\":"
            : JSON.stringify(valid);
          process.stdout.write(JSON.stringify({
            type: "result", engine: "pi_sdk", content,
            usage: {model_calls: 1, tool_calls: 0, input_tokens: 11, output_tokens: 17}
          }) + "\n", () => process.exit(0));
        });
        '''
    )


@pytest.mark.parametrize(
    "code",
    [
        "TOOL_ARGUMENT_JSON",
        "TOOL_SCHEMA",
        "TOOL_PARSER_ERROR",
        "STREAM_INTERRUPTED",
        "MODEL_FORMAT_ERROR",
    ],
)
@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_pi_sdk_preserves_classified_runtime_error_and_safe_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
):
    runner = tmp_path / "runtime-error.mjs"
    runner.write_text(_runtime_error_runner(), encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")

    with pytest.raises(AIError) as caught:
        asyncio.run(PiSDKProvider().analyze(**_bridge_args(code)))

    error = caught.value
    assert error.code == code
    assert error.metadata["position"] == 17
    assert error.metadata["line"] == 1
    assert error.metadata["column"] == 18
    assert error.metadata["eof"] is True
    assert error.metadata["diagnostic_path"] == "diagnostics/run-123.json"
    assert "raw_response" not in error.metadata
    assert "secret-value" not in str(error)
    assert "Expecting" not in str(error)


def test_pi_sdk_keeps_legacy_runtime_error_codes_allowlisted():
    assert _safe_error_code("MODEL_ERROR") == "MODEL_ERROR"
    assert _safe_error_code("OUTPUT_TOKEN_LIMIT") == "OUTPUT_TOKEN_LIMIT"
    assert _safe_error_code("TOOL_ERROR") == "TOOL_ERROR"
    assert _safe_error_code("old-provider-detail") == "RUNTIME_ERROR"


def test_validate_judgment_marks_json_format_error_with_position_and_eof():
    raw = '{"decision":"consider"'

    with pytest.raises(AIError) as caught:
        validate_judgment(raw, {"records": []}, "test", "fingerprint")

    error = caught.value
    assert error.code == "MODEL_FORMAT_ERROR"
    assert error.metadata["position"] == len(raw)
    assert error.metadata["eof"] is True
    assert error.metadata["line"] == 1
    assert error.metadata["column"] == len(raw) + 1
    assert "Expecting" not in str(error)


def test_validate_judgment_marks_non_eof_json_format_error():
    raw = '{"decision":,}'

    with pytest.raises(AIError) as caught:
        validate_judgment(raw, {"records": []}, "test", "fingerprint")

    error = caught.value
    assert error.code == "MODEL_FORMAT_ERROR"
    assert error.metadata["position"] == raw.index(",")
    assert error.metadata["eof"] is False


def test_ai_error_holds_code_and_metadata_without_payload():
    error = AIError("format error", code="MODEL_FORMAT_ERROR", metadata={"position": 3, "eof": True})
    assert error.code == "MODEL_FORMAT_ERROR"
    assert error.metadata == {"position": 3, "eof": True}
    assert "format error" in str(error)


def test_diagnostic_path_preserves_chinese_local_directory():
    path = "C:\\Users\\cgr\\Desktop\\new\\\u9500\u552e\u5e73\u53f0\u7b5b\u9009\u7cfb\u7edf\\data\\ai-traces\\strict-finalizer.json"
    metadata = _safe_error_metadata({"diagnostic_path": path, "raw_response": "secret"})
    assert metadata["diagnostic_path"] == path
    assert "raw_response" not in metadata


def test_yaml_reader_preserves_quote_backslash_and_newline():
    value = json.loads(_yaml_to_json(VALID_YAML))
    quote = value["report"]["sections"][0]["blocks"][0]["evidence"][0]["quote"]
    assert quote == 'line "quoted" \\ slash\nnext line'
    assert value["summary"] == 'summary "quoted" \\ slash'


def test_yaml_reader_rejects_duplicate_keys_aliases_tags_and_multiple_documents():
    invalid_documents = [
        "---\na: |-\n  one\na: |-\n  two\n...\n",
        "---\na: &value |-\n  one\nb: *value\n...\n",
        "---\na: !custom |-\n  one\n...\n",
        "---\na: |-\n  one\n...\n---\nb: |-\n  two\n...\n",
        "---\n1: |-\n  one\n...\n",
    ]
    for document in invalid_documents:
        with pytest.raises(_YamlOutputError):
            _yaml_to_json(document)


def _formatter_response(content: str, finish_reason: str | None = "stop", *, usage: dict | None = None) -> dict:
    choice: dict = {"message": {"content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice], "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7}}


def test_yaml_formatter_uses_regular_nonstream_chat_without_tools():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request, json.loads(request.content)))
        return httpx.Response(200, json=_formatter_response(VALID_YAML))

    result = asyncio.run(strict_format_report(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        api_key="secret-value",
        messages=[{"role": "system", "content": "old JSON schema"}],
        previous_output='{"report":',
        available_evidence={"documents": [{"record_id": 1, "read_fragments": []}]},
        transport=httpx.MockTransport(handler),
    ))

    assert isinstance(result, StrictFinalizerResult)
    parsed = json.loads(result.content)
    assert parsed["report"]["sections"][0]["blocks"][0]["evidence"][0]["quote"] == (
        'line "quoted" \\ slash\nnext line'
    )
    assert result.usage == {"model_calls": 1, "tool_calls": 0, "input_tokens": 11, "output_tokens": 7}
    request, payload = calls[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False
    assert payload["max_tokens"] == 16_384
    assert "tools" not in payload
    assert "response_format" not in payload
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert payload["messages"][0]["content"].startswith("You are a serialization formatter")
    repair_input = json.loads(payload["messages"][1]["content"])
    assert repair_input["previous_output"] == '{"report":'
    assert repair_input["original_messages"][0]["content"] == "old JSON schema"
    assert repair_input["available_evidence"]["documents"][0]["read_fragments"] == []


def test_yaml_formatter_missing_document_end_is_model_format_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCREENING_AI_TRACE_DIR", str(tmp_path))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_formatter_response("---\ndecision: |-\n  consider\n"))

    with pytest.raises(AIError) as caught:
        asyncio.run(strict_format_report(
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            api_key="secret-value",
            messages=[], previous_output="bad", available_evidence={},
            transport=httpx.MockTransport(handler),
        ))
    error = caught.value
    assert error.code == "MODEL_FORMAT_ERROR"
    assert error.metadata["eof"] is True
    trace = json.loads(Path(error.metadata["diagnostic_path"]).read_text(encoding="utf-8"))
    assert trace["raw_output"].startswith("---")
    assert "..." not in trace["raw_output"]


@pytest.mark.parametrize(
    ("finish_reason", "expected_code"),
    [(None, "STREAM_INTERRUPTED"), ("length", "OUTPUT_TOKEN_LIMIT"), ("tool_calls", "MODEL_FORMAT_ERROR")],
)
def test_yaml_formatter_classifies_finish_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish_reason: str | None, expected_code: str
):
    monkeypatch.setenv("SCREENING_AI_TRACE_DIR", str(tmp_path))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_formatter_response(VALID_YAML, finish_reason))

    with pytest.raises(AIError) as caught:
        asyncio.run(strict_format_report(
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            api_key="secret-value",
            messages=[], previous_output="bad", available_evidence={},
            transport=httpx.MockTransport(handler),
        ))
    error = caught.value
    assert error.code == expected_code
    assert "diagnostic_path" in error.metadata
    trace = Path(error.metadata["diagnostic_path"]).read_text(encoding="utf-8")
    assert "secret-value" not in trace
    assert "thinking" not in trace.lower()
    if finish_reason is not None:
        assert error.metadata["finish_reason"] == finish_reason


def test_yaml_formatter_trace_keeps_bounded_head_tail_and_usage_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SCREENING_AI_TRACE_DIR", str(tmp_path))
    output = "---\nsummary: |-\n  " + ("a" * 140_000)
    usages = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_formatter_response(
            output, usage={"prompt_tokens": 31, "completion_tokens": 47}
        ))

    with pytest.raises(AIError) as caught:
        asyncio.run(strict_format_report(
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            api_key="secret-value",
            messages=[], previous_output="bad", available_evidence={},
            on_usage=usages.append,
            transport=httpx.MockTransport(handler),
        ))
    assert caught.value.code == "MODEL_FORMAT_ERROR"
    assert usages == [{"model_calls": 1, "tool_calls": 0, "input_tokens": 31, "output_tokens": 47}]
    trace = json.loads(Path(caught.value.metadata["diagnostic_path"]).read_text(encoding="utf-8"))
    assert trace["raw_output_meta"]["truncated"] is True
    assert "[trace truncated]" in trace["raw_output"]
    assert trace["raw_output_meta"]["original_length"] == len(output)


def test_yaml_formatter_endpoint_only_allows_official_deepseek_host():
    assert deepseek_formatter_endpoint("https://api.deepseek.com/v1") == "https://api.deepseek.com/chat/completions"
    assert deepseek_formatter_endpoint("https://mock.local/v1") is None


def _new_service_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = tmp_path / "bad-result.mjs"
    runner.write_text(_runtime_bad_result_runner(), encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_NODE_PATH", NODE or "")
    source = "source project body with concrete deadline 2099-12-31"
    record = {
        "id": 1, "source_id": "test", "kind": "tender", "title": "Project",
        "project_name": "Project", "buyer": "Buyer", "body_text": source,
        "equipment": ["equipment"], "facts": [], "inferences": [],
        "published_at": "2026-10-08", "deadline_at": "2099-12-31 09:00",
        "archive_path": "archive/1/v1.html", "current_version": 1,
    }
    export = tmp_path / "records.jsonl"
    export.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=export,
                               archive_root=tmp_path)
    asyncio.run(service.import_file())
    service.update_settings({"ai": {"base_url": "https://api.deepseek.com/v1",
                                      "model": "deepseek-flash", "enabled": True,
                                      "api_key": "secret-value"}})
    return service, service.queue_project(1)["job_id"], source


def _valid_final_payload(source: str, *, quote: str | None = None) -> dict:
    return {
        "decision": "consider", "summary": "summary", "demand_summary": "demand",
        "analysis_meta": {"company_match": "not_evaluated"},
        "report": {"version": 1, "sections": [{"id": "s", "title": "Facts", "blocks": [{
            "type": "paragraph", "text": source,
            "evidence": [{"record_id": 1, "quote": source if quote is None else quote}],
        }]}], "opportunities": [], "gaps": []},
        "options": [],
    }


@pytest.mark.parametrize("empty_draft", [False, True])
@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_service_uses_one_yaml_finalizer_without_restarting_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty_draft: bool
):
    service, job_id, source = _new_service_case(tmp_path, monkeypatch)
    if empty_draft:
        (tmp_path / "bad-result.mjs").write_text(_runtime_empty_result_runner("stop"), encoding="utf-8")
    final_payload = {
        "decision": "consider", "summary": "summary", "demand_summary": "demand",
        "analysis_meta": {"company_match": "not_evaluated"},
        "report": {"version": 1, "sections": [{"id": "s", "title": "Facts", "blocks": [{
            "type": "paragraph", "text": source, "evidence": [{"record_id": 1, "quote": source}],
        }]}], "opportunities": [], "gaps": []},
        "options": [],
    }
    finalizer_calls = []

    async def fake_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        return StrictFinalizerResult(
            content=json.dumps(final_payload, ensure_ascii=False),
            usage={"model_calls": 1, "tool_calls": 0, "input_tokens": 2, "output_tokens": 3},
        )

    monkeypatch.setattr(service_module, "strict_format_report", fake_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert (job["status"], job["attempts"]) == ("succeeded", 2)
        assert len(finalizer_calls) == 1
        assert finalizer_calls[0]["previous_output"] == ""
        prior_input = json.loads(finalizer_calls[0]["messages"][-2]["content"])
        assert prior_input["untrusted_previous_output"] == ("" if empty_draft else '{"report":')
        assert f"JSON parse position={0 if empty_draft else 10}" in finalizer_calls[0]["messages"][-1]["content"]
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 1
    finally:
        service.close()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_service_formats_after_semantic_correction_then_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, job_id, source = _new_service_case(tmp_path, monkeypatch)
    runner = tmp_path / "semantic-then-malformed.mjs"
    runner.write_text(_runtime_semantic_then_malformed_runner(), encoding="utf-8")
    state = tmp_path / "runner-count.txt"
    state.write_text("0", encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_SEQUENCE_STATE", str(state))
    finalizer_calls = []

    async def fake_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        return StrictFinalizerResult(
            content=json.dumps(_valid_final_payload(source), ensure_ascii=False),
            usage={"model_calls": 1, "tool_calls": 0, "input_tokens": 5, "output_tokens": 7},
        )

    monkeypatch.setattr(service_module, "strict_format_report", fake_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        usage_row = service.db.execute("SELECT usage_json FROM agent_runs WHERE job_id=?", (job_id,)).fetchone()
        usage = json.loads(usage_row["usage_json"])
        assert (job["status"], job["attempts"]) == ("succeeded", 3)
        assert state.read_text(encoding="utf-8") == "2"
        assert len(finalizer_calls) == 1
        assert usage == {"model_calls": 3, "tool_calls": 0, "input_tokens": 27, "output_tokens": 41}
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 1
    finally:
        service.close()


@pytest.mark.parametrize("finalizer_result", ["malformed", "invalid_evidence"])
@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_service_does_not_store_when_format_repair_fails_or_evidence_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finalizer_result: str
):
    service, job_id, source = _new_service_case(tmp_path, monkeypatch)
    runner = tmp_path / "semantic-then-malformed.mjs"
    runner.write_text(_runtime_semantic_then_malformed_runner(), encoding="utf-8")
    state = tmp_path / "runner-count.txt"
    state.write_text("0", encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_SEQUENCE_STATE", str(state))
    finalizer_calls = []
    finalizer_usage = {"model_calls": 1, "tool_calls": 0, "input_tokens": 5, "output_tokens": 7}

    async def fake_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        if finalizer_result == "malformed":
            kwargs["on_usage"](finalizer_usage)
            raise AIError("formatter returned malformed JSON", code="MODEL_FORMAT_ERROR")
        return StrictFinalizerResult(
            content=json.dumps(_valid_final_payload(source, quote="not in source"), ensure_ascii=False),
            usage=finalizer_usage,
        )

    monkeypatch.setattr(service_module, "strict_format_report", fake_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        usage_row = service.db.execute("SELECT usage_json FROM agent_runs WHERE job_id=?", (job_id,)).fetchone()
        usage = json.loads(usage_row["usage_json"])
        assert (job["status"], job["attempts"]) == ("failed", 3)
        assert state.read_text(encoding="utf-8") == "2"
        assert len(finalizer_calls) == 1
        assert usage == {"model_calls": 3, "tool_calls": 0, "input_tokens": 27, "output_tokens": 41}
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 0
    finally:
        service.close()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_service_allows_semantic_correction_after_format_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, job_id, source = _new_service_case(tmp_path, monkeypatch)
    runner = tmp_path / "malformed-then-valid.mjs"
    runner.write_text(_runtime_malformed_then_second_runner(), encoding="utf-8")
    state = tmp_path / "runner-count.txt"
    state.write_text("0", encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_SEQUENCE_STATE", str(state))
    finalizer_calls = []

    async def fake_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        return StrictFinalizerResult(
            content=json.dumps(_valid_final_payload(source, quote="not in source"), ensure_ascii=False),
            usage={"model_calls": 1, "tool_calls": 0, "input_tokens": 5, "output_tokens": 7},
        )

    monkeypatch.setattr(service_module, "strict_format_report", fake_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        usage_row = service.db.execute("SELECT usage_json FROM agent_runs WHERE job_id=?", (job_id,)).fetchone()
        usage = json.loads(usage_row["usage_json"])
        assert (job["status"], job["attempts"]) == ("succeeded", 3)
        assert state.read_text(encoding="utf-8") == "2"
        assert len(finalizer_calls) == 1
        assert usage == {"model_calls": 3, "tool_calls": 0, "input_tokens": 27, "output_tokens": 41}
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 1
    finally:
        service.close()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_service_does_not_repeat_format_repair_after_semantic_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, job_id, source = _new_service_case(tmp_path, monkeypatch)
    runner = tmp_path / "malformed-then-malformed.mjs"
    runner.write_text(_runtime_malformed_then_second_runner(), encoding="utf-8")
    state = tmp_path / "runner-count.txt"
    state.write_text("0", encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_SEQUENCE_STATE", str(state))
    monkeypatch.setenv("SCREENING_SECOND_RESPONSE", "malformed")
    finalizer_calls = []
    finalizer_usage = {"model_calls": 1, "tool_calls": 0, "input_tokens": 5, "output_tokens": 7}

    async def fake_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        return StrictFinalizerResult(
            content=json.dumps(_valid_final_payload(source, quote="not in source"), ensure_ascii=False),
            usage=finalizer_usage,
        )

    monkeypatch.setattr(service_module, "strict_format_report", fake_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        usage_row = service.db.execute("SELECT usage_json FROM agent_runs WHERE job_id=?", (job_id,)).fetchone()
        usage = json.loads(usage_row["usage_json"])
        assert (job["status"], job["attempts"]) == ("failed", 3)
        assert state.read_text(encoding="utf-8") == "2"
        assert len(finalizer_calls) == 1
        assert usage == {"model_calls": 3, "tool_calls": 0, "input_tokens": 27, "output_tokens": 41}
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 0
    finally:
        service.close()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_service_keeps_non_format_correction_failure_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, job_id, _ = _new_service_case(tmp_path, monkeypatch)
    runner = tmp_path / "semantic-twice.mjs"
    runner.write_text(_runtime_semantic_then_malformed_runner(), encoding="utf-8")
    state = tmp_path / "runner-count.txt"
    state.write_text("0", encoding="utf-8")
    monkeypatch.setenv("SCREENING_AGENT_RUNNER", str(runner))
    monkeypatch.setenv("SCREENING_SEQUENCE_STATE", str(state))
    monkeypatch.setenv("SCREENING_SECOND_RESPONSE", "semantic")
    finalizer_calls = []

    async def fake_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        raise AssertionError("semantic correction must not invoke the formatter")

    monkeypatch.setattr(service_module, "strict_format_report", fake_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        usage_row = service.db.execute("SELECT usage_json FROM agent_runs WHERE job_id=?", (job_id,)).fetchone()
        usage = json.loads(usage_row["usage_json"])
        assert (job["status"], job["attempts"]) == ("failed", 2)
        assert state.read_text(encoding="utf-8") == "2"
        assert finalizer_calls == []
        assert usage == {"model_calls": 2, "tool_calls": 0, "input_tokens": 22, "output_tokens": 34}
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 0
    finally:
        service.close()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the subprocess protocol test")
def test_yaml_finalizer_failure_does_not_store_judgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, job_id, _ = _new_service_case(tmp_path, monkeypatch)

    async def failing_finalizer(**kwargs):
        raise AIError("YAML document end marker is missing", code="STREAM_INTERRUPTED")

    monkeypatch.setattr(service_module, "strict_format_report", failing_finalizer)
    try:
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert (job["status"], job["attempts"]) == ("failed", 2)
        assert service.db.execute("SELECT count(*) FROM judgments").fetchone()[0] == 0
    finally:
        service.close()


def test_report_prompts_explicitly_require_json_string_escaping():
    messages = build_messages({"id": 1, "records": []}, {}, [])
    correction = correction_messages(messages, AIError("format error"), previous_output="bad")
    prompt = messages[0]["content"] + correction[-1]["content"]
    assert r'\"' in prompt
    assert r'\\' in prompt
    assert r'\n' in prompt
    assert "after JSON decoding" in prompt
