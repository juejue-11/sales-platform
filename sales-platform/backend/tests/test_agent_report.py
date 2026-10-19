from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from backend.analysis.errors import AIError
from backend.analysis.prompts import build_messages
from backend.analysis.validation import validate_judgment
from backend.storage.db import connect
from backend.application.agent_runs import AgentRuns
from backend.application.service import ScreeningService
from backend.core.utils import source_quote, source_quote_hint
from backend.tests.test_backend import Provider


def project(*, body: str = "采购破碎机，交付至北京。", kind: str = "tender") -> dict:
    return {
        "id": 1,
        "kind": kind,
        "records": [{"id": 1, "kind": kind, "body_text": body}],
    }


def report(*, evidence: list[dict] | None = None, opportunity: dict | None = None) -> dict:
    evidence = evidence or [{"record_id": 1, "quote": "采购破碎机"}]
    return {
        "version": 1,
        "sections": [{"id": "facts", "title": "项目事实", "blocks": [
            {"type": "paragraph", "text": "公告明确采购破碎机。", "evidence": evidence},
        ]}],
        "opportunities": [opportunity] if opportunity else [],
        "gaps": [{"id": "delivery", "description": "详细技术要求尚未说明", "status": "not_stated",
                   "impact": "影响型号核对", "next_action": "核实技术文件", "worth_researching": True}],
    }


def test_report_roundtrip_derives_legacy_fields() -> None:
    value = validate_judgment({
        "decision": "consider", "summary": "有条件关注",
        "analysis_meta": {"company_match": "not_evaluated"}, "report": report(),
    }, project(), "mock", "fp")

    assert value["report"]["version"] == 1
    assert value["demand_summary"] == "有条件关注"
    assert value["reasons"][0]["evidence"][0]["quote"] == "采购破碎机"
    assert "详细技术要求尚未说明（原文未说明）" in value["unknowns"]
    assert "核实技术文件" in value["next_actions"]


@pytest.mark.parametrize("version", ["1", 1.0, True, None, 2])
def test_report_version_requires_the_exact_integer_protocol(version) -> None:
    value = report()
    value["version"] = version
    with pytest.raises(AIError, match="report.version must be integer 1") as caught:
        validate_judgment({"decision": "verify", "summary": "待核实", "report": value}, project(), "mock", "fp")
    assert caught.value.code == "MODEL_FORMAT_ERROR"


def test_initial_model_input_keeps_long_bill_of_materials_behind_document_tool() -> None:
    value = project()
    value["procurement"] = {
        "status": "complete", "scope_summary": "143项钢材，详细规格待按需读取",
        "items": [{"name": f"钢材{i}", "quote": "长清单原文" * 200} for i in range(143)],
        "documents": [{"id": "long-doc", "record_id": 1, "name": "物料清单.pdf",
                       "pages": 36, "status": "parsed", "text": "长文" * 100000}],
    }
    original = json.dumps(value, ensure_ascii=False)
    messages = build_messages(value, {})
    assert len(messages[1]["content"]) < 12000
    aggregate = json.loads(messages[1]["content"])["project"]["procurement"]
    assert aggregate["item_count"] == 143
    assert "items" not in aggregate and "text" not in aggregate["documents"][0]
    assert json.dumps(value, ensure_ascii=False) == original


def test_new_generation_requires_report_while_legacy_validation_remains_available() -> None:
    legacy = Provider().payload
    current_project = project(body="采购破碎机")
    assert validate_judgment(legacy, current_project, "mock", "fp")["report"] is None
    with pytest.raises(AIError, match="versioned report"):
        validate_judgment(legacy, current_project, "mock", "fp", require_report=True)


def test_report_rejects_untrusted_quote_and_duplicate_ids() -> None:
    bad_quote = report(evidence=[{"record_id": 1, "quote": "模型看不到的内容"}])
    with pytest.raises(AIError, match="quote"):
        validate_judgment({"decision": "verify", "summary": "待核实", "demand_summary": "待核实",
                           "report": bad_quote}, project(), "mock", "fp")

    duplicate = report()
    duplicate["opportunities"] = [{"id": "facts", "kind": "explicit_demand", "title": "需求",
                                    "description": "明确需求", "basis": "正文", "uncertainties": [],
                                    "next_actions": [], "evidence": [{"record_id": 1, "quote": "采购破碎机"}]}]
    with pytest.raises(AIError, match="duplicate"):
        validate_judgment({"decision": "verify", "summary": "待核实", "demand_summary": "待核实",
                           "report": duplicate}, project(), "mock", "fp")

    invalid_version = report()
    invalid_version["version"] = True
    with pytest.raises(AIError, match="version"):
        validate_judgment({"decision": "verify", "summary": "待核实", "demand_summary": "待核实",
                           "report": invalid_version}, project(), "mock", "fp")


