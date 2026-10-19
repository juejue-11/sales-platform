"""Agent activity, usage and cited research audit records."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import re
import sqlite3
from ..analysis.ai import sanitize_ai_error
from ..research.project_research import ResearchSession, configuration_fingerprint, current_dependencies, read_saved_source
from ..analysis.agent_report import iter_report_evidence
from ..core.utils import json_text, json_value, now_iso
from .state import ApplicationState
from .errors import NotFoundError

if TYPE_CHECKING:
    from .records import ProjectRecords
from .agent_policy import DEFAULT_AGENT_LIMITS, TRUSTED_AGENT_TOOLS


class AgentRuns:
    def __init__(self, *, state: ApplicationState, records: ProjectRecords) -> None:
        self.state = state
        self.records = records

    @staticmethod
    def agent_run_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        usage = json_value(row["usage_json"], {})
        limits = json_value(row["limits_json"], {})
        steps = json_value(row["steps_json"], [])
        if not isinstance(usage, dict):
            usage = {}
        if not isinstance(limits, dict):
            limits = {}
        if not isinstance(steps, list):
            steps = []
        usage = {key: int(usage.get(key, 0) or 0) for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens")}
        limits = {key: int(limits.get(key, DEFAULT_AGENT_LIMITS[key]) or DEFAULT_AGENT_LIMITS[key])
                  for key in DEFAULT_AGENT_LIMITS}
        safe_steps = []
        for step in steps:
            if not isinstance(step, dict) or step.get("stage") not in {"started", "model", "tool_start", "tool_end", "completed"}:
                continue
            value = {"stage": step["stage"], "label": str(step.get("label") or "")[:120],
                     "at": str(step.get("at") or "")[:80]}
            if isinstance(step.get("tool"), str) and step["tool"]:
                value["tool"] = step["tool"][:100]
            if isinstance(step.get("turn"), int):
                value["turn"] = step["turn"]
            safe_steps.append(value)
        return {
            "id": int(row["id"]), "job_id": int(row["job_id"]), "engine": "pi_sdk",
            "status": row["status"] if row["status"] in {"running", "succeeded", "failed"} else "failed",
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "error": row["error"], "steps": safe_steps, "usage": usage, "limits": limits,
            "investigation": json_value(row["investigation_json"], {}) or None,
        }

    def research_runs(self, project_id: int) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        rows = self.state.db.execute("SELECT * FROM agent_runs WHERE project_id=? ORDER BY id DESC LIMIT 50",
                               (project_id,)).fetchall()
        total = self.state.db.execute("SELECT COUNT(*) FROM agent_runs WHERE project_id=?", (project_id,)).fetchone()[0]
        return {"items": [self.agent_run_dict(row) for row in rows], "total": int(total)}

    def research_run(self, project_id: int, run_id: int) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        row = self.state.db.execute("SELECT * FROM agent_runs WHERE project_id=? AND id=?",
                              (project_id, run_id)).fetchone()
        value = self.agent_run_dict(row)
        if value is None:
            raise NotFoundError("调查运行记录不存在")
        return value

    def research_source_snapshot(self, project_id: int, source_ref: str, version: str, *,
                                 page: int | None = None, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        try:
            return read_saved_source(self.state.db, project_id, source_ref, version,
                                     page=page, offset=offset, limit=limit)
        except (LookupError, ValueError, PermissionError) as exc:
            raise NotFoundError("调查来源版本不存在或不属于当前项目") from exc

    def agent_limits(self) -> dict[str, int]:
        return dict(DEFAULT_AGENT_LIMITS)

    def new_agent_run(self, job_id: int, project_id: int) -> int:
        with self.state.lock, self.state.db:
            row = self.state.db.execute(
                "INSERT INTO agent_runs(job_id,project_id,engine,status,started_at,steps_json,usage_json,limits_json) "
                "VALUES (?,?,?,'running',?,?,?,?) RETURNING id",
                (job_id, project_id, "pi_sdk", now_iso(), json_text([]),
                 json_text({"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}),
                 json_text(self.agent_limits())),
            ).fetchone()
            return int(row[0])

    @staticmethod
    def agent_public_event(event: Any) -> dict[str, Any] | None:
        labels = {
            "started": "智能体已启动", "model": "模型调用", "tool_start": "工具调用开始",
            "tool_end": "工具调用完成", "completed": "方案已通过校验",
        }
        if not isinstance(event, dict) or event.get("stage") not in labels:
            return None
        stage = str(event["stage"])
        value: dict[str, Any] = {"stage": stage, "label": labels[stage], "at": now_iso()}
        tool = event.get("tool")
        if stage in {"tool_start", "tool_end"} and isinstance(tool, str) and tool in TRUSTED_AGENT_TOOLS:
            tool = re.sub(r"[^A-Za-z0-9_.:/-]", "", tool)[:100]
            if tool:
                value["tool"] = tool
                names = {
                    "read_project_records": "读取项目资料",
                    "read_company_capability": "读取公司能力",
                    "read_related_records": "读取关联资料",
                    "fetch_project_documents": "读取采购附件",
                    "search_project_sources": "搜索项目资料",
                    "read_research_source": "读取调查来源",
                    "assess_source_relation": "核实资料关系",
                    "search_research_content": "检索资料内容",
                }
                name = names[tool]
                if stage == "tool_start":
                    value["label"] = f"{name}开始"
                elif event.get("is_error") is True or event.get("_is_error") is True:
                    value["label"] = f"{name}失败"
                else:
                    value["label"] = f"{name}完成"
        elif stage == "tool_end" and event.get("is_error") is True:
            value["label"] = "工具调用失败"
        turn = event.get("turn")
        if isinstance(turn, int) and 0 <= turn <= 1000:
            value["turn"] = turn
        return value

    def record_agent_event(self, agent_run_id: int, event: Any) -> None:
        clean = self.agent_public_event(event)
        if clean is None:
            return
        try:
            with self.state.lock, self.state.db:
                row = self.state.db.execute("SELECT steps_json FROM agent_runs WHERE id=?", (agent_run_id,)).fetchone()
                steps = json_value(row[0], []) if row else []
                if not isinstance(steps, list):
                    steps = []
                steps.append(clean)
                self.state.db.execute("UPDATE agent_runs SET steps_json=? WHERE id=?", (json_text(steps[-200:]), agent_run_id))
        except sqlite3.ProgrammingError:
            # Closing the host while a provider callback is in flight should
            # not resurrect a DB connection or mask process cleanup.
            return

    def update_agent_usage(self, agent_run_id: int, usage: dict[str, Any] | None) -> None:
        if not isinstance(usage, dict):
            return
        try:
            with self.state.lock, self.state.db:
                row = self.state.db.execute("SELECT usage_json FROM agent_runs WHERE id=?", (agent_run_id,)).fetchone()
                current = json_value(row[0], {}) if row else {}
                if not isinstance(current, dict):
                    current = {}
                for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens"):
                    value = usage.get(key)
                    if isinstance(value, int) and value >= 0:
                        current[key] = int(current.get(key, 0) or 0) + min(value, 2_000_000_000)
                self.state.db.execute("UPDATE agent_runs SET usage_json=? WHERE id=?", (json_text(current), agent_run_id))
        except sqlite3.ProgrammingError:
            return

    def finish_agent_run(self, agent_run_id: int, status: str, error: BaseException | str | None = None) -> None:
        if status not in {"succeeded", "failed"}:
            status = "failed"
        safe_error = sanitize_ai_error(error if isinstance(error, BaseException) else RuntimeError(str(error))) if error else None
        try:
            with self.state.lock, self.state.db:
                self.state.db.execute("UPDATE agent_runs SET status=?,finished_at=?,error=? WHERE id=?",
                                (status, now_iso(), safe_error, agent_run_id))
        except sqlite3.ProgrammingError:
            return

    def record_investigation(self, agent_run_id: int, session: ResearchSession, *,
                              status: str | None = None, stop_reason: str | None = None,
                              unresolved: list[str] | None = None, configuration: str | None = None) -> None:
        value = session.investigation(status=status, stop_reason=stop_reason, unresolved=unresolved)
        value["configuration_fingerprint"] = configuration or configuration_fingerprint(self.state.research_sources_path)
        try:
            with self.state.lock, self.state.db:
                self.state.db.execute("UPDATE agent_runs SET investigation_json=? WHERE id=?",
                                (json_text(value), agent_run_id))
        except sqlite3.ProgrammingError:
            return

    @staticmethod
    def cited_research_dependencies(judgment: dict[str, Any], session: ResearchSession | None) -> list[dict[str, Any]]:
        if session is None:
            return []
        cited = {(evidence["source_ref"], evidence["source_version"])
                 for reason in judgment.get("reasons", []) for evidence in reason.get("evidence", [])
                 if isinstance(evidence, dict) and "source_ref" in evidence and "source_version" in evidence}
        cited.update((evidence["source_ref"], evidence["source_version"])
                     for evidence in iter_report_evidence(judgment.get("report"))
                     if "source_ref" in evidence and "source_version" in evidence)
        return sorted((dependency for dependency in session.dependencies()
                       if (dependency["source_ref"], dependency["source_version"]) in cited),
                      key=lambda dependency: (dependency["source_ref"], dependency["source_version"]))

    def research_dependencies_changed(self, project_id: int, dependencies: list[dict[str, Any]]) -> bool:
        comparisons = current_dependencies(self.state.db, project_id, dependencies)
        if len(comparisons) != len(dependencies):
            return True
        return any(current.get("changed") or current.get("relation_status") != saved.get("relation_status")
                   for saved, current in zip(dependencies, comparisons))
