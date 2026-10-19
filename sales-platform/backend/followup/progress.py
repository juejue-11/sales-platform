"""Task and material progress updates with immutable-history rules."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from ..core.utils import json_value
from .errors import FollowupConflictError, FollowupValidationError
from .planning import _iso_date
from .repository import FollowupRepository, _id, _project_id
from .validation import (
    _array,
    _json_safe_patch,
    _optional_text_value,
    _reason,
    _required_text,
    _validate_ai_status,
    _validate_basis,
    _validate_material_status,
    _validate_task_status,
    _validate_task_transition,
)


def update_task(
    conn: sqlite3.Connection,
    project_id: Any,
    task_id: Any,
    patch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update one task while preserving completed/skipped records."""

    if patch is None:
        patch = {}
    if not isinstance(patch, Mapping):
        raise FollowupValidationError("task patch must be an object")
    project_id = _project_id(project_id)
    repo = FollowupRepository(conn)
    row = repo.task_row(project_id, task_id, required=True)
    assert row is not None
    values = dict(patch)
    if "skip_reason" in values and "skipped_reason" not in values:
        values["skipped_reason"] = values.pop("skip_reason")
    allowed = {
        "title", "basis", "owner", "due_at", "prerequisites", "materials", "status",
        "completion_note", "skipped_reason", "reason",
    }
    unknown = set(values) - allowed
    if unknown:
        raise FollowupValidationError(f"unknown task fields: {', '.join(sorted(unknown))}")

    # ``reason`` is an audit-only explanation for changing a task definition.
    # It is deliberately independent from ``skipped_reason``: completing a
    # task or skipping it must never smuggle a definition change through the
    # status fields.
    adjustment_reason: str | None = None
    if "reason" in values:
        adjustment_reason = _required_text(values.pop("reason"), "task.reason")

    if "title" in values:
        values["title"] = _required_text(values["title"], "task.title")
    if "basis" in values:
        values["basis"] = _validate_basis(values["basis"], "task.basis")
    if "owner" in values:
        values["owner"] = _optional_text_value(values["owner"])
    if "due_at" in values:
        values["due_at"] = _iso_date(values["due_at"], field="task.due_at")
    if "prerequisites" in values:
        values["prerequisites"] = _array(values["prerequisites"], "task.prerequisites")
    if "materials" in values:
        material_ids = _array(values["materials"], "task.materials")
        normalized_material_ids: list[str] = []
        for material_id in material_ids:
            if isinstance(material_id, Mapping):
                material_id = material_id.get("id")
            normalized = _id(material_id, "task.materials[]")
            if repo.material_row(project_id, normalized) is None:
                raise FollowupValidationError(f"unknown material id: {normalized}")
            normalized_material_ids.append(normalized)
        values["materials"] = normalized_material_ids

    definition_fields = ("title", "basis", "due_at", "prerequisites", "materials")
    existing_definition = {
        "title": row["title"],
        "basis": row["basis"],
        "due_at": row["due_at"],
        "prerequisites": json_value(row["prerequisites_json"], []) or [],
        "materials": json_value(row["materials_json"], []) or [],
    }
    definition_changes = {
        key: values[key]
        for key in definition_fields
        if key in values and values[key] != existing_definition[key]
    }
    if row["status"] in {"done", "skipped"} and definition_changes:
        raise FollowupConflictError(
            "completed or skipped task definitions cannot be edited; keep the original task and review a replacement"
        )
    if definition_changes and not adjustment_reason:
        raise FollowupValidationError("task definition changes require a non-blank reason")
    if row["status"] in {"done", "skipped"}:
        for key in ("status", "completion_note", "skipped_reason"):
            if key in values and str(values[key] or "").strip() != str(row[key] or "").strip():
                raise FollowupConflictError("completed or skipped task records cannot be overwritten")
        # Preserve every immutable task record; a repeated identical patch is
        # harmless and returns the current state.  ``owner`` remains editable
        # as an explicit assignment, because it is not task definition data.
        values = {
            key: value
            for key, value in values.items()
            if key not in {"status", "completion_note", "skipped_reason"}
        }
    if "status" in values:
        values["status"] = _validate_task_status(values["status"])
        if values["status"] == "skipped":
            reason = _reason(values.get("skipped_reason", row["skipped_reason"]))
            if not reason:
                raise FollowupValidationError("skipping a task requires a non-blank reason")
            values["skipped_reason"] = reason
        elif values["status"] != "skipped" and "skipped_reason" in values:
            values["skipped_reason"] = None
    if "completion_note" in values:
        values["completion_note"] = _optional_text_value(values["completion_note"])
    if "skipped_reason" in values and values["skipped_reason"] is not None:
        values["skipped_reason"] = _required_text(values["skipped_reason"], "task.skipped_reason")
    if "status" in values and values["status"] != row["status"]:
        _validate_task_transition(row["status"], values["status"])
    if values:
        repo.update_task(project_id, task_id, values)
        event_payload: dict[str, Any] = {
            "task_id": str(task_id),
            "changes": _json_safe_patch(values),
        }
        if definition_changes:
            event_payload["definition_changed"] = True
            event_payload["reason"] = adjustment_reason
        repo.add_event(project_id, "task_updated", event_payload)
        conn.commit()
    return repo.state(project_id)


