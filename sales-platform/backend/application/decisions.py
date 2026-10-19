"""Human decisions, gate overrides and explicit project relations."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from ..analysis.analysis_policy import current_analysis_meta
from ..core.utils import json_value, now_iso
from .state import ApplicationState
from .errors import ServiceError, ConflictError

if TYPE_CHECKING:
    from .analysis_context import AnalysisContext
    from .projects import ProjectQueries
    from .reading_store import ProcurementReadings
    from .records import ProjectRecords
    from .settings import ApplicationSettings


class ProjectDecisions:
    def __init__(
        self, *,
        state: ApplicationState,
        analysis_context: AnalysisContext,
        projects: ProjectQueries,
        reading_store: ProcurementReadings,
        records: ProjectRecords,
        settings: ApplicationSettings,
        queue_project: Callable[..., Any],
    ) -> None:
        self.state = state
        self.analysis_context = analysis_context
        self.projects = projects
        self.reading_store = reading_store
        self.records = records
        self.settings = settings
        self.queue_project = queue_project

    def save_decision(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        status = payload.get("status")
        if status not in {"new", "following", "verify", "closed"}:
            raise ServiceError("invalid decision status")
        selected = payload.get("selected_option_id")
        if selected is not None and not isinstance(selected, str):
            raise ServiceError("selected_option_id must be a string or null")
        gate, _ = self.reading_store.gate(self.records.records_for_project(project_id), project_id)
        judgment = self.records.latest_judgment(project_id)
        judgment_is_current = bool(judgment and not self.projects.judgment_dict(judgment)["stale"] and
                                   current_analysis_meta(json_value(judgment["analysis_meta_json"], None)))
        options = json_value(judgment["options_json"], []) if judgment_is_current else []
        if selected is not None:
            if gate in {"expired", "closed", "duplicate", "excluded"}:
                raise ConflictError("不能选择已过期或不可推进项目的方案")
            if not judgment_is_current:
                raise ConflictError("当前判断已过期，请先重新分析")
            requested_judgment_id = payload.get("judgment_id")
            try:
                requested_judgment_id = int(requested_judgment_id) if requested_judgment_id is not None else None
            except (TypeError, ValueError):
                requested_judgment_id = None
            if requested_judgment_id is None or requested_judgment_id != int(judgment["id"]):
                raise ConflictError("方案来自旧判断，请刷新后重新确认")
            if selected not in {str(option.get("id")) for option in options if isinstance(option, dict)}:
                raise ConflictError("方案不存在或已失效")
        fields = {
            "owner": str(payload.get("owner") or ""), "next_action": str(payload.get("next_action") or ""),
            "note": str(payload.get("note") or ""), "selected_option_id": selected,
        }
        judgment_id = int(judgment["id"]) if selected is not None and judgment else None
        judgment_fingerprint = judgment["input_fingerprint"] if selected is not None and judgment else None
        with self.state.lock, self.state.db:
            self.state.db.execute("""INSERT INTO decisions(project_id,status,owner,next_action,note,selected_option_id,judgment_id,
                            judgment_fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                            (project_id, status, fields["owner"], fields["next_action"], fields["note"], selected,
                             judgment_id, judgment_fingerprint, now_iso()))
            self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
        return self.projects.summary(project_id)

    def gate_action(self, project_id: int, action: str, reason: str) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        if action not in {"restore", "clear"}:
            raise ServiceError("invalid gate action")
        reason = str(reason or "").strip()
        if not reason:
            raise ServiceError("reason is required")
        with self.state.lock, self.state.db:
            if action == "restore":
                self.state.db.execute("INSERT INTO project_gate(project_id,override_action,reason,updated_at) VALUES (?, 'restore', ?, ?) ON CONFLICT(project_id) DO UPDATE SET override_action='restore',reason=excluded.reason,updated_at=excluded.updated_at",
                                (project_id, reason, now_iso()))
            else:
                self.state.db.execute("DELETE FROM project_gate WHERE project_id=?", (project_id,))
            self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
        if action == "restore" and self.settings.ai_config()["configured"]:
            self.queue_project(project_id, schedule=True)
        return self.projects.summary(project_id)

    def link_projects(self, project_id: int, target_project_id: int, relation: str, note: str) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        target_project_id = int(self.records.project_row(target_project_id)["id"])
        if project_id == target_project_id:
            raise ConflictError("不能关联项目自身")
        if relation not in {"same_project", "duplicate", "dismiss"}:
            raise ServiceError("invalid relation")
        with self.state.lock, self.state.db:
            related_context_before = self.analysis_context.related_context_fingerprints((project_id, target_project_id))
            self.state.db.execute("INSERT INTO links(project_id,target_project_id,relation,note,created_at) VALUES (?,?,?,?,?)",
                            (project_id, target_project_id, relation, str(note or ""), now_iso()))
            self.state.db.execute("UPDATE projects SET updated_at=? WHERE id IN (?,?)", (now_iso(), project_id, target_project_id))
            self.analysis_context.mark_related_context_changes(related_context_before)
        return {"ok": True, "message": "人工关联已记录，双方源记录和决定均予保留"}
