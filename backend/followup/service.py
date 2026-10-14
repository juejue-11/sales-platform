"""Public follow-up interface and state-transition rules.

Every function accepts a sqlite connection and a project id.  The service
layer here is intentionally small: it validates user input, applies
monotonic/immutable-history rules, and delegates SQL to
``FollowupRepository``.  It has no HTTP, service, network, or model imports.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ..core.utils import json_text, json_value, now_iso
from .errors import FollowupConflictError, FollowupNotFoundError, FollowupValidationError
from .planning import _iso_date, generate_plan, plan_fingerprint
from .repository import FollowupRepository, _id, _project_id, empty_state

STAGES = ("evaluating", "working", "submitted", "awaiting_result", "finished")
DISPOSITIONS = ("following", "paused", "abandoned")
OUTCOMES = ("won", "lost", "abandoned", "other")
TASK_STATUSES = ("todo", "doing", "done", "skipped")
MATERIAL_STATUSES = ("not_prepared", "preparing", "provided", "pending_check", "needs_supplement")
AI_CHECK_STATUSES = ("not_checked", "checking", "passed", "needs_supplement", "failed")
BASES = ("required", "conditional", "suggested")


def get_followup(
    conn: sqlite3.Connection,
    project_id: Any,
    reading: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a project follow-up, optionally recording a newer reading.

    Passing ``reading`` is a convenience for the HTTP GET integration.  It
    saves the proposal and material version, but it does not adopt tasks.
    ``note_material_change`` can be used when the caller only wants to mark
    changed source material and keep the previously proposed plan untouched.
    """

    project_id = _project_id(project_id)
    if reading is not None:
        if not isinstance(reading, Mapping):
            raise FollowupValidationError("reading must be an object")
        return save_followup(
            conn,
            project_id,
            {"reading": reading, "source_version": _reading_version(reading)},
        )
    return FollowupRepository(conn).state(project_id)