def update_material(
    conn: sqlite3.Connection,
    project_id: Any,
    material_id: Any,
    patch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update preparation or AI-check metadata for one material.

    AI check status is deliberately stored separately from ``status``.  A
    passed check never becomes an external submission or a submitted stage.
    """

    if patch is None:
        patch = {}
    if not isinstance(patch, Mapping):
        raise FollowupValidationError("material patch must be an object")
    project_id = _project_id(project_id)
    repo = FollowupRepository(conn)
    row = repo.material_row(project_id, material_id, required=True)
    assert row is not None
    values = dict(patch)
    ai_check = values.pop("ai_check", None)
    if ai_check is not None:
        if not isinstance(ai_check, Mapping):
            raise FollowupValidationError("material.ai_check must be an object")
        if "status" in ai_check:
            values["ai_check_status"] = ai_check["status"]
        if "note" in ai_check:
            values["ai_check_note"] = ai_check["note"]
    if "check_status" in values and "ai_check_status" not in values:
        values["ai_check_status"] = values.pop("check_status")
    allowed = {"task_id", "name", "basis", "status", "ai_check_status", "ai_check_note", "provided_at", "note"}
    unknown = set(values) - allowed
    if unknown:
        raise FollowupValidationError(f"unknown material fields: {', '.join(sorted(unknown))}")
    if "task_id" in values and values["task_id"] is not None:
        task_id = _id(values["task_id"], "material.task_id")
        if repo.task_row(project_id, task_id) is None:
            raise FollowupValidationError(f"unknown task id: {task_id}")
        values["task_id"] = task_id
    if "name" in values:
        values["name"] = _required_text(values["name"], "material.name")
    if "basis" in values:
        values["basis"] = _validate_basis(values["basis"], "material.basis")
    if "status" in values:
        values["status"] = _validate_material_status(values["status"])
    if "ai_check_status" in values:
        values["ai_check_status"] = _validate_ai_status(values["ai_check_status"])
    if "ai_check_note" in values:
        values["ai_check_note"] = _optional_text_value(values["ai_check_note"])
    if "provided_at" in values:
        values["provided_at"] = _iso_date(values["provided_at"], field="material.provided_at")
    if "note" in values:
        values["note"] = _optional_text_value(values["note"])
    if values:
        repo.update_material(project_id, material_id, values)
        event_values = _json_safe_patch(values)
        repo.add_event(project_id, "material_updated", {"material_id": str(material_id), "changes": event_values})
        conn.commit()
    return repo.state(project_id)


__all__ = ["update_material", "update_task"]
