"""Explicit adoption of a proposed follow-up plan."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from ..core.utils import json_text, json_value
from .errors import FollowupValidationError
from .planning import generate_plan, plan_fingerprint
from .repository import FollowupRepository, _project_id
from .validation import (
    _changed_task_ids,
    _material_structural,
    _normalize_plan_material,
    _normalize_plan_task,
    _plan_id,
    _reading_version,
    _required_text,
    _source_fingerprint,
    _task_structural,
    _validate_plan,
)


def adopt_plan(
    conn: sqlite3.Connection,
    project_id: Any,
    plan: Mapping[str, Any] | str | None = None,
    *,
    plan_id: str | None = None,
    owner: str | None = None,
    source_fingerprint: str | None = None,
    source_version: str | None = None,
    procurement: Mapping[str, Any] | None = None,
    explanation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adopt a plan once, preserving all human progress and history.

    ``plan`` may be a plan object, a persisted plan id, or ``None`` (use the
    proposal saved by :func:`save_followup`).  Repeating the same adoption is
    idempotent and does not append another adoption event.
    """

    project_id = _project_id(project_id)
    repo = FollowupRepository(conn)
    if repo.followup_row(project_id) is None:
        repo.ensure(project_id)
    row = repo.followup_row(project_id, required=True)
    assert row is not None

    # Route handlers often pass the decoded POST body as the third argument.
    # Accept that envelope while retaining the direct ``plan`` contract.
    if isinstance(plan, Mapping) and "tasks" not in plan and (
        "plan" in plan or "plan_id" in plan or "owner" in plan or "reading" in plan
    ):
        request = dict(plan)
        plan = request.get("plan")
        plan_id = request.get("plan_id", plan_id)
        owner = request.get("owner", owner)
        source_fingerprint = request.get("source_fingerprint", source_fingerprint)
        source_version = request.get("source_version", request.get("material_version", source_version))
        procurement = request.get("procurement", procurement)
        explanation = request.get("explanation", request.get("reading", explanation))

    source_document = procurement or explanation
    if source_document is not None and not isinstance(source_document, Mapping):
        raise FollowupValidationError("procurement explanation must be an object")
    if plan is not None and isinstance(plan, str):
        plan_id = plan
        plan = None
    if plan is None and source_document is not None:
        plan = generate_plan(source_document)
    if plan is None:
        plan = json_value(row["plan_json"], None)
    if plan is None:
        raise FollowupValidationError("no plan proposal is available to adopt")
    _validate_plan(plan)
    actual_plan_id = _plan_id(plan)
    if plan_id is not None and str(plan_id).strip() != actual_plan_id:
        raise FollowupValidationError("unknown plan id")
    fingerprint = plan_fingerprint(plan)
    if row["plan_fingerprint"] == fingerprint:
        # If an older database has the plan metadata but no adoption event or
        # tasks, continue once; otherwise this is the idempotent fast path.
        adopted = conn.execute(
            "SELECT 1 FROM followup_events WHERE project_id=? AND event_type='plan_adopted' AND payload_json LIKE ? LIMIT 1",
            (project_id, f'%"fingerprint":"{fingerprint}"%'),
        ).fetchone()
        if adopted is not None:
            return repo.state(project_id)

    changes: dict[str, Any] = {
        "is_following": 1,
        "plan_json": json_text(dict(plan)), "plan_id": actual_plan_id,
        "plan_fingerprint": fingerprint,
    }
    if source_fingerprint is None and source_document is not None:
        source_fingerprint = _source_fingerprint(None, source_document)
    if source_version is None and source_document is not None:
        source_version = _reading_version(source_document)
    if source_fingerprint:
        source_fingerprint = _required_text(source_fingerprint, "source_fingerprint")
        if row["source_fingerprint"] and row["source_fingerprint"] != source_fingerprint:
            changes["needs_review"] = 1
        changes["source_fingerprint"] = source_fingerprint
    if source_version is not None:
        source_version = _required_text(source_version, "source_version")
        if row["source_version"] and row["source_version"] != source_version:
            changes["needs_review"] = 1
        changes["source_version"] = source_version
    if row["needs_review"]:
        changes["needs_review"] = 1

    changed_tasks = json_value(row["changed_tasks_json"], []) or []
    if row["plan_fingerprint"] and row["plan_fingerprint"] != fingerprint:
        changed_tasks = _changed_task_ids(row, plan, changed_tasks)
    task_ids: list[str] = []
    material_ids: list[str] = []
    material_by_id = {str(material["id"]): material for material in plan.get("materials", [])}
    plan_tasks = list(plan.get("tasks", []))
    # A proposal marked ineligible is kept for explanation but never creates a
    # confirmed bidding workflow.
    if not plan.get("eligible", True):
        changes.setdefault("needs_review", 0)
        repo.update_followup(project_id, changes)
        repo.add_event(project_id, "plan_rejected", {"plan_id": actual_plan_id, "reason": plan.get("reason", "")})
        conn.commit()
        return repo.state(project_id)

    for raw_task in plan_tasks:
        task = _normalize_plan_task(raw_task, material_by_id)
        task_id = task["id"]
        task_ids.append(task_id)
        existing = repo.task_row(project_id, task_id)
        if existing is None:
            task_owner = task["owner"] or (owner.strip() if isinstance(owner, str) else "") or row["owner"]
            task["owner"] = task_owner
            task["plan_id"] = actual_plan_id
            repo.insert_task(project_id, task)
        else:
            structural = _task_structural(existing, task)
            if structural:
                if task_id not in changed_tasks:
                    changed_tasks.append(task_id)
                if existing["status"] in {"done", "skipped"}:
                    # A changed reading may make a completed action worth
                    # reviewing, but it must not rewrite the historical task
                    # definition or its completion record.
                    repo.update_task(project_id, task_id, {"plan_id": actual_plan_id})
                else:
                    repo.update_task(
                        project_id,
                        task_id,
                        {
                            "title": task["title"], "basis": task["basis"], "due_at": task["due_at"],
                            "prerequisites": task["prerequisites"], "materials": task["materials"], "plan_id": actual_plan_id,
                        },
                    )
            else:
                # Keep completed state, completion note, and owner untouched;
                # the plan id is safe metadata to update.
                repo.update_task(project_id, task_id, {"plan_id": actual_plan_id})
            if not existing["owner"] and task["owner"]:
                repo.update_task(project_id, task_id, {"owner": task["owner"]})
        for raw_material in plan.get("materials", []):
            if str(raw_material.get("task_id", "")) != task_id:
                continue
            material = _normalize_plan_material(raw_material, task_id=task_id, plan_id=actual_plan_id)
            material_id = material["id"]
            material_ids.append(material_id)
            existing_material = repo.material_row(project_id, material_id)
            if existing_material is None:
                repo.insert_material(project_id, material)
            else:
                if _material_structural(existing_material, material):
                    if task_id not in changed_tasks:
                        changed_tasks.append(task_id)
                    repo.update_material(
                        project_id,
                        material_id,
                        {"task_id": task_id, "name": material["name"], "basis": material["basis"], "plan_id": actual_plan_id},
                    )
                else:
                    repo.update_material(project_id, material_id, {"plan_id": actual_plan_id})

    if changed_tasks:
        changes["changed_tasks_json"] = json_text(sorted(set(changed_tasks)))
        changes["needs_review"] = 1
    repo.update_followup(project_id, changes)
    repo.add_event(
        project_id,
        "plan_adopted",
        {"plan_id": actual_plan_id, "fingerprint": fingerprint, "task_ids": task_ids, "material_ids": material_ids},
    )
    if owner is not None and not row["owner"]:
        owner_text = _required_text(owner, "owner")
        repo.update_followup(project_id, {"owner": owner_text})
        repo.add_event(project_id, "owner_changed", {"owner": owner_text})
    conn.commit()
    return repo.state(project_id)


__all__ = ["adopt_plan"]
