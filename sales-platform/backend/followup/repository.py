"""Small persistence adapter for follow-up records.

The adapter keeps SQL and JSON decoding in one place.  Business invariants
live in :mod:`backend.followup.service`, so callers can use this class for
read-only inspection without importing the screening service.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from ..core.utils import json_text, json_value, now_iso
from .errors import FollowupNotFoundError, FollowupValidationError


def _row_as_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    names = [column[0] for column in cursor.description or ()]
    return dict(zip(names, row))


def _project_id(value: Any) -> int:
    if isinstance(value, bool):
        raise FollowupValidationError("project_id must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise FollowupValidationError("project_id must be a positive integer") from exc
    if parsed <= 0 or str(value).strip() != str(parsed):
        raise FollowupValidationError("project_id must be a positive integer")
    return parsed


class FollowupRepository:
    """Repository bound to one sqlite connection.

    Call :func:`backend.followup.schema.ensure_schema` once at application
    startup.  Construction is deliberately read/prepare-only and never runs
    DDL or commits a caller's transaction.
    """

    def __init__(self, conn: sqlite3.Connection):
        if not isinstance(conn, sqlite3.Connection):
            raise TypeError("conn must be a sqlite3.Connection")
        self.conn = conn

    def ensure(self, project_id: Any) -> dict[str, Any]:
        project_id = _project_id(project_id)
        cursor = self.conn.execute("SELECT project_id FROM followups WHERE project_id=?", (project_id,))
        if cursor.fetchone() is None:
            stamp = now_iso()
            self.conn.execute(
                """INSERT INTO followups
                   (project_id, stage, disposition, owner, next_action, outcome,
                    created_at, updated_at)
                   VALUES (?, 'evaluating', 'following', '', '', NULL, ?, ?)""",
                (project_id, stamp, stamp),
            )
            self.add_event(project_id, "created", {})
        return self.followup_row(project_id, required=True)

    def followup_row(self, project_id: Any, *, required: bool = False) -> dict[str, Any] | None:
        project_id = _project_id(project_id)
        cursor = self.conn.execute("SELECT * FROM followups WHERE project_id=?", (project_id,))
        row = _row_as_dict(cursor, cursor.fetchone())
        if row is None and required:
            raise FollowupNotFoundError(f"follow-up for project {project_id} does not exist")
        return row

    def task_row(self, project_id: Any, task_id: Any, *, required: bool = False) -> dict[str, Any] | None:
        project_id = _project_id(project_id)
        task_id = _id(task_id, "task_id")
        cursor = self.conn.execute(
            "SELECT * FROM followup_tasks WHERE project_id=? AND id=?", (project_id, task_id)
        )
        row = _row_as_dict(cursor, cursor.fetchone())
        if row is None and required:
            raise FollowupNotFoundError(f"unknown task id: {task_id}")
        return row

    def material_row(self, project_id: Any, material_id: Any, *, required: bool = False) -> dict[str, Any] | None:
        project_id = _project_id(project_id)
        material_id = _id(material_id, "material_id")
        cursor = self.conn.execute(
            "SELECT * FROM followup_materials WHERE project_id=? AND id=?", (project_id, material_id)
        )
        row = _row_as_dict(cursor, cursor.fetchone())
        if row is None and required:
            raise FollowupNotFoundError(f"unknown material id: {material_id}")
        return row

    def add_event(self, project_id: Any, event_type: str, payload: Mapping[str, Any] | None = None) -> int:
        project_id = _project_id(project_id)
        event_type = str(event_type).strip()
        if not event_type:
            raise FollowupValidationError("event_type must not be blank")
        encoded = payload if payload is not None else {}
        if not isinstance(encoded, Mapping):
            raise FollowupValidationError("event payload must be an object")
        cursor = self.conn.execute(
            "INSERT INTO followup_events(project_id,event_type,payload_json,created_at) VALUES (?,?,?,?)",
            (project_id, event_type, json_text(dict(encoded)), now_iso()),
        )
        return int(cursor.lastrowid)

    def update_followup(self, project_id: Any, values: Mapping[str, Any]) -> None:
        project_id = _project_id(project_id)
        values = dict(values)
        if not values:
            return
        values["updated_at"] = now_iso()
        allowed = {
            "is_following", "stage", "disposition", "owner", "next_action", "note", "outcome", "submitted_at",
            "submission_channel", "receipt_json", "plan_json", "plan_id", "plan_fingerprint",
            "source_fingerprint", "source_version", "needs_review", "changed_tasks_json", "updated_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise FollowupValidationError(f"unknown follow-up fields: {', '.join(sorted(unknown))}")
        assignments = ", ".join(f"{key}=?" for key in values)
        params = [values[key] for key in values] + [project_id]
        cursor = self.conn.execute(
            f"UPDATE followups SET {assignments} WHERE project_id=?",  # values are from a fixed allow-list
            params,
        )
        if cursor.rowcount != 1:
            raise FollowupNotFoundError(f"follow-up for project {project_id} does not exist")

    def insert_task(self, project_id: Any, task: Mapping[str, Any]) -> None:
        project_id = _project_id(project_id)
        values = _task_values(task)
        self.conn.execute(
            """INSERT INTO followup_tasks
               (project_id,id,title,basis,owner,due_at,prerequisites_json,materials_json,
                status,completion_note,skipped_reason,plan_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id, values["id"], values["title"], values["basis"], values["owner"], values["due_at"],
                json_text(values["prerequisites"]), json_text(values["materials"]), values["status"],
                values["completion_note"], values["skipped_reason"], values["plan_id"], now_iso(), now_iso(),
            ),
        )

    def update_task(self, project_id: Any, task_id: Any, values: Mapping[str, Any]) -> None:
        project_id = _project_id(project_id)
        task_id = _id(task_id, "task_id")
        values = dict(values)
        encoded = {"prerequisites", "materials"}
        if "prerequisites" in values:
            values["prerequisites_json"] = json_text(values.pop("prerequisites"))
        if "materials" in values:
            values["materials_json"] = json_text(values.pop("materials"))
        values["updated_at"] = now_iso()
        allowed = {
            "title", "basis", "owner", "due_at", "prerequisites_json", "materials_json", "status",
            "completion_note", "skipped_reason", "plan_id", "updated_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise FollowupValidationError(f"unknown task fields: {', '.join(sorted(unknown))}")
        assignments = ", ".join(f"{key}=?" for key in values)
        cursor = self.conn.execute(
            f"UPDATE followup_tasks SET {assignments} WHERE project_id=? AND id=?",
            [values[key] for key in values] + [project_id, task_id],
        )
        if cursor.rowcount != 1:
            raise FollowupNotFoundError(f"unknown task id: {task_id}")

    def insert_material(self, project_id: Any, material: Mapping[str, Any]) -> None:
        project_id = _project_id(project_id)
        values = _material_values(material)
        self.conn.execute(
            """INSERT INTO followup_materials
               (project_id,id,task_id,name,basis,status,ai_check_status,ai_check_note,provided_at,note,plan_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id, values["id"], values["task_id"], values["name"], values["basis"], values["status"],
                values["ai_check_status"], values["ai_check_note"], values["provided_at"], values["note"],
                values["plan_id"], now_iso(), now_iso(),
            ),
        )

    def update_material(self, project_id: Any, material_id: Any, values: Mapping[str, Any]) -> None:
        project_id = _project_id(project_id)
        material_id = _id(material_id, "material_id")
        values = dict(values)
        values["updated_at"] = now_iso()
        allowed = {
            "task_id", "name", "basis", "status", "ai_check_status", "ai_check_note", "provided_at", "note", "plan_id",
            "updated_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise FollowupValidationError(f"unknown material fields: {', '.join(sorted(unknown))}")
        assignments = ", ".join(f"{key}=?" for key in values)
        cursor = self.conn.execute(
            f"UPDATE followup_materials SET {assignments} WHERE project_id=? AND id=?",
            [values[key] for key in values] + [project_id, material_id],
        )
        if cursor.rowcount != 1:
            raise FollowupNotFoundError(f"unknown material id: {material_id}")

    def state(self, project_id: Any) -> dict[str, Any]:
        project_id = _project_id(project_id)
        row = self.followup_row(project_id)
        if row is None:
            return empty_state()
        cursor = self.conn.execute(
            "SELECT * FROM followup_tasks WHERE project_id=? ORDER BY created_at,id", (project_id,)
        )
        task_rows = [_row_as_dict(cursor, row_value) for row_value in cursor.fetchall()]
        cursor = self.conn.execute(
            "SELECT * FROM followup_materials WHERE project_id=? ORDER BY created_at,id", (project_id,)
        )
        material_rows = [_row_as_dict(cursor, row_value) for row_value in cursor.fetchall()]
        cursor = self.conn.execute(
            "SELECT * FROM followup_events WHERE project_id=? ORDER BY id", (project_id,)
        )
        event_rows = [_row_as_dict(cursor, row_value) for row_value in cursor.fetchall()]
        tasks = [_task_public(row) for row in task_rows if row is not None]
        materials = [_material_public(row) for row in material_rows if row is not None]
        events = [_event_public(row) for row in event_rows if row is not None]
        plan = json_value(row.get("plan_json"), None)
        receipt = json_value(row.get("receipt_json"), None)
        changed_tasks = json_value(row.get("changed_tasks_json"), []) or []
        return {
            "is_following": bool(row["is_following"]),
            "stage": row["stage"],
            "disposition": row["disposition"],
            "owner": row["owner"],
            "next_action": row["next_action"],
            "note": row["note"],
            "outcome": row["outcome"],
            "submitted_at": row["submitted_at"],
            "submission_channel": row["submission_channel"],
            "receipt": receipt,
            "tasks": tasks,
            "materials": materials,
            "events": events,
            "plan": plan,
            "needs_review": bool(row["needs_review"]),
            "changed_tasks": changed_tasks,
        }


def _id(value: Any, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise FollowupValidationError(f"{field} must not be blank")
    result = str(value).strip()
    if not result or len(result) > 256:
        raise FollowupValidationError(f"{field} must not be blank and must be at most 256 characters")
    return result


def _list_value(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (list, tuple)):
        raise FollowupValidationError(f"{field} must be an array")
    return list(value)


def _task_values(task: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(task, Mapping):
        raise FollowupValidationError("task must be an object")
    task_id = _id(task.get("id"), "task.id")
    title = str(task.get("title", "")).strip()
    if not title:
        raise FollowupValidationError("task.title must not be blank")
    basis = str(task.get("basis", "")).strip()
    if basis not in {"required", "conditional", "suggested"}:
        raise FollowupValidationError("task.basis is invalid")
    status = str(task.get("status", "todo")).strip()
    if status not in {"todo", "doing", "done", "skipped"}:
        raise FollowupValidationError("task.status is invalid")
    prerequisites = _list_value(task.get("prerequisites", []), "task.prerequisites")
    materials = _list_value(task.get("materials", []), "task.materials")
    owner = str(task.get("owner", "") or "").strip()
    completion_note = str(task.get("completion_note", "") or "").strip()
    skipped_reason = task.get("skipped_reason")
    if skipped_reason is not None:
        skipped_reason = str(skipped_reason).strip() or None
    return {
        "id": task_id, "title": title, "basis": basis, "owner": owner,
        "due_at": task.get("due_at"), "prerequisites": prerequisites, "materials": materials,
        "status": status, "completion_note": completion_note, "skipped_reason": skipped_reason,
        "plan_id": task.get("plan_id"),
    }


def _material_values(material: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(material, Mapping):
        raise FollowupValidationError("material must be an object")
    material_id = _id(material.get("id"), "material.id")
    name = str(material.get("name", "")).strip()
    if not name:
        raise FollowupValidationError("material.name must not be blank")
    basis = str(material.get("basis", "")).strip()
    if basis not in {"required", "conditional", "suggested"}:
        raise FollowupValidationError("material.basis is invalid")
    status = str(material.get("status", "not_prepared")).strip()
    if status not in {"not_prepared", "preparing", "provided", "pending_check", "needs_supplement"}:
        raise FollowupValidationError("material.status is invalid")
    ai_check_status = str(material.get("ai_check_status", "not_checked")).strip()
    if ai_check_status not in {"not_checked", "checking", "passed", "needs_supplement", "failed"}:
        raise FollowupValidationError("material.ai_check_status is invalid")
    return {
        "id": material_id, "task_id": material.get("task_id"), "name": name, "basis": basis,
        "status": status, "ai_check_status": ai_check_status,
        "ai_check_note": str(material.get("ai_check_note", "") or "").strip(),
        "provided_at": material.get("provided_at"), "note": str(material.get("note", "") or "").strip(),
        "plan_id": material.get("plan_id"),
    }


def _task_public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "title": row["title"], "basis": row["basis"], "owner": row["owner"],
        "due_at": row["due_at"], "prerequisites": json_value(row["prerequisites_json"], []) or [],
        "materials": json_value(row["materials_json"], []) or [], "status": row["status"],
        "completion_note": row["completion_note"], "skipped_reason": row["skipped_reason"],
    }


def _material_public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "task_id": row["task_id"], "name": row["name"], "basis": row["basis"],
        "status": row["status"], "ai_check_status": row["ai_check_status"],
        "ai_check_note": row["ai_check_note"], "provided_at": row["provided_at"], "note": row["note"],
    }


def _event_public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "type": row["event_type"], "payload": json_value(row["payload_json"], {}) or {},
        "created_at": row["created_at"],
    }


def empty_state() -> dict[str, Any]:
    return {
        "is_following": False, "stage": "evaluating", "disposition": "following", "owner": "", "next_action": "", "note": "", "outcome": None,
        "submitted_at": None, "submission_channel": None, "receipt": None, "tasks": [], "materials": [],
        "events": [], "plan": None, "needs_review": False, "changed_tasks": [],
    }


__all__ = ["FollowupRepository", "empty_state"]
