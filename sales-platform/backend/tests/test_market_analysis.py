from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from backend.analysis.errors import AIError
from backend.analysis.prompts import build_messages, input_fingerprint
from backend.analysis.validation import validate_judgment
from backend.analysis.analysis_policy import ANALYSIS_POLICY_VERSION, analysis_context
from backend.application.service import ScreeningService
from backend.application.errors import ConflictError
from backend.tests.test_backend import Provider, make_service, row


AI_SETTINGS = {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
               "api_key": "secret-value"}


def payload():
    return copy.deepcopy(Provider().payload)


@pytest.mark.parametrize("business", [
    {}, {"company_name": "某设备公司"}, {"region_preferences": "山西"},
    {"target_keywords": ["破碎机"], "region_preferences": "山西", "company_name": "某公司"},
])
def test_empty_identity_and_preferences_allow_market_analysis(tmp_path: Path, business):
    service = make_service(tmp_path, [row()])
    try:
        service.update_settings({"business": business, "ai": AI_SETTINGS})
        assert service.get_settings()["analysis"]["company_profile_present"] is False
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        judgment = service.project_detail(1)["judgment"]
        assert judgment["decision"] == "consider"
        assert judgment["options"] and judgment["analysis_meta"] == {
            "semantic_version": ANALYSIS_POLICY_VERSION, "decision_scope": "market",
            "company_match": "not_evaluated",
        }
        context, _ = service.analysis_context.analysis_context(1, business)
        message_input = json.loads(build_messages(service.project_detail(1), business)[1]["content"])
        for key in ("analysis_goal", "customer_preferences", "company_capability_card", "company_profile_present"):
            assert context[key] == message_input[key]
        assert "region_preferences" not in context["company_capability_card"]
        assert "target_keywords" not in context["company_capability_card"]
        assert context["customer_preferences"]["region_preferences"] == business.get("region_preferences", "")
        # General actions remain selectable without claiming company fit.
        service.save_decision(1, {"status": "following", "selected_option_id": "a",
                                 "judgment_id": judgment["id"], "next_action": "取得技术清单"})
        assert service.summary(1)["decision"]["judgment_id"] == judgment["id"]
    finally:
        service.close()


def test_partial_company_matching_is_persisted_without_downgrading_market(tmp_path: Path):
    value = payload()
    value["analysis_meta"] = {"company_match": "partial", "semantic_version": "model-spoof",
                              "decision_scope": "company"}
    service = make_service(tmp_path, [row()], Provider(value))
    try:
        service.update_settings({"business": {"supported_products": "破碎机，资质与型号尚待确认"},
                                 "ai": AI_SETTINGS})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        judgment = service.project_detail(1)["judgment"]
        assert judgment["decision"] == "consider"
        assert judgment["analysis_meta"] == {"semantic_version": ANALYSIS_POLICY_VERSION,
                                              "decision_scope": "market", "company_match": "partial"}
        service_path, source_path = service.db_path, service.source_path
    finally:
        service.close()
    reopened = ScreeningService(db_path=service_path, source_path=source_path, provider=Provider())
    try:
        assert reopened.project_detail(1)["judgment"]["analysis_meta"]["company_match"] == "partial"
        assert reopened.queue_project(1)["status"] == "succeeded"
    finally:
        reopened.close()


@pytest.mark.parametrize("value", [
    {"company_match": "assessed"}, {"company_match": "partial"}, {"company_match": "qualified"},
    {"company_match": []}, [],
])
def test_company_matching_metadata_requires_real_material(value):
    result = payload()
    result["analysis_meta"] = value
    with pytest.raises(AIError):
        validate_judgment(result, {"records": [row()]}, "mock", "fp",
                          company_material={"company_name": "名称", "region_preferences": "地区"})


