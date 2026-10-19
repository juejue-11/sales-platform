from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.analysis.errors import AIError
from backend.analysis.prompts import MAX_CONTEXT_BODY_CHARS, build_messages, correction_evidence
from backend.analysis.runtime_bridge import PiSDKProvider
from backend.analysis.validation import validate_judgment
from backend.analysis.provider_finalizer import _formatter_user_data
from backend.core.citations import citation_fragments
from backend.documents.document_access import build_document_view
from backend.tests.test_agent_report import project, report
from backend.tests.test_backend import make_service, row


def test_record_fragment_fills_original_quote_without_model_punctuation() -> None:
    body = "8.9 投标有效期：\n90\n天\n8.10 其他规则：\n详见附件"
    source = project(body=body)
    record = json.loads(build_messages(source, {})[1]["content"])["project"]["records"][0]
    fragment = record["evidence_fragments"][0]
    assert "body_text" not in record  # The prompt carries each source only once.
    result = validate_judgment({
        "decision": "consider", "summary": "投标有效期90天",
        "report": report(evidence=[{"record_id": 1, "fragment_id": fragment["fragment_id"],
                                     "quote": "90天。"}]),
    }, source, "mock", "fp", require_report=True)
    evidence = result["report"]["sections"][0]["blocks"][0]["evidence"][0]
    assert evidence == {"record_id": 1, "quote": body}


@pytest.mark.parametrize("body,fragment", [
    ("采购2台破碎机。", "采购3台破碎机。"),
    ("可见正文" * MAX_CONTEXT_BODY_CHARS + "未提供的尾部", "未提供的尾部"),
], ids=["changed-number", "unseen-tail"])
def test_fragment_cannot_change_numbers_or_reference_unseen_source(body: str, fragment: str) -> None:
    with pytest.raises(AIError, match="fragment was not read"):
        validate_judgment({"decision": "verify", "summary": "待核实", "report": report(evidence=[{
            "record_id": 1, "fragment_id": citation_fragments(fragment)[0]["fragment_id"],
        }])}, project(body=body), "mock", "fp")


def test_document_fragment_is_resolved_only_within_read_window() -> None:
    document = {"id": "doc1", "record_id": 1, "status": "parsed", "name": "报价要求.pdf",
                "text": "完整正文，含本轮未读取的其他条款", "pages": 1,
                "page_texts": [{"page": 1, "text": "报价有效期：\n90天"}]}
    returned = build_document_view([document], {"record_id": 1, "document_id": "doc1", "page": 1})
    fragment = returned["documents"][0]["evidence_fragments"][0]
    evidence = {"record_id": 1, "document_id": "doc1", "fragment_id": fragment["fragment_id"]}
    trusted = {**document, "read_fragments": [{"page": 1, "text": fragment["text"]}]}
    result = validate_judgment({"decision": "consider", "summary": "关注", "report": report(evidence=[evidence])},
                               project(), "mock", "fp", document_sources=[trusted])
    citation = result["report"]["sections"][0]["blocks"][0]["evidence"][0]
    assert citation["quote"] == fragment["text"]
    assert citation["page"] == 1
    evidence["fragment_id"] = citation_fragments(document["text"])[0]["fragment_id"]
    with pytest.raises(AIError, match="fragment was not read"):
        validate_judgment({"decision": "verify", "summary": "待核实", "report": report(evidence=[evidence])},
                          project(), "mock", "fp", document_sources=[trusted])


def test_research_fragment_keeps_relation_and_version_checks() -> None:
    body = "本批次采购破碎机2台，项目编号A-001。"
    source = {"source_ref": "research_1", "source_version": "v1", "project_id": 1,
              "relation": "same_procurement", "relation_status": "verified",
              "page_texts": [{"page": 2, "text": body}]}
    citation = {"source_ref": "research_1", "source_version": "v1",
                "fragment_id": citation_fragments(body)[0]["fragment_id"]}
    payload = {"decision": "consider", "summary": "关注", "report": report(evidence=[citation])}
    result = validate_judgment(payload, project(), "mock", "fp", research_sources=[source])
    evidence = result["report"]["sections"][0]["blocks"][0]["evidence"][0]
    assert evidence["quote"] == body and evidence["page"] == 2
    with pytest.raises(AIError, match="relation is not verified"):
        validate_judgment(payload, project(), "mock", "fp",
                          research_sources=[{**source, "relation_status": "unconfirmed"}])


def test_resolved_source_fragment_cannot_introduce_a_restricted_credential() -> None:
    body = "原文意外包含 secret-value，不允许写入分析结果。"
    with pytest.raises(AIError, match="restricted credential"):
        validate_judgment({"decision": "verify", "summary": "待核实", "report": report(evidence=[{
            "record_id": 1, "fragment_id": citation_fragments(body)[0]["fragment_id"],
        }])}, project(body=body), "mock", "fp", forbidden_values=["secret-value"])


def test_output_correction_reuses_evidence_without_new_tool_calls(tmp_path: Path) -> None:
    class Agent:
        def __init__(self) -> None:
            self.requests = []

        async def analyze(self, *, messages, limits, tool_callback, **kwargs):
            self.requests.append({"messages": messages, "limits": limits})
            if len(self.requests) == 1:
                await tool_callback({"record_id": 1})
                evidence = [{"record_id": 1, "quote": "不存在的引用"}]
            else:
                evidence = [{"record_id": 1, "fragment_id": citation_fragments(row()["body_text"])[0]["fragment_id"]}]
            return {"content": json.dumps({"decision": "consider", "summary": "关注",
                                           "report": report(evidence=evidence)}), "usage": {}}

    agent = Agent()
    service = make_service(tmp_path, [row()], agent)
    try:
        service.update_settings({"ai": {"base_url": "https://mock.local", "model": "mock", "enabled": True,
                                         "api_key": "secret-value"}})
        job_id = service.queue_project(1)["job_id"]
        asyncio.run(service.run_job(job_id))
        assert service.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "succeeded"
        assert len(agent.requests) == 2
        assert agent.requests[1]["limits"]["max_tool_calls"] == 0
        assert agent.requests[1]["limits"]["max_turns"] == 1
        prior = json.loads(agent.requests[1]["messages"][-2]["content"])
        assert "available_evidence" in prior
    finally:
        service.close()


def test_zero_tool_budget_survives_python_runtime_boundary() -> None:
    assert PiSDKProvider._normalized_limits({"max_tool_calls": 0})["max_tool_calls"] == 0


def test_both_correction_paths_preserve_fragment_catalogs_and_read_boundaries() -> None:
    evidence = {
        "documents": [{"id": "doc1", "record_id": 1, "read_fragments": [{"page": 2, "text": "已读附件条款"}]}],
        "research_sources": [{"source_ref": "research1", "source_version": "v1",
                              "relation_status": "verified", "page_texts": [{"page": 3, "text": "已核实条款"}]}],
    }
    expected = correction_evidence(evidence)
    actual = json.loads(_formatter_user_data([], "draft", evidence))["available_evidence"]
    assert actual == expected
    for key, text, page in (("documents", "已读附件条款", 2), ("research_sources", "已核实条款", 3)):
        assert actual[key][0]["evidence_fragments"] == [{**citation_fragments(text)[0], "page": page}]
        assert "evidence_fragments" not in evidence[key][0]
