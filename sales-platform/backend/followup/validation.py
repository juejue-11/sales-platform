"""Validation and normalization for follow-up plans and updates."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..core.utils import json_text, json_value
from .errors import FollowupConflictError, FollowupValidationError
from .planning import BASES, MATERIAL_STATUSES, TASK_STATUSES, _iso_date
from .repository import _id

STAGES = ("evaluating", "working", "submitted", "awaiting_result", "finished")
DISPOSITIONS = ("following", "paused", "abandoned")
OUTCOMES = ("won", "lost", "abandoned", "other")
AI_CHECK_STATUSES = ("not_checked", "checking", "passed", "needs_supplement", "failed")


def _validate_plan(plan: Any) -> None:
    if not isinstance(plan, Mapping):
        raise FollowupValidationError("plan must be an object")
    if not isinstance(plan.get("eligible", True), bool):
        raise FollowupValidationError("plan.eligible must be boolean")
    tasks = plan.get("tasks", [])
    materials = plan.get("materials", [])
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes, bytearray)):
        raise FollowupValidationError("plan.tasks must be an array")
    if not isinstance(materials, Sequence) or isinstance(materials, (str, bytes, bytearray)):
        raise FollowupValidationError("plan.materials must be an array")
    if not plan.get("eligible", True) and (tasks or materials):
        raise FollowupValidationError("an ineligible plan cannot contain tasks or materials")
    task_ids: set[str] = set()
    for raw_task in tasks:
        task = _normalize_plan_task(raw_task, {str(item.get("id")): item for item in materials if isinstance(item, Mapping) and item.get("id") is not None})
        if task["id"] in task_ids:
            raise FollowupValidationError(f"duplicate task id: {task['id']}")
        task_ids.add(task["id"])
    material_ids: set[str] = set()
    for raw_material in materials:
        if not isinstance(raw_material, Mapping):
            raise FollowupValidationError("plan material must be an object")
        material = _normalize_plan_material(raw_material, task_id=raw_material.get("task_id"), plan_id=None)
        if material["id"] in material_ids:
            raise FollowupValidationError(f"duplicate material id: {material['id']}")
        material_ids.add(material["id"])
        if material["task_id"] is not None and str(material["task_id"]) not in task_ids:
            raise FollowupValidationError(f"unknown task id in material: {material['task_id']}")
    for raw_task in tasks:
        task_id = _id(raw_task.get("id"), "task.id")
        for material_id in _array(raw_task.get("materials", []), "task.materials"):
            if _id(material_id if not isinstance(material_id, Mapping) else material_id.get("id"), "task.materials[]") not in material_ids:
                raise FollowupValidationError(f"unknown material id in task {task_id}")


def _normalize_plan_task(raw: Any, material_by_id: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FollowupValidationError("plan task must be an object")
    task_id = _id(raw.get("id"), "task.id")
    title = _required_text(raw.get("title"), "task.title")
    basis = _validate_basis(raw.get("basis"), "task.basis")
    owner = _optional_text_value(raw.get("owner", ""))
    due_at = _iso_date(raw.get("due_at"), field="task.due_at")
    prerequisites = _array(raw.get("prerequisites", []), "task.prerequisites")
    materials_raw = _array(raw.get("materials", []), "task.materials")
    materials: list[str] = []
    for value in materials_raw:
        value = value.get("id") if isinstance(value, Mapping) else value
        materials.append(_id(value, "task.materials[]"))
    return {
        "id": task_id, "title": title, "basis": basis, "owner": owner, "due_at": due_at,
        "prerequisites": prerequisites, "materials": materials, "status": "todo",
        "completion_note": "", "skipped_reason": None, "plan_id": None,
    }


def _normalize_plan_material(raw: Any, *, task_id: Any, plan_id: str | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FollowupValidationError("plan material must be an object")
    material_id = _id(raw.get("id"), "material.id")
    if task_id is not None:
        task_id = _id(task_id, "material.task_id")
    return {
        "id": material_id, "task_id": task_id, "name": _required_text(raw.get("name"), "material.name"),
        "basis": _validate_basis(raw.get("basis"), "material.basis"), "status": "not_prepared",
        "ai_check_status": "not_checked", "ai_check_note": "", "provided_at": None, "note": "", "plan_id": plan_id,
    }


def _task_structural(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    return any(
        existing[key] != incoming[key]
        for key in ("title", "basis", "due_at")
    ) or json_value(existing["prerequisites_json"], []) != incoming["prerequisites"] or json_value(existing["materials_json"], []) != incoming["materials"]


def _material_structural(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    return any(existing[key] != incoming[key] for key in ("task_id", "name", "basis"))


def _changed_task_ids(current: Mapping[str, Any], plan: Mapping[str, Any], existing: list[Any]) -> list[str]:
    ids = {str(item) for item in existing}
    old_plan = json_value(current.get("plan_json"), {}) or {}
    old_tasks = {
        str(item.get("id")): item
        for item in old_plan.get("tasks", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    new_tasks = {
        str(item.get("id")): item
        for item in plan.get("tasks", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    for task_id in set(old_tasks) | set(new_tasks):
        if old_tasks.get(task_id) != new_tasks.get(task_id):
            ids.add(task_id)
    return sorted(ids)


def _plan_id(plan: Mapping[str, Any]) -> str:
    value = str(plan.get("id", "")).strip()
    if not value:
        return "plan-" + hashlib.sha256(json_text(dict(plan)).encode("utf-8")).hexdigest()[:16]
    if len(value) > 256:
        raise FollowupValidationError("plan.id is too long")
    return value


def _source_fingerprint(value: Any, source_document: Mapping[str, Any] | None) -> str | None:
    if value is not None:
        return _required_text(value, "source_fingerprint")
    if source_document is None:
        return None
    return hashlib.sha256(json_text(dict(source_document)).encode("utf-8")).hexdigest()


def _reading_version(reading: Mapping[str, Any]) -> str | None:
    """Read the version names used by the reading contract."""

    for key in ("material_version", "source_version", "version", "revision"):
        if key in reading and reading[key] not in (None, ""):
            return _required_text(reading[key], key)
    return None


def _validate_stage(value: Any) -> str:
    value = _required_text(value, "stage")
    if value not in STAGES:
        raise FollowupValidationError("stage is invalid")
    return value


def _validate_disposition(value: Any) -> str:
    value = _required_text(value, "disposition")
    if value not in DISPOSITIONS:
        raise FollowupValidationError("disposition is invalid")
    return value


def _validate_outcome(value: Any) -> str | None:
    if value in (None, ""):
        return None
    value = _required_text(value, "outcome")
    if value not in OUTCOMES:
        raise FollowupValidationError("outcome is invalid")
    return value


def _validate_stage_transition(current: str, incoming: str) -> None:
    if STAGES.index(incoming) < STAGES.index(current):
        raise FollowupConflictError("follow-up stage cannot move backward")


def _validate_task_transition(current: str, incoming: str) -> None:
    if current in {"done", "skipped"} and incoming != current:
        raise FollowupConflictError("completed or skipped task cannot move backward")


def _validate_task_status(value: Any) -> str:
    value = _required_text(value, "task.status")
    if value not in TASK_STATUSES:
        raise FollowupValidationError("task.status is invalid")
    return value


def _validate_material_status(value: Any) -> str:
    value = _required_text(value, "material.status")
    if value not in MATERIAL_STATUSES:
        raise FollowupValidationError("material.status is invalid")
    return value


def _validate_ai_status(value: Any) -> str:
    value = _required_text(value, "material.ai_check_status")
    if value not in AI_CHECK_STATUSES:
        raise FollowupValidationError("material.ai_check_status is invalid")
    return value


def _validate_basis(value: Any, field: str) -> str:
    value = _required_text(value, field)
    if value not in BASES:
        raise FollowupValidationError(f"{field} is invalid")
    return value


def _required_text(value: Any, field: str) -> str:
    if value is None:
        raise FollowupValidationError(f"{field} must not be blank")
    text = str(value).strip()
    if not text:
        raise FollowupValidationError(f"{field} must not be blank")
    if len(text) > 4000:
        raise FollowupValidationError(f"{field} is too long")
    return text


def _optional_text(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        if key in value:
            return _optional_text_value(value[key])
    return None


def _optional_text_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > 4000:
        raise FollowupValidationError("text field is too long")
    return text


def _reason(value: Any) -> str:
    return _optional_text_value(value)


def _array(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise FollowupValidationError(f"{field} must be an array")
    return list(value)


def _json_safe_patch(values: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key.endswith("_json"):
            result[key[:-5]] = json_value(value, value)
        else:
            result[key] = value
    return result
