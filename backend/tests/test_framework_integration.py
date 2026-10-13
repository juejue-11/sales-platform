from __future__ import annotations

import asyncio
import copy

import pytest

from backend.analysis_policy import ANALYSIS_POLICY_VERSION
from backend.ai import AIError
from backend.service import ScreeningService
from backend.tests.test_backend import Provider, make_service, row


def test_failed_runtime_usage_is_saved_without_an_accepted_report(tmp_path):
    class Agent:
        async def analyze(self, *, on_event, **kwargs):
            await on_event({"stage": "usage", "usage": {
                "model_calls": 5, "tool_calls": 7, "input_tokens": 1200, "output_tokens": 16384,
            }})
            raise AIError("模型输出达到token上限")

    service = make_service(tmp_path, [row()], Agent())
    try:
        service.update_settings({"ai": {"enabled": True, "base_url": "https://mock.local/v1",
                                        "model": "mock", "api_key": "isolated-secret"}})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        detail = service.project_detail(1)
        assert detail["agent_run"]["status"] == "failed"
        assert detail["agent_run"]["usage"]["output_tokens"] == 16384
        assert detail["agent_run"]["usage"]["model_calls"] == 5
        assert detail["judgment"] is None
    finally:
        service.close()


@pytest.mark.parametrize("kind,body,opportunity_kind", [
    ("tender", "采购破碎机两台，技术指标待核实。", "explicit_demand"),
    ("news", "矿区拟建设破碎系统，采购安排尚未公布。", "potential_opportunity"),
    ("tender", "矿区道路劳务服务，范围仅为路面维护。", None),
])
def test_flexible_report_is_saved_reopened_and_invalidated_as_one_framework(tmp_path, kind, body, opportunity_kind):
    record = row(body=body)
    record["kind"] = kind
    evidence = [{"record_id": 1, "quote": body}]
    opportunities = []
    if opportunity_kind:
        opportunities.append({"id": "market-direction", "kind": opportunity_kind, "title": "设备关注方向",
                              "description": "依据项目实际范围判断参与方向", "basis": body,
                              "uncertainties": ["具体技术条件尚待核实"], "next_actions": ["核实采购范围和时间"],
                              "evidence": evidence})
    report = {"version": 1, "sections": [{"id": "project", "title": "当前项目的事实和边界", "blocks": [
        {"type": "paragraph", "text": body, "evidence": evidence},
        {"type": "table", "columns": ["依据", "尚未确认"], "rows": [{"cells": [body, ""], "evidence": evidence}]},
    ]}], "opportunities": opportunities, "gaps": [{
        "id": "technical", "description": "设备技术清单", "status": "not_stated" if opportunity_kind else "not_applicable",
        "impact": "影响型号选择" if opportunity_kind else "本次范围为道路劳务，无设备采购依据",
        "next_action": "核实技术文件" if opportunity_kind else "", "worth_researching": bool(opportunity_kind),
    }]}
    result = {"decision": "consider" if opportunity_kind else "not_recommended",
              "summary": "依据公开项目资料判断市场关注方向", "report": report,
              "options": copy.deepcopy(Provider().payload["options"]) if opportunity_kind else []}
    service = make_service(tmp_path, [record], Provider(result))
    try:
        service.update_settings({"ai": {"enabled": True, "base_url": "https://mock.local/v1",
                                        "model": "mock", "api_key": "isolated-secret"},
                                 "business": {"customer_role": "经销商", "mineral_types": "煤矿"}})
        asyncio.run(service.run_job(service.queue_project(1)["job_id"]))
        detail = service.project_detail(1)
        assert detail["ai_status"] == "succeeded"
        assert detail["judgment"]["report"] == report
        assert detail["analysis_meta"]["company_match"] == "not_evaluated"
        assert detail["analysis_meta"]["semantic_version"] == ANALYSIS_POLICY_VERSION
        assert detail["opportunity_signals"] == opportunities
        if not opportunity_kind:
            assert detail["judgment"]["unknowns"] == []
        db_path, source_path = service.db_path, service.source_path
    finally:
        service.close()
    reopened = ScreeningService(db_path=db_path, source_path=source_path, archive_root=tmp_path, provider=Provider(result))
    try:
        assert reopened.project_detail(1)["judgment"]["report"] == report
        assert reopened.project_detail(1)["ai_status"] == "succeeded"
        reopened.update_settings({"business": {"equipment_categories": "筛分设备"}})
        changed = reopened.project_detail(1)
        assert changed["judgment"]["stale"] is True
        assert changed["opportunity_signals"] == []
        assert changed["judgment_history"][0]["report"] == report
    finally:
        reopened.close()