def test_company_disclaimer_does_not_fail_analysis_or_trigger_correction(tmp_path: Path):
    value = payload()
    value["summary"] = (
        "本次未提供企业能力资料，因此企业匹配度保持未评估，上述门槛仅作对照清单使用；"
        "这不代表我方已具备供货能力或投标资格。"
    )
    provider = Provider(value)
    service = make_service(tmp_path, [row()], provider)
    try:
        service.update_settings({"ai": AI_SETTINGS})
        job_id = service.queue_project(1)["job_id"]
        asyncio.run(service.run_job(job_id))
        job = service.db.execute("SELECT status,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert job["status"] == "succeeded"
        assert job["attempts"] == 1
        judgment = service.project_detail(1)["judgment"]
        assert judgment["summary"] == value["summary"]
        assert judgment["analysis_meta"]["company_match"] == "not_evaluated"
    finally:
        service.close()


@pytest.mark.parametrize("text", [
    "不能证明我方具备相关产品供货能力。",
    "在确认我方具备相关产品供货能力后参与。",
    "若我方具备相关资质，可考虑参与。",
])
def test_company_narrative_wording_does_not_determine_matching_state(text):
    value = payload()
    value["options"][0]["suitable_for"] = text
    result = validate_judgment(value, {"records": [row()]}, "mock", "fp")
    assert result["options"][0]["suitable_for"] == text
    assert result["analysis_meta"]["company_match"] == "not_evaluated"


def test_key_fact_gap_remains_verify_without_company_gap(tmp_path: Path):
    value = payload()
    value.update(decision="verify", unknowns=["需核实设备技术要求"], options=[])
    service = make_service(tmp_path, [row()], Provider(value))
    try:
        service.update_settings({"ai": AI_SETTINGS})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        judgment = service.project_detail(1)["judgment"]
        assert judgment["decision"] == "verify"
        assert judgment["unknowns"] == ["需核实设备技术要求"]
    finally:
        service.close()


def test_news_can_have_evidenced_market_interest_but_no_procurement_plan():
    project = {"kind": "news", "records": [row(body="矿区计划建设破碎系统，采购安排尚未公布。") ]}
    value = payload()
    value["decision"] = "recommend"
    value["reasons"][0]["evidence"][0]["quote"] = "矿区计划建设破碎系统"
    value["summary"] = "建议关注破碎系统项目线索，当前尚非明确采购。"
    assert validate_judgment(value, project, "mock", "fp")["decision"] == "recommend"
    value["options"][0]["title"] = "供货方案"
    with pytest.raises(AIError, match="procurement option"):
        validate_judgment(value, project, "mock", "fp")


def test_agent_failure_keeps_evidenced_body_judgment(tmp_path: Path, monkeypatch):
    class Agent:
        context = None

        async def analyze(self, *, context, messages, tool_callback, **kwargs):
            self.context = context
            assert context["company_profile_present"] is False
            data = await tool_callback({"record_id": 1})
            assert data["documents"][0]["status"] == "failed"
            assert data["documents"][0]["error"] == "fetch_failed"
            value = payload()
            value["unknowns"] = ["附件获取失败，技术指标需另行核实；正文采购破碎机已明确"]
            return {"content": json.dumps(value, ensure_ascii=False), "usage": {"model_calls": 2}}

    async def failed_fetch(record):
        raise RuntimeError("isolated document failure")

    monkeypatch.setattr("backend.application.documents.fetch_record_documents", failed_fetch)
    agent = Agent()
    service = make_service(tmp_path, [row()], agent)
    try:
        service.update_settings({"ai": AI_SETTINGS})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        judgment = service.project_detail(1)["judgment"]
        assert judgment["decision"] == "consider"
        assert judgment["options"]
        assert "附件获取失败" in judgment["unknowns"][0]
        assert judgment["analysis_meta"]["company_match"] == "not_evaluated"
    finally:
        service.close()


def test_legacy_upgrade_preserves_history_and_requires_new_choice(tmp_path: Path):
    service = make_service(tmp_path, [row()])
    service.update_settings({"ai": AI_SETTINGS})
    asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
    old = service.project_detail(1)["judgment"]
    service.save_decision(1, {"status": "following", "selected_option_id": "a",
                             "judgment_id": old["id"], "owner": "销售", "next_action": "核对清单"})
    decisions = [tuple(value) for value in service.db.execute("SELECT * FROM decisions")]
    db_path, source_path = service.db_path, service.source_path
    service.close()
    # Reproduce a database created before the metadata column existed.
    with sqlite3.connect(db_path) as legacy:
        legacy.execute("ALTER TABLE judgments DROP COLUMN analysis_meta_json")
    migrated = ScreeningService(db_path=db_path, source_path=source_path, provider=Provider())
    try:
        detail = migrated.project_detail(1)
        assert detail["judgment"]["stale"] is True
        assert detail["judgment"]["analysis_meta"] is None
        assert detail["judgment"]["summary"] == old["summary"]
        assert detail["ai_decision"] is None
        assert [tuple(value) for value in migrated.db.execute("SELECT * FROM decisions")] == decisions
        # Migration itself does not enqueue a paid batch.
        assert migrated.db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 0
        new_job = migrated.queue_project(1)
        assert new_job["status"] == "queued"
        asyncio.run(migrated.run_job(new_job["job_id"]))
        fresh = migrated.project_detail(1)["judgment"]
        assert fresh["analysis_meta"]["semantic_version"] == ANALYSIS_POLICY_VERSION
        assert migrated.project_detail(1)["decision"]["judgment_id"] == old["id"]
        with pytest.raises(ConflictError):
            migrated.save_decision(1, {"status": "following", "selected_option_id": "a",
                                       "judgment_id": old["id"]})
        migrated.save_decision(1, {"status": "following", "selected_option_id": "a",
                                   "judgment_id": fresh["id"]})
    finally:
        migrated.close()


def test_context_version_and_preferences_participate_in_fingerprint(monkeypatch):
    project = {"records": [row()]}
    empty = input_fingerprint(project, {})
    assert empty != input_fingerprint(project, {"region_preferences": "山西"})
    assert empty != input_fingerprint(project, {"supported_products": "破碎机"})
    for field in ("customer_role", "equipment_categories", "mineral_types", "demand_focus"):
        business = {field: "关注内容"}
        assert empty != input_fingerprint(project, business)
        context = analysis_context(business)
        assert context["customer_preferences"][field] == "关注内容"
        assert field not in context["company_capability_card"]
        assert context["company_profile_present"] is False
    assert analysis_context({})["analysis_semantic_version"] == ANALYSIS_POLICY_VERSION
    monkeypatch.setattr("backend.analysis.analysis_policy.ANALYSIS_POLICY_VERSION", "future_market_policy")
    assert empty != input_fingerprint(project, {})