def test_potential_opportunity_requires_evidence_and_verification_action() -> None:
    opportunity = {"id": "potential", "kind": "potential_opportunity", "title": "潜在线索",
                   "description": "可能存在后续设备需求", "basis": "项目建设阶段",
                   "uncertainties": [], "next_actions": [], "evidence": []}
    with pytest.raises(AIError, match="potential opportunity") as caught:
        validate_judgment({"decision": "verify", "summary": "待核实", "demand_summary": "待核实",
                           "report": report(opportunity=opportunity)}, project(), "mock", "fp")
    assert "opportunities[0].evidence" in str(caught.value)
    assert "potential" in str(caught.value)


def test_quote_correction_reports_all_bad_quotes_without_accepting_them() -> None:
    value = report(evidence=[{"record_id": 1, "quote": "不存在的报价"},
                             {"record_id": 1, "quote": "不存在的交付承诺"}])
    with pytest.raises(AIError) as caught:
        validate_judgment({"decision": "consider", "summary": "关注", "report": value},
                          project(), "mock", "fp")
    assert "不存在的报价" in str(caught.value)
    assert "不存在的交付承诺" in str(caught.value)


def test_layout_hint_preserves_strict_quote_validation() -> None:
    original = "投标文件递交到\n“中国煤科电子采购平台”\n（https://cg.ccteg.cn），逾期将拒绝。"
    altered = "投标文件递交到中国煤科电子采购平台https://cg.ccteg.cn逾期将拒绝"
    assert source_quote(altered, original) is None
    hint = source_quote_hint(altered, original)
    assert hint and hint in original
    assert source_quote(hint, original) == hint
    assert source_quote_hint("采购32台破碎机", "采购2台破碎机") is None
    assert source_quote_hint("先采购破碎机，后采购筛分机", "先采购破碎机；不采购筛分机") is None


def test_explicit_demand_rejects_background_research_evidence() -> None:
    source = {"source_ref": "research_1", "source_version": "v1", "project_id": 1,
              "relation": "buyer_background", "relation_status": "verified",
              "title": "背景", "url": "https://example.com", "page_texts": [{"page": 1, "text": "某矿业公司"}]}
    opportunity = {"id": "demand", "kind": "explicit_demand", "title": "采购需求",
                   "description": "本次采购", "basis": "当前公告", "uncertainties": [],
                   "next_actions": [], "evidence": [{"source_ref": "research_1", "source_version": "v1",
                                                       "quote": "某矿业公司", "page": 1}]}
    with pytest.raises(AIError, match="background"):
        validate_judgment({"decision": "verify", "summary": "待核实", "demand_summary": "待核实",
                           "report": report(opportunity=opportunity)}, project(), "mock", "fp",
                          research_sources=[source])


def test_potential_opportunity_allows_verified_related_project_evidence() -> None:
    source = {"source_ref": "research_3", "source_version": "v1", "project_id": 1,
              "relation": "related_project", "relation_status": "verified",
              "title": "相关项目", "url": "https://example.com", "page_texts": [{"page": 1, "text": "后续扩建"}]}
    opportunity = {"id": "potential", "kind": "potential_opportunity", "title": "后续设备线索",
                   "description": "相关项目可能带来后续需求", "basis": "相关项目明确提到后续扩建",
                   "uncertainties": ["当前采购批次尚未确认"], "next_actions": ["核实是否属于当前项目"],
                   "evidence": [{"source_ref": "research_3", "source_version": "v1",
                                 "quote": "后续扩建", "page": 1}]}
    value = validate_judgment({"decision": "verify", "summary": "待核实", "report": report(opportunity=opportunity)},
                              project(), "mock", "fp", research_sources=[source])
    assert value["report"]["opportunities"][0]["kind"] == "potential_opportunity"


def test_linked_record_demand_is_not_promoted_to_current_procurement() -> None:
    related = [{"id": 2, "body_text": "另一个标段采购筛分机"}]
    opportunity = {"id": "linked", "kind": "explicit_demand", "title": "设备需求",
                   "description": "筛分机采购", "basis": "关联公告提及筛分机",
                   "uncertainties": ["是否属于当前采购"], "next_actions": ["核实采购批次"],
                   "evidence": [{"record_id": 2, "quote": "另一个标段采购筛分机"}]}
    with pytest.raises(AIError, match="related-project"):
        validate_judgment({"decision": "consider", "summary": "关注设备", "report": report(opportunity=opportunity)},
                          project(), "mock", "fp", related_records=related)
    opportunity["kind"] = "potential_opportunity"
    result = validate_judgment({"decision": "consider", "summary": "关注设备", "report": report(opportunity=opportunity)},
                               project(), "mock", "fp", related_records=related)
    assert result["report"]["opportunities"][0]["evidence"][0]["relation"] == "related_project"


