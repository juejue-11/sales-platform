"""Public follow-up lifecycle state implementation.

This module owns the follow-up header, monotonic stage, disposition, and
submission-history invariants. Plan adoption, progress updates, and project
merges live in their own modules and are exported directly by the package.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from ..core.utils import json_text, json_value
from .errors import FollowupConflictError, FollowupValidationError
from .planning import _iso_date, generate_plan, plan_fingerprint
from .repository import FollowupRepository, _project_id
from .validation import (
    _changed_task_ids,
    _optional_text,
    _plan_id,
    _reading_version,
    _reason,
    _required_text,
    _source_fingerprint,
    _validate_disposition,
    _validate_outcome,
    _validate_plan,
    _validate_stage,
    _validate_stage_transition,
)


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



__all__ = ["get_followup", "note_material_change", "save_followup"]
