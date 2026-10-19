"""Project summaries, detail, filtering and dashboard queries."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import sqlite3
from datetime import datetime, timedelta
from ..analysis.briefing import build_briefing
from ..analysis.analysis_policy import current_analysis_meta
from ..research.project_research import RESEARCH_POLICY_VERSION, configuration_fingerprint, list_sources
from ..core.utils import SHANGHAI, json_text, json_value, parse_timestamp, sha256_text
from ..procurement import procurement_fingerprint
from .workbench import followup_summary
from ..procurement.milestones import submission_deadline
from .state import ApplicationState

if TYPE_CHECKING:
    from .agent_runs import AgentRuns
    from .reading_store import ProcurementReadings
    from .records import ProjectRecords
    from .settings import ApplicationSettings
    from .source_status import SourceStatus


class ProjectQueries:
    def __init__(
        self, *,
        state: ApplicationState,
        agent_runs: AgentRuns,
        reading_store: ProcurementReadings,
        records: ProjectRecords,
        settings: ApplicationSettings,
        source_status: SourceStatus,
    ) -> None:
        self.state = state
        self.agent_runs = agent_runs
        self.reading_store = reading_store
        self.records = records
        self.settings = settings
        self.source_status = source_status

    def all_project_summaries(self) -> list[dict[str, Any]]:
        return [self.summary(int(row["id"])) for row in self.state.db.execute("SELECT id FROM projects ORDER BY id").fetchall()]

    @staticmethod
    def is_deadline_soon(item: dict[str, Any], *, now: datetime | None = None) -> bool:
        now = now or datetime.now(SHANGHAI)
        deadline = parse_timestamp(item.get("deadline_at"), deadline=True)
        return bool(deadline and now <= deadline <= now + timedelta(days=7) and
                    item.get("gate_status") in {"candidate", "review"})

    @staticmethod
    def reading_submission_date(item: dict[str, Any]) -> Any:
        value = submission_deadline(item.get("procurement_reading") if isinstance(item, dict) else None)
        return value.astimezone(SHANGHAI).date() if value else None

    def judgment_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        metadata = json_value(row["analysis_meta_json"], None)
        report_value = json_value(row["report_json"], None)
        report = report_value if isinstance(report_value, dict) else None
        dependencies = json_value(row["research_dependencies_json"], [])
        changed_sources = self.agent_runs.research_dependencies_changed(int(row["project_id"]), dependencies)
        run = None
        if row["agent_run_id"] is not None:
            run = self.agent_runs.agent_run_dict(self.state.db.execute("SELECT * FROM agent_runs WHERE id=? AND project_id=?",
                                      (row["agent_run_id"], row["project_id"])).fetchone())
        investigation = run.get("investigation") if run else None
        changed_configuration = bool(isinstance(investigation, dict)
                                     and investigation.get("configuration_fingerprint")
                                     and investigation["configuration_fingerprint"] != configuration_fingerprint(
                                         self.state.research_sources_path))
        return {
            "id": int(row["id"]), "created_at": row["created_at"], "model": row["model"],
            "input_fingerprint": row["input_fingerprint"],
            "stale": bool(row["stale"]) or not current_analysis_meta(metadata)
                     or row["research_policy_version"] != RESEARCH_POLICY_VERSION or changed_sources or changed_configuration,
            "analysis_meta": metadata if isinstance(metadata, dict) else None,
            "decision": row["decision"], "summary": row["summary"], "demand_summary": row["demand_summary"],
            "reasons": json_value(row["reasons_json"], []), "unknowns": json_value(row["unknowns_json"], []),
            "next_actions": json_value(row["next_actions_json"], []), "options": json_value(row["options_json"], []),
            "report": report,
            "agent_run_id": int(row["agent_run_id"]) if row["agent_run_id"] is not None else None,
            "investigation": run.get("investigation") if run else {
                "policy_version": RESEARCH_POLICY_VERSION, "status": "not_performed",
                "stop_reason": "该报告未执行主动补查", "events": [], "sources": [], "unresolved": [],
            },
        }

    def summary(self, project_id: int) -> dict[str, Any]:
        project_row = self.records.project_row(project_id)
        project_id = int(project_row["id"])
        records = self.records.records_for_project(project_id)
        gate, reasons = self.reading_store.gate(records, project_id)
        latest = records[0] if records else {}
        judgment_row = self.records.latest_judgment(project_id)
        judgment = self.judgment_dict(judgment_row)
        job = self.records.latest_job(project_id)
        ai_configured = self.settings.ai_config()["configured"]
        if gate in {"excluded", "expired", "duplicate", "closed"}:
            ai_status = "skipped"
        elif job and job["status"] in {"queued", "running", "failed"}:
            ai_status = str(job["status"])
        elif judgment and bool(judgment["stale"]):
            ai_status = "stale"
        elif judgment:
            ai_status = "succeeded"
        elif not ai_configured:
            ai_status = "not_configured"
        elif ai_configured:
            ai_status = "idle"
        else:
            ai_status = "not_configured"
        active_judgment = judgment
        if (active_judgment is None or active_judgment["stale"] or
                active_judgment.get("decision") == "blocked" or
                gate in {"excluded", "expired", "closed", "duplicate", "blocked"} or
                ai_status in {"stale", "failed", "skipped"}):
            active_judgment = None
        decision = self.records.latest_decision(project_id)
        decision_value = {
            "status": decision["status"] if decision else "new",
            "owner": decision["owner"] if decision else "",
            "next_action": decision["next_action"] if decision else "",
            "note": decision["note"] if decision else "",
            "selected_option_id": decision["selected_option_id"] if decision else None,
            "judgment_id": int(decision["judgment_id"]) if decision and decision["judgment_id"] is not None else None,
            "judgment_fingerprint": decision["judgment_fingerprint"] if decision else None,
            "updated_at": decision["created_at"] if decision else None,
        }
        selection_needs_review = bool(decision and decision["selected_option_id"] and
                                      judgment and int(decision["judgment_id"] or -1) != int(judgment["id"]))
        published = latest.get("published_at")
        deadline = latest.get("deadline_at")
        procurement = self.records.procurement_for_project(project_id)
        procurement_reading = self.reading_store.procurement_reading_for_project(project_id)
        if self.reading_store.reading_gate_enabled():
            verified_deadline = submission_deadline(procurement_reading)
            deadline = verified_deadline.isoformat() if verified_deadline else None
        briefing = build_briefing(latest, procurement=procurement)
        # Keep source-derived briefing fields intact while exposing parsed
        # attachment facts beside them for detail and list clients.
        briefing["procurement"] = procurement
        equipment_values = sorted({str(x) for r in records for x in r.get("equipment", []) if x})
        if not equipment_values:
            # Collector equipment remains authoritative when present.  An
            # evidenced procurement item is a safe fallback for source rows
            # whose equipment field was empty; titles are never consulted.
            equipment_values = sorted({str(item.get("name") or "").strip()
                                       for item in procurement.get("items", [])
                                       if isinstance(item, dict) and str(item.get("name") or "").strip()})
        ai_next_actions = active_judgment.get("next_actions") if active_judgment else []
        if not isinstance(ai_next_actions, list):
            ai_next_actions = []
        opportunity_signals: list[dict[str, Any]] = []
        if active_judgment and isinstance(active_judgment.get("report"), dict):
            raw_signals = active_judgment["report"].get("opportunities", [])
            if isinstance(raw_signals, list):
                opportunity_signals = [item for item in raw_signals if isinstance(item, dict)]
        latest_run = self.records.latest_agent_run(project_id)
        investigation = json_value(latest_run["investigation_json"], {}) if latest_run else {}
        research_summary = None
        if isinstance(investigation, dict) and investigation.get("status"):
            research_summary = {"status": investigation["status"],
                                "source_count": len(investigation.get("sources", [])),
                                "stop_reason": investigation.get("stop_reason"),
                                "updated_at": latest_run["finished_at"] or latest_run["started_at"]}
        return {
            "id": int(project_id), "title": latest.get("title") or f"项目 {project_id}",
            "buyer": latest.get("buyer"), "equipment": equipment_values,
            "kind": latest.get("kind") or "tender", "source_id": latest.get("source_id") or "",
            "record_ids": [int(r["id"]) for r in records], "published_at": published,
            "deadline_at": deadline, "updated_at": project_row["updated_at"], "gate_status": gate,
            "gate_reasons": reasons, "ai_status": ai_status,
            "ai_decision": active_judgment["decision"] if active_judgment else None,
            "ai_summary": active_judgment["summary"] if active_judgment else None,
            "demand_summary": active_judgment["demand_summary"] if active_judgment else None,
            "ai_next_actions": ai_next_actions,
            "analysis_meta": active_judgment.get("analysis_meta") if active_judgment else None,
            "opportunity_signals": opportunity_signals,
            "research_summary": research_summary,
            "briefing": briefing,
            "procurement": procurement,
            "procurement_reading": procurement_reading,
            "procurement_fingerprint": procurement_fingerprint(procurement),
            "procurement_reading_fingerprint": sha256_text(json_text(procurement_reading)),
            "needs_review": gate == "review" or ai_status in {"stale", "failed", "not_configured", "idle"} or
                            (judgment is not None and judgment["decision"] == "verify") or selection_needs_review,
            "decision": decision_value,
            **followup_summary(self.state.db, project_id, decision_value),
        }

    def project_detail(self, project_id: int) -> dict[str, Any]:
        summary = self.summary(project_id)
        project_id = int(summary["id"])
        records = self.records.records_for_project(project_id)
        judgments = self.state.db.execute("SELECT * FROM judgments WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        decisions = self.state.db.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        versions = self.state.db.execute("""SELECT rv.* FROM record_versions rv JOIN project_records pr ON pr.record_id=rv.record_id
                                     WHERE pr.project_id=? ORDER BY rv.record_id, rv.version DESC""", (project_id,)).fetchall()
        related_ids: set[int] = set()
        for row in self.state.db.execute("""SELECT r.possible_duplicate_of,r.duplicate_of,r.related_record_id FROM records r
                                     JOIN project_records pr ON pr.record_id=r.id WHERE pr.project_id=?""", (project_id,)).fetchall():
            for key in ("possible_duplicate_of", "duplicate_of", "related_record_id"):
                if row[key] is not None:
                    pid = self.records.project_for_record_id(int(row[key]))
                    if pid is not None and pid != project_id:
                        related_ids.add(pid)
        for row in self.state.db.execute("SELECT project_id,target_project_id FROM links WHERE active=1 AND (project_id=? OR target_project_id=?)",
                                   (project_id, project_id)).fetchall():
            other = int(row["target_project_id"] if row["project_id"] == project_id else row["project_id"])
            if other != project_id:
                related_ids.add(other)
        history = [self.judgment_dict(row) for row in judgments]
        decision_history = [{
            "id": int(row["id"]), "status": row["status"], "owner": row["owner"], "next_action": row["next_action"],
            "note": row["note"], "selected_option_id": row["selected_option_id"],
            "judgment_id": int(row["judgment_id"]) if row["judgment_id"] is not None else None,
            "judgment_fingerprint": row["judgment_fingerprint"], "updated_at": row["created_at"],
        } for row in decisions]
        version_values = [dict(row) for row in versions]
        for row in version_values:
            row["id"] = int(row["id"])
        agent_run = self.agent_runs.agent_run_dict(self.records.latest_agent_run(project_id))
        return {**summary, "records": records, "judgment": history[0] if history else None,
                "judgment_history": history, "decision_history": decision_history,
                "related_projects": [self.summary(pid) for pid in sorted(related_ids)], "versions": version_values,
                "agent_run": agent_run, "research_sources": list_sources(self.state.db, project_id)}

    def list_projects(self, *, q: str = "", gate: str = "", ai_status: str = "", decision: str = "",
                      ai_decision: str = "", deadline_soon: bool = False, kind: str = "", source_id: str = "",
                      category: str = "", region: str = "", buyer: str = "", procurement_stage: str = "",
                      demand_type: str = "", followup_stage: str = "", is_following: bool = False,
                      key_time: str = "", published_days: int = 0,
                      page: int = 1, page_size: int = 30, sort: str = "updated",
                      active_only: bool = False) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        projects = []
        now = datetime.now(SHANGHAI)
        published_days = max(0, min(365, int(published_days)))
        for item in self.all_project_summaries():
            if published_days:
                published = parse_timestamp(item.get("published_at"))
                today = now.date()
                if published is None or not today - timedelta(days=published_days - 1) <= published.astimezone(SHANGHAI).date() <= today:
                    continue
            briefing = item.get("briefing") or {}
            equipment = " ".join(str(value) for value in item.get("equipment") or [])
            haystack = " ".join([str(item.get("title") or ""), str(item.get("buyer") or ""),
                                 equipment, str(item.get("ai_summary") or ""),
                                 str(briefing.get("scope_excerpt") or ""),
                                 str((item.get("procurement") or {}).get("scope_summary") or ""),
                                 str((item.get("procurement") or {}).get("delivery_location") or "")]).lower()
            if q and q.lower() not in haystack:
                continue
            if active_only and item["gate_status"] not in {"candidate", "review", "signal"}:
                continue
            if gate and item["gate_status"] != gate:
                continue
            if ai_status and item["ai_status"] != ai_status:
                continue
            if decision and item["decision"]["status"] != decision:
                continue
            # ai_decision is deliberately taken from the summary's current,
            # non-stale judgment field.  Gate status/candidate status cannot
            # stand in for an AI recommendation.
            if ai_decision and item.get("ai_decision") != ai_decision:
                continue
            if deadline_soon and not self.is_deadline_soon(item, now=now):
                continue
            if kind and item["kind"] != kind:
                continue
            if source_id and item["source_id"] != source_id:
                continue
            reading = item.get("procurement_reading") or {}
            if category and not any(category.lower() in str(value).lower()
                                    for value in reading.get("category", []) if value):
                continue
            if region and region.lower() not in str(reading.get("region") or "").lower():
                continue
            if buyer and buyer.lower() not in str(reading.get("buyer") or item.get("buyer") or "").lower():
                continue
            if procurement_stage and str(reading.get("stage") or "") != procurement_stage:
                continue
            if demand_type and str(reading.get("demand_type") or "unknown") != demand_type:
                continue
            if is_following and not item.get("is_following"):
                continue
            if followup_stage and item.get("followup_stage") != followup_stage:
                continue
            if key_time:
                submission_date = self.reading_submission_date(item)
                today = now.astimezone(SHANGHAI).date()
                if key_time == "unknown":
                    if submission_date is not None:
                        continue
                elif key_time in {"today", "7d", "30d"}:
                    days = {"today": 0, "7d": 7, "30d": 30}[key_time]
                    if submission_date is None or not today <= submission_date <= today + timedelta(days=days):
                        continue
            projects.append(item)
        if sort == "deadline":
            projects.sort(key=lambda item: (item.get("deadline_at") is None, item.get("deadline_at") or "9999"))
        elif sort == "published":
            def published_key(item: dict[str, Any]) -> tuple[float, int]:
                published = parse_timestamp(item.get("published_at"))
                return (published.timestamp() if published else float('-inf'), item["id"])
            projects.sort(key=published_key, reverse=True)
        else:
            projects.sort(key=lambda item: (item.get("updated_at") or "", item["id"]), reverse=True)
        total = len(projects)
        start = (page - 1) * page_size
        return {"items": projects[start:start + page_size], "total": total, "page": page, "page_size": page_size}

    def dashboard(self) -> dict[str, Any]:
        # Dashboard aggregates are full-dataset counts.  The visible deadline
        # list is intentionally capped separately for the home page.
        projects = self.all_project_summaries()
        gate_counts: dict[str, int] = {}
        ai_counts: dict[str, int] = {}
        ai_decision_counts: dict[str, int] = {}
        decision_counts: dict[str, int] = {}
        active_demand_counts: dict[str, int] = {}
        now = datetime.now(SHANGHAI)
        deadline_soon = []
        for item in projects:
            if item["gate_status"] in {"candidate", "review", "signal"}:
                demand_type = str((item.get("procurement_reading") or {}).get("demand_type") or "unknown")
                active_demand_counts[demand_type] = active_demand_counts.get(demand_type, 0) + 1
            gate_counts[item["gate_status"]] = gate_counts.get(item["gate_status"], 0) + 1
            ai_counts[item["ai_status"]] = ai_counts.get(item["ai_status"], 0) + 1
            decision_counts[item["decision"]["status"]] = decision_counts.get(item["decision"]["status"], 0) + 1
            ai_decision = item.get("ai_decision")
            if ai_decision:
                ai_decision_counts[str(ai_decision)] = ai_decision_counts.get(str(ai_decision), 0) + 1
            if self.is_deadline_soon(item, now=now):
                deadline_soon.append(item)
        rows = self.state.db.execute("SELECT count(*) AS c FROM records").fetchone()
        updated = self.source_status.source_data_updated_at()
        file_stale = self.source_status.source_age_stale()
        import_pending = self.source_status.import_pending()
        return {"total_records": int(rows["c"] if rows else 0), "total_projects": len(projects),
                "gate_counts": gate_counts, "ai_counts": ai_counts, "ai_decision_counts": ai_decision_counts,
                "active_demand_counts": active_demand_counts,
                "decision_counts": decision_counts, "deadline_soon": deadline_soon[:30],
                "followup_count": sum(bool(item.get("is_following")) for item in projects),
                "deadline_soon_count": len(deadline_soon), "data_updated_at": updated,
                "last_import_at": self.settings.get_meta("last_import_at"), "import_error": self.settings.get_meta("import_error"),
                "file_stale": file_stale, "import_pending": import_pending,
                "source_notes": self.source_status.source_notes(), "ai_configured": self.settings.ai_config()["configured"]}
