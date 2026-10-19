"""Versioned procurement readings and source-grounded gate decisions."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import sqlite3
from collections.abc import Mapping
from datetime import datetime
from ..analysis.rules import gate_for_project
from ..core.utils import SHANGHAI, json_text, json_value, now_iso, parse_timestamp, sha256_text
from ..procurement.milestones import submission_deadline
from ..procurement.schedule import restore_milestones
from ..procurement.reading import build_material, empty_reading, material_version as reading_material_version
from .state import ApplicationState

if TYPE_CHECKING:
    from .records import ProjectRecords
    from .settings import ApplicationSettings


class ProcurementReadings:
    def __init__(self, *, state: ApplicationState, records: ProjectRecords, settings: ApplicationSettings) -> None:
        self.state = state
        self.records = records
        self.settings = settings

    def reading_corrections(self, project_id: int) -> list[dict[str, Any]]:
        """Read optional human corrections without coupling to supplements.py.

        The corrections table is created by the supplements workstream.  This
        branch must also run against databases created before that table
        existed, so the lookup is deliberately capability-detected.
        """

        table = self.state.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='procurement_corrections'"
        ).fetchone()
        if table is None:
            return []
        rows = self.state.db.execute(
            "SELECT id,project_id,requirement_id,text,reason,material_version,created_at "
            "FROM procurement_corrections WHERE project_id=? ORDER BY id", (int(project_id),)
        ).fetchall()
        return [dict(row) for row in rows]

    def reading_material(self, project_id: int) -> dict[str, Any]:
        records = self.records.records_for_project(int(project_id))
        documents = self.records.document_sources_for_records([int(record["id"]) for record in records
                                                         if record.get("id") is not None])
        corrections = self.reading_corrections(int(project_id))
        version = reading_material_version(records, documents, corrections)
        return build_material(records, documents, version=version, corrections=corrections)

    def reading_row(self, project_id: int, material_version: str | None = None) -> sqlite3.Row | None:
        if material_version:
            return self.state.db.execute(
                "SELECT * FROM procurement_readings WHERE project_id=? AND material_version=? "
                "ORDER BY id DESC LIMIT 1", (int(project_id), material_version)
            ).fetchone()
        return self.state.db.execute(
            "SELECT * FROM procurement_readings WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (int(project_id),)
        ).fetchone()

    @staticmethod
    def reading_json(value: Any, default: Any) -> Any:
        parsed = json_value(value, default)
        return parsed if isinstance(parsed, type(default)) else default

    def reading_public(self, row: sqlite3.Row | None, current_material_version: str,
                        *, configured: bool | None = None) -> dict[str, Any]:
        configured = self.settings.ai_config()["configured"] if configured is None else configured
        if row is None:
            return empty_reading(current_material_version, status="pending" if configured else "not_configured")
        status = str(row["status"] or "partial")
        if status not in {"pending", "complete", "partial", "failed", "not_configured"}:
            status = "failed"
        stale = str(row["material_version"] or "") != current_material_version
        return {
            "version": int(row["version"] or 1), "id": int(row["id"]), "revision": int(row["revision"] or 1),
            "material_version": str(row["material_version"] or ""), "status": status, "stale": stale,
            "summary": str(row["summary"] or ""), "demand_type": str(row["demand_type"] or "unknown"),
            "stage": str(row["stage"] or ""), "buyer": str(row["buyer"] or ""),
            "region": str(row["region"] or ""), "category": self.reading_json(row["category_json"], []),
            "sections": self.reading_json(row["sections_json"], []),
            "milestones": self.reading_json(row["milestones_json"], []),
            "coverage": self.reading_json(row["coverage_json"], []),
            "conflicts": self.reading_json(row["conflicts_json"], []),
            "unresolved": self.reading_json(row["unresolved_json"], []),
            "error": str(row["error"] or "") if row["error"] else None,
            "updated_at": row["updated_at"],
        }

    def procurement_reading_for_project(self, project_id: int, *, material: dict[str, Any] | None = None) -> dict[str, Any]:
        material = material or self.reading_material(int(project_id))
        current_version = str(material.get("material_version") or "")
        row = self.reading_row(int(project_id), current_version)
        if row is None:
            # Keep a previous explanation visible as stale evidence when the
            # source package changed. The current row (if any) is selected
            # below only when no exact-version row exists.
            row = self.reading_row(int(project_id))
        # Legacy injected judgment providers do not opt into the procurement
        # reading protocol.  Do not manufacture a pending reading in their
        # project snapshot, which would perturb their existing fingerprints.
        if row is None and not self.reading_gate_enabled():
            return empty_reading(current_version, status="not_configured")
        public = self.reading_public(row, current_version)
        # A pending or failed retry must not erase a previously successful
        # explanation. Expose the old facts with the current processing status
        # and an explicit limitation; gate decisions still inspect the latest
        # row directly, so an incomplete retry cannot make an old deadline
        # look verified.
        if row is not None and str(row["status"] or "") in {"pending", "failed"}:
            fallback = self.state.db.execute(
                "SELECT * FROM procurement_readings WHERE project_id=? AND material_version=? "
                "AND status IN ('complete','partial') AND id<? ORDER BY id DESC LIMIT 1",
                (int(project_id), current_version, int(row["id"])),
            ).fetchone()
            if fallback is not None:
                previous = self.reading_public(fallback, current_version)
                for key in ("summary", "demand_type", "stage", "buyer", "region", "category",
                            "sections", "milestones", "coverage", "conflicts"):
                    public[key] = previous[key]
                unresolved = list(previous.get("unresolved") or [])
                unresolved.append("本轮采购阅读尚未成功，以上保留上一次已保存说明供复核")
                public["unresolved"] = list(dict.fromkeys(unresolved))
                public["status"] = str(row["status"] or "failed")
                public["id"] = int(row["id"])
                public["revision"] = int(row["revision"] or 1)
                public["error"] = str(row["error"] or "") or None
                public["updated_at"] = row["updated_at"]
        return restore_milestones(public, material)

    def save_procurement_reading(self, project_id: int, reading: Mapping[str, Any], *, material_version: str,
                                  config: Mapping[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        value = dict(reading)
        status = str(value.get("status") or "partial")
        if status not in {"pending", "complete", "partial", "failed", "not_configured"}:
            status = "failed"
        category = value.get("category") if isinstance(value.get("category"), list) else []
        sections = value.get("sections") if isinstance(value.get("sections"), list) else []
        milestones = value.get("milestones") if isinstance(value.get("milestones"), list) else []
        coverage = value.get("coverage") if isinstance(value.get("coverage"), list) else []
        conflicts = value.get("conflicts") if isinstance(value.get("conflicts"), list) else []
        unresolved = value.get("unresolved") if isinstance(value.get("unresolved"), list) else []
        error_value = str(error or value.get("error") or "")[:500] or None
        current = now_iso()
        model = str((config or {}).get("model") or "")[:256]
        fingerprint = self.reading_request_fingerprint(material_version, config or {})
        with self.state.lock, self.state.db:
            previous = self.state.db.execute(
                "SELECT COALESCE(MAX(revision),0) FROM procurement_readings WHERE project_id=? AND material_version=?",
                (int(project_id), material_version),
            ).fetchone()
            revision = int(previous[0] or 0) + 1 if previous else 1
            self.state.db.execute(
                """INSERT INTO procurement_readings
                (project_id,version,revision,material_version,status,stale,summary,demand_type,stage,buyer,region,
                 category_json,sections_json,milestones_json,coverage_json,conflicts_json,unresolved_json,
                 input_fingerprint,model,error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(project_id), int(value.get("version") or 1), revision, material_version, status, 0,
                 str(value.get("summary") or "")[:4_000], str(value.get("demand_type") or "unknown")[:20],
                 str(value.get("stage") or "")[:200], str(value.get("buyer") or "")[:500],
                 str(value.get("region") or "")[:500], json_text(category), json_text(sections),
                 json_text(milestones), json_text(coverage), json_text(conflicts), json_text(unresolved),
                 fingerprint, model, error_value, current, current),
            )
        return self.procurement_reading_for_project(int(project_id))

    @staticmethod
    def reading_request_fingerprint(material_version: str, config: Mapping[str, Any]) -> str:
        return sha256_text(json_text({"material_version": material_version,
                                      "model": config.get("model"), "base_url": config.get("base_url"),
                                      "engine": config.get("engine"), "enabled": config.get("enabled")}))

    def reading_gate_enabled(self) -> bool:
        return (not self.state.injected_provider
                or callable(getattr(self.state.provider, "read_procurement", None))
                or callable(getattr(self.state.provider, "read", None))
                or bool(getattr(self.state.provider, "supports_procurement_reading", False)))

    def gate(self, records: list[dict[str, Any]], project_id: int) -> tuple[str, list[str]]:
        override_row = self.state.db.execute("SELECT * FROM project_gate WHERE project_id=?", (project_id,)).fetchone()
        override = dict(override_row) if override_row else None
        if not self.reading_gate_enabled():
            return gate_for_project(records, self.settings.get_settings(), now=datetime.now(SHANGHAI), override=override)

        # Collector deadline_at is not known to be a submission deadline.  It
        # may be the public-document acquisition window, so an expired value
        # cannot block the first source-grounded reading.  Preserve all other
        # rule checks and let the normalized submission milestone decide
        # expiration after a successful reading.
        now = datetime.now(SHANGHAI)
        adjusted: list[dict[str, Any]] = []
        for record in records:
            value = dict(record)
            deadline = parse_timestamp(value.get("deadline_at"), deadline=True)
            if deadline and deadline < now:
                value["deadline_at"] = None
            adjusted.append(value)
        gate, reasons = gate_for_project(adjusted, self.settings.get_settings(), now=now, override=override)

        try:
            material = self.reading_material(int(project_id))
            row = self.reading_row(int(project_id), str(material.get("material_version") or ""))
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            row = None
        if row is None:
            if gate == "expired":
                gate = "review"
                reasons.append("reading has not verified a submission deadline")
            return gate, reasons
        status = str(row["status"] or "")
        verified_submission = submission_deadline(restore_milestones({
            "status": status, "stale": bool(row["stale"]),
            "conflicts": json_value(row["conflicts_json"], []),
            "milestones": json_value(row["milestones_json"], []),
        }, material))
        if verified_submission is not None and verified_submission < now:
            return "expired", reasons + ["reading verified the submission deadline has passed"]
        if gate == "expired":
            return "review", reasons + ["raw collector deadline is not a verified submission deadline"]
        return gate, reasons