def test_table_report_evidence_enters_research_dependency_trace() -> None:
    source = {"source_ref": "research_2", "source_version": "v1", "project_id": 1,
              "relation": "same_procurement", "relation_status": "verified",
              "title": "澄清", "url": "https://example.com", "page_texts": [{"page": 1, "text": "交付至北京"}]}
    value = report()
    value["sections"][0]["blocks"] = [{
        "type": "table", "columns": ["事实"],
        "rows": [{"cells": ["交付地点"], "evidence": [{"source_ref": "research_2",
                                                        "source_version": "v1", "quote": "交付至北京",
                                                        "page": 1}]}],
    }]
    judgment = validate_judgment({"decision": "consider", "summary": "有条件关注",
                                  "report": value}, project(), "mock", "fp",
                                 research_sources=[source])

    class Session:
        def dependencies(self):
            return [{"source_ref": "research_2", "source_version": "v1", "relation": "same_procurement",
                     "relation_status": "verified"}]

    dependencies = AgentRuns.cited_research_dependencies(judgment, Session())
    assert [(item["source_ref"], item["source_version"]) for item in dependencies] == [("research_2", "v1")]


def test_table_empty_cell_is_preserved_as_unstated() -> None:
    value = report()
    value["sections"][0]["blocks"] = [{"type": "table", "columns": ["数量"],
                                         "rows": [{"cells": [""], "evidence": []}]}]
    result = validate_judgment({"decision": "verify", "summary": "待核实", "report": value},
                               project(), "mock", "fp")
    assert result["report"]["sections"][0]["blocks"][0]["rows"][0]["cells"] == [""]


def test_attachment_evidence_uses_only_current_run_fragments() -> None:
    document = {"record_id": 1, "id": "doc/1", "name": "采购文件", "url": "https://example.com/doc",
                "sha256": "abc", "text": "完整文档中的未读取尾部", "page_texts": [{"page": 1, "text": "完整文档中的未读取尾部"}],
                "read_fragments": [{"page": 2, "text": "本轮读取的片段"}]}
    value = report(evidence=[{"record_id": 1, "document_id": "doc/1", "quote": "完整文档中的未读取尾部"}])
    with pytest.raises(AIError, match="quote"):
        validate_judgment({"decision": "verify", "summary": "待核实", "report": value}, project(), "mock", "fp",
                          document_sources=[document])

    value = report(evidence=[{"record_id": 1, "document_id": "doc/1", "quote": "本轮读取的片段"}])
    result = validate_judgment({"decision": "verify", "summary": "待核实", "report": value},
                               project(), "mock", "fp", document_sources=[document])
    evidence = result["report"]["sections"][0]["blocks"][0]["evidence"][0]
    assert evidence["page"] == 2


def test_company_disclaimer_preserves_report_text() -> None:
    value = report()
    text = "这不代表我方已具备供货能力或投标资格。"
    value["sections"][0]["blocks"][0]["text"] = text
    result = validate_judgment({"decision": "consider", "summary": "关注", "report": value},
                               project(), "mock", "fp", require_report=True)
    assert result["report"]["sections"][0]["blocks"][0]["text"] == text
    assert result["analysis_meta"]["company_match"] == "not_evaluated"


@pytest.mark.parametrize("state", ["partial", "assessed"])
def test_company_matching_still_requires_material_for_reports(state: str) -> None:
    with pytest.raises(AIError, match="company matching requires substantive company material"):
        validate_judgment({"decision": "consider", "summary": "关注", "demand_summary": "采购",
                           "analysis_meta": {"company_match": state}, "report": report()},
                          project(), "mock", "fp", require_report=True)


def test_old_database_migration_keeps_report_nullable(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE judgments (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, created_at TEXT NOT NULL, model TEXT NOT NULL, input_fingerprint TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0, decision TEXT NOT NULL, summary TEXT NOT NULL, demand_summary TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '[]', unknowns_json TEXT NOT NULL DEFAULT '[]', next_actions_json TEXT NOT NULL DEFAULT '[]', options_json TEXT NOT NULL DEFAULT '[]', analysis_meta_json TEXT, agent_run_id INTEGER, research_dependencies_json TEXT NOT NULL DEFAULT '[]', research_policy_version TEXT)")
    raw.execute("INSERT INTO judgments(project_id,created_at,model,input_fingerprint,decision,summary,demand_summary) VALUES (1,'now','old','fp','verify','old','old')")
    raw.commit()
    raw.close()

    db = connect(path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(judgments)")}
    assert "report_json" in columns
    assert db.execute("SELECT report_json FROM judgments").fetchone()[0] is None
    db.close()


def test_active_only_includes_signal_projects(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps({"id": 1, "source_id": "test", "kind": "news", "title": "行业线索",
                                  "project_name": "行业线索", "body_text": "矿山设备市场动态", "equipment": [],
                                  "facts": [], "inferences": [], "published_at": "2026-10-08",
                                  "archive_path": "archive/1/v1.html", "current_version": 1}, ensure_ascii=False),
                       encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path)
    asyncio.run(service.import_file())
    try:
        result = service.list_projects(active_only=True)
        assert [item["id"] for item in result["items"]] == [1]
        assert result["items"][0]["gate_status"] == "signal"
        assert result["items"][0]["opportunity_signals"] == []
    finally:
        service.close()
