"""Project membership, source records and procurement document queries."""
from __future__ import annotations

from typing import Any

import sqlite3
from pathlib import Path
from ..analysis.rules import business_sort_key
from ..core.utils import json_value, safe_join
from ..procurement import build_procurement_summary, document_from_row
from .state import ApplicationState
from .errors import NotFoundError, ConflictError


class ProjectRecords:
    def __init__(self, *, state: ApplicationState) -> None:
        self.state = state

    def record_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key, default in (("equipment_json", []), ("facts_json", []), ("inferences_json", [])):
            out_key = key.removesuffix("_json")
            result[out_key] = json_value(result.pop(key), default)
        payload = json_value(result.pop("source_payload_json", "{}"), {})
        attachments = payload.get("attachments", []) if isinstance(payload, dict) else []
        result["attachments"] = attachments if isinstance(attachments, list) else []
        return result

    def records_for_project(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.state.db.execute("""SELECT r.* FROM records r JOIN project_records pr ON pr.record_id=r.id
                                WHERE pr.project_id=?""", (project_id,)).fetchall()
        records = [self.record_dict(row) for row in rows]
        return sorted(records, key=business_sort_key, reverse=True)

    def document_sources_for_records(self, record_ids: list[int] | tuple[int, ...] | None = None,
                                      *, current_only: bool = True) -> list[dict[str, Any]]:
        current_clause = " WHERE is_current=1" if current_only else ""
        if record_ids is None:
            rows = self.state.db.execute(f"SELECT * FROM procurement_documents{current_clause} ORDER BY record_id,id").fetchall()
        elif not record_ids:
            return []
        else:
            placeholders = ",".join("?" for _ in record_ids)
            rows = self.state.db.execute(
                f"SELECT * FROM procurement_documents WHERE record_id IN ({placeholders})"
                f"{' AND is_current=1' if current_only else ''} ORDER BY record_id,id",
                tuple(int(value) for value in record_ids),
            ).fetchall()
        return [document_from_row(row) for row in rows]

    def procurement_for_project(self, project_id: int) -> dict[str, Any]:
        record_rows = self.state.db.execute(
            "SELECT r.id,r.kind,r.body_text,r.source_payload_json FROM records r JOIN project_records pr ON pr.record_id=r.id "
            "WHERE pr.project_id=? ORDER BY r.id", (project_id,)
        ).fetchall()
        record_ids = [int(row["id"]) for row in record_rows]
        documents = self.document_sources_for_records(record_ids)
        refresh = self.state.db.execute("SELECT * FROM procurement_refreshes WHERE project_id=?", (project_id,)).fetchone()
        return build_procurement_summary(record_rows, documents, refresh)

    def project_row(self, project_id: int) -> sqlite3.Row:
        row = self.state.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            # Project ids are normally the anchor record id. For a grouped
            # record, accepting its record id as an alias keeps detail links
            # resilient without changing the canonical summary id.
            alias = self.state.db.execute("SELECT project_id FROM project_records WHERE record_id=?", (project_id,)).fetchone()
            if alias:
                row = self.state.db.execute("SELECT * FROM projects WHERE id=?", (int(alias["project_id"]),)).fetchone()
        if not row:
            raise NotFoundError("project not found")
        return row

    def latest_judgment(self, project_id: int) -> sqlite3.Row | None:
        return self.state.db.execute("SELECT * FROM judgments WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def latest_job(self, project_id: int) -> sqlite3.Row | None:
        return self.state.db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def latest_decision(self, project_id: int) -> sqlite3.Row | None:
        return self.state.db.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def latest_agent_run(self, project_id: int) -> sqlite3.Row | None:
        return self.state.db.execute("SELECT * FROM agent_runs WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def project_for_record_id(self, record_id: int) -> int | None:
        row = self.state.db.execute("SELECT project_id FROM project_records WHERE record_id=?", (record_id,)).fetchone()
        return int(row[0]) if row else None

    def archive_path(self, record_id: int) -> Path:
        row = self.state.db.execute("SELECT archive_path FROM records WHERE id=?", (record_id,)).fetchone()
        if not row or not row["archive_path"]:
            raise NotFoundError("archive not found")
        try:
            path = safe_join(self.state.archive_root, str(row["archive_path"]))
        except ValueError as exc:
            raise ConflictError("archive path is outside allowed directory") from exc
        if not path.is_file():
            raise NotFoundError("archive not found")
        return path