def save_followup(
    conn: sqlite3.Connection,
    project_id: Any,
    payload: Mapping[str, Any] | None = None,
    *,
    procurement: Mapping[str, Any] | None = None,
    explanation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/update the follow-up header and optionally save a plan proposal.

    Saving a ``plan`` or a procurement explanation only stores the proposal.
    It never creates tasks; callers must use :func:`adopt_plan` explicitly.
    """

    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise FollowupValidationError("follow-up input must be an object")
    project_id = _project_id(project_id)
    repo = FollowupRepository(conn)
    if repo.followup_row(project_id) is None:
        repo.ensure(project_id)
    row = repo.followup_row(project_id, required=True)
    assert row is not None
    current = dict(row)

    # Accept both names used by the reading contract and the narrower public
    # function arguments.  Explicit function arguments win.
    source_document = procurement or explanation or payload.get("procurement") or payload.get("explanation") or payload.get("reading")
    if source_document is not None and not isinstance(source_document, Mapping):
        raise FollowupValidationError("procurement explanation must be an object")
    incoming_plan = payload.get("plan")
    if incoming_plan is None and source_document is not None:
        incoming_plan = generate_plan(source_document)
    if incoming_plan is not None:
        _validate_plan(incoming_plan)

    changes: dict[str, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []
    if "is_following" in payload or "active" in payload:
        raw_active = payload.get("is_following", payload.get("active"))
        if not isinstance(raw_active, bool):
            raise FollowupValidationError("is_following must be boolean")
        if bool(current["is_following"]) != raw_active:
            changes["is_following"] = int(raw_active)
            events.append(("followup_activation_changed", {"is_following": raw_active}))
    stage = _validate_stage(payload.get("stage", current["stage"]))
    _validate_stage_transition(current["stage"], stage)
    if stage != current["stage"]:
        changes["stage"] = stage
        events.append(("stage_changed", {"from": current["stage"], "to": stage}))

    disposition = _validate_disposition(payload.get("disposition", current["disposition"]))
    if disposition != current["disposition"]:
        if current["disposition"] == "abandoned" and disposition != "abandoned":
            raise FollowupConflictError("an abandoned follow-up cannot be reopened")
        reason = _reason(payload.get("reason"))
        if disposition in {"paused", "abandoned"} and not reason:
            raise FollowupValidationError("a reason is required when pausing or abandoning a follow-up")
        changes["disposition"] = disposition
        events.append(("disposition_changed", {"from": current["disposition"], "to": disposition, "reason": reason}))

    incoming_owner = _optional_text(payload, "owner")
    if incoming_owner is not None:
        if current["owner"] and incoming_owner != current["owner"]:
            # A re-generated reading/plan cannot silently reassign a human
            # owner.  An explicit owner update gets recorded as ignored so it
            # remains visible in the history rather than being destructive.
            events.append(("owner_preserved", {"owner": current["owner"], "requested": incoming_owner}))
        elif incoming_owner != current["owner"]:
            changes["owner"] = incoming_owner
            events.append(("owner_changed", {"owner": incoming_owner}))

    next_action = _optional_text(payload, "next_action", "next_step")
    if next_action is not None and next_action != current["next_action"]:
        changes["next_action"] = next_action
        events.append(("next_action_changed", {"next_action": next_action}))

    note = _optional_text(payload, "note")
    if note is not None and note != current["note"]:
        changes["note"] = note
        events.append(("note_changed", {"note": note}))

    outcome = payload.get("outcome", current["outcome"])
    outcome = _validate_outcome(outcome)
    if current["stage"] == "finished" and outcome != current["outcome"]:
        raise FollowupConflictError("finished outcome cannot be overwritten")
    if outcome != current["outcome"]:
        changes["outcome"] = outcome
        events.append(("outcome_changed", {"outcome": outcome}))
    if stage == "finished" and outcome is None:
        raise FollowupValidationError("finished stage requires outcome")

    # Real submission confirmation is a separate, explicit update.  A task
    # completion, material check, or AI suggestion never writes these fields.
    submitted_at_present = "submitted_at" in payload
    submission_channel_present = "submission_channel" in payload or "channel" in payload
    submitted_at = current["submitted_at"]
    if submitted_at_present:
        submitted_at = _iso_date(payload.get("submitted_at"), field="submitted_at")
    submission_channel = current["submission_channel"]
    if submission_channel_present:
        submission_channel = _required_text(payload.get("submission_channel", payload.get("channel")), "submission_channel")
    if stage == "submitted":
        if not submitted_at or not submission_channel:
            raise FollowupValidationError("submitted stage requires submitted_at and submission_channel confirmation")
    elif submitted_at_present or submission_channel_present:
        raise FollowupValidationError("submission confirmation is only valid for submitted stage")
    if stage == "awaiting_result" and (not submitted_at or not submission_channel):
        raise FollowupValidationError("awaiting_result requires a previously confirmed submission")
    if current["submitted_at"] and submitted_at != current["submitted_at"]:
        raise FollowupConflictError("submitted_at is part of immutable submission history")
    if current["submission_channel"] and submission_channel != current["submission_channel"]:
        raise FollowupConflictError("submission_channel is part of immutable submission history")
    if submitted_at != current["submitted_at"]:
        changes["submitted_at"] = submitted_at
        events.append(("submitted", {"submitted_at": submitted_at, "submission_channel": submission_channel}))
    if submission_channel != current["submission_channel"]:
        changes["submission_channel"] = submission_channel
    if "receipt" in payload:
        receipt = payload.get("receipt")
        if stage != "submitted" and current["stage"] != "submitted":
            raise FollowupValidationError("a receipt requires submitted stage confirmation")
        if receipt is not None and isinstance(receipt, str) and not receipt.strip():
            receipt = None
        if current["receipt_json"] and json_value(current["receipt_json"], None) != receipt:
            raise FollowupConflictError("submission receipt cannot be overwritten")
        if not current["receipt_json"] and receipt is not None:
            changes["receipt_json"] = json_text(receipt)
    elif current["receipt_json"]:
        receipt = json_value(current["receipt_json"], None)

    # Source changes are recorded independently from the task status.  They
    # set a review marker; no task is rolled back.
    source_fingerprint = _source_fingerprint(payload.get("source_fingerprint"), source_document)
    source_version = _optional_text(payload, "source_version", "procurement_version")
    if source_version is None and isinstance(source_document, Mapping):
        source_version = _reading_version(source_document)
    if source_fingerprint and current["source_fingerprint"] and source_fingerprint != current["source_fingerprint"]:
        changes["needs_review"] = 1
        changes["source_fingerprint"] = source_fingerprint
        events.append(("source_changed", {"from": current["source_fingerprint"], "to": source_fingerprint}))
    elif source_fingerprint and not current["source_fingerprint"]:
        changes["source_fingerprint"] = source_fingerprint
    if source_version is not None and source_version != current["source_version"]:
        if current["source_version"]:
            changes["needs_review"] = 1
            events.append(("source_version_changed", {"from": current["source_version"], "to": source_version}))
        changes["source_version"] = source_version

    changed_tasks = json_value(current["changed_tasks_json"], []) or []
    if incoming_plan is not None:
        plan_id = _plan_id(incoming_plan)
        fingerprint = plan_fingerprint(incoming_plan)
        if current["plan_fingerprint"] != fingerprint:
            changes["plan_json"] = json_text(dict(incoming_plan))
            changes["plan_id"] = plan_id
            changes["plan_fingerprint"] = fingerprint
            if current["plan_fingerprint"]:
                changes["needs_review"] = 1
                changed_tasks = _changed_task_ids(current, incoming_plan, changed_tasks)
            events.append(("plan_generated", {"plan_id": plan_id, "adopted": False}))
    if changed_tasks != (json_value(current["changed_tasks_json"], []) or []):
        changes["changed_tasks_json"] = json_text(changed_tasks)
    # Once set, needs_review remains set until a future explicit review action.
    if current["needs_review"]:
        changes["needs_review"] = 1

    if changes:
        repo.update_followup(project_id, changes)
    for event_type, event_payload in events:
        repo.add_event(project_id, event_type, event_payload)
    conn.commit()
    return repo.state(project_id)


def note_material_change(
    conn: sqlite3.Connection,
    project_id: Any,
    reading: Mapping[str, Any],
) -> dict[str, Any]:
    """Mark a changed procurement reading without rewinding user progress.

    The function intentionally does not regenerate or adopt a plan.  It only
    compares the reading fingerprint/version, sets ``needs_review`` and
    records the existing task ids as affected.  A later explicit plan review
    can refine that list while preserving every task completion and submission
    event already stored.
    """

    if not isinstance(reading, Mapping):
        raise FollowupValidationError("reading must be an object")
    project_id = _project_id(project_id)
    repo = FollowupRepository(conn)
    if repo.followup_row(project_id) is None:
        repo.ensure(project_id)
    row = repo.followup_row(project_id, required=True)
    assert row is not None
    fingerprint = _source_fingerprint(None, reading)
    version = _reading_version(reading)
    changed = bool(
        (row["source_fingerprint"] and row["source_fingerprint"] != fingerprint)
        or (version and row["source_version"] and row["source_version"] != version)
    )
    if not changed and not row["source_fingerprint"] and not row["source_version"]:
        repo.update_followup(
            project_id,
            {"source_fingerprint": fingerprint, "source_version": version} if version else {"source_fingerprint": fingerprint},
        )
        conn.commit()
        return repo.state(project_id)
    if changed:
        task_cursor = conn.execute("SELECT id FROM followup_tasks WHERE project_id=? ORDER BY created_at,id", (project_id,))
        task_ids = [str(row_value[0]) for row_value in task_cursor.fetchall()]
        old_ids = json_value(row["changed_tasks_json"], []) or []
        changed_ids = sorted({str(value) for value in old_ids} | set(task_ids))
        values: dict[str, Any] = {"needs_review": 1, "changed_tasks_json": json_text(changed_ids), "source_fingerprint": fingerprint}
        if version:
            values["source_version"] = version
        repo.update_followup(project_id, values)
        repo.add_event(
            project_id,
            "source_changed",
            {"source_version": version, "changed_tasks": changed_ids},
        )
        conn.commit()
    return repo.state(project_id)


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
    if "reason" in values and "skipped_reason" not in values:
        values["skipped_reason"] = values.pop("reason")
    allowed = {"title", "basis", "owner", "due_at", "prerequisites", "materials", "status", "completion_note", "skipped_reason"}
    unknown = set(values) - allowed
    if unknown:
        raise FollowupValidationError(f"unknown task fields: {', '.join(sorted(unknown))}")
    if row["status"] in {"done", "skipped"}:
        for key in ("status", "completion_note", "skipped_reason"):
            if key in values and str(values[key] or "").strip() != str(row[key] or "").strip():
                raise FollowupConflictError("completed or skipped task records cannot be overwritten")
        # Preserve every immutable task record; a repeated identical patch is
        # harmless and returns the current state.
        values = {key: value for key, value in values.items() if key not in {"status", "completion_note", "skipped_reason"}}
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
            normalized = _id(material_id, "task.materials[]")
            if repo.material_row(project_id, normalized) is None:
                raise FollowupValidationError(f"unknown material id: {normalized}")
            normalized_material_ids.append(normalized)
        values["materials"] = normalized_material_ids
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
        repo.add_event(project_id, "task_updated", {"task_id": str(task_id), "changes": _json_safe_patch(values)})
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


__all__ = [
    "AI_CHECK_STATUSES", "BASES", "DISPOSITIONS", "MATERIAL_STATUSES", "OUTCOMES", "STAGES", "TASK_STATUSES",
    "adopt_plan", "get_followup", "note_material_change", "save_followup", "update_material", "update_task",
]
