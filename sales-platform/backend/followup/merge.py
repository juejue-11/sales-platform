"""Project-merge preservation for follow-up history."""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ..core.utils import json_text, json_value, now_iso
from .repository import FollowupRepository, _project_id


def merge_followup(
    conn: sqlite3.Connection,
    source_project_id: Any,
    target_project_id: Any,
) -> dict[str, Any]:
    """Move or merge follow-up history before a project is deleted.

    The screening project merge owns the surrounding transaction and calls
    this function before deleting ``source_project_id``.  This function never
    commits.  When the target has no follow-up row, the source header and all
    children are moved intact and the target is marked for review.  When both
    projects have rows, target header fields remain authoritative while every
    source task, material, and event is retained; colliding child ids receive
    a stable source prefix and all task/material references are remapped.  A
    target row that is only an unadopted reading preview has no human progress
    to protect and inherits the source header instead.
    """

    source_project_id = _project_id(source_project_id)
    target_project_id = _project_id(target_project_id)
    repo = FollowupRepository(conn)
    if source_project_id == target_project_id:
        return repo.state(target_project_id)

    source_row = repo.followup_row(source_project_id)
    if source_row is None:
        # Most projects have no follow-up row.  The ingest merge hook must be
        # safe to call for those projects and must not create a new row.
        return repo.state(target_project_id)
    target_row = repo.followup_row(target_project_id)
    source_tasks = _merge_rows(
        conn,
        "SELECT * FROM followup_tasks WHERE project_id=? ORDER BY created_at,id",
        (source_project_id,),
    )
    source_materials = _merge_rows(
        conn,
        "SELECT * FROM followup_materials WHERE project_id=? ORDER BY created_at,id",
        (source_project_id,),
    )
    target_tasks = _merge_rows(
        conn,
        "SELECT * FROM followup_tasks WHERE project_id=? ORDER BY created_at,id",
        (target_project_id,),
    )
    target_materials = _merge_rows(
        conn,
        "SELECT * FROM followup_materials WHERE project_id=? ORDER BY created_at,id",
        (target_project_id,),
    )
    target_preview = target_row is not None and not _merge_has_human_progress(
        target_row,
        target_tasks,
        target_materials,
    )
    target_task_ids = {
        str(row["id"])
        for row in target_tasks
    } if not target_preview else set()
    target_material_ids = {
        str(row["id"])
        for row in target_materials
    } if not target_preview else set()
    task_id_map = _merge_id_map(
        source_tasks,
        occupied=target_task_ids,
        source_project_id=source_project_id,
    )
    material_id_map = _merge_id_map(
        source_materials,
        occupied=target_material_ids,
        source_project_id=source_project_id,
    )

    source_changed_tasks = _merge_task_ids(
        source_row.get("changed_tasks_json"),
        task_id_map,
    )
    source_task_ids = [task_id_map[str(row["id"])] for row in source_tasks]
    review_task_ids = sorted(set(source_changed_tasks) | set(source_task_ids))

    if target_row is None:
        _insert_merged_header(
            conn,
            target_project_id,
            source_row,
            changed_tasks=review_task_ids,
        )
    elif target_preview:
        _replace_preview_header(
            conn,
            target_project_id,
            source_row,
            changed_tasks=review_task_ids,
        )
    else:
        target_changed_tasks = json_value(target_row.get("changed_tasks_json"), []) or []
        merged_changed_tasks = sorted(
            {str(value) for value in target_changed_tasks} | set(review_task_ids)
        )
        conn.execute(
            "UPDATE followups SET needs_review=1, changed_tasks_json=?, updated_at=? WHERE project_id=?",
            (json_text(merged_changed_tasks), now_iso(), target_project_id),
        )

    for row in source_tasks:
        old_task_id = str(row["id"])
        new_task_id = task_id_map[old_task_id]
        prerequisites = _merge_task_references(row.get("prerequisites_json"), task_id_map)
        material_ids = _merge_material_references(row.get("materials_json"), material_id_map)
        conn.execute(
            """INSERT INTO followup_tasks
               (project_id,id,title,basis,owner,due_at,prerequisites_json,materials_json,
                status,completion_note,skipped_reason,plan_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                target_project_id,
                new_task_id,
                row["title"],
                row["basis"],
                row["owner"],
                row["due_at"],
                json_text(prerequisites),
                json_text(material_ids),
                row["status"],
                row["completion_note"],
                row["skipped_reason"],
                row["plan_id"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    for row in source_materials:
        old_material_id = str(row["id"])
        new_material_id = material_id_map[old_material_id]
        old_task_id = row.get("task_id")
        new_task_id = task_id_map.get(str(old_task_id), old_task_id) if old_task_id is not None else None
        conn.execute(
            """INSERT INTO followup_materials
               (project_id,id,task_id,name,basis,status,ai_check_status,ai_check_note,provided_at,note,plan_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                target_project_id,
                new_material_id,
                new_task_id,
                row["name"],
                row["basis"],
                row["status"],
                row["ai_check_status"],
                row["ai_check_note"],
                row["provided_at"],
                row["note"],
                row["plan_id"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    # Event ids are globally unique, so moving the source event rows keeps
    # their original order and payload while attaching them to the target.
    conn.execute(
        "UPDATE followup_events SET project_id=? WHERE project_id=?",
        (target_project_id, source_project_id),
    )
    merge_payload = {
        "source_project_id": source_project_id,
        "target_project_id": target_project_id,
        "source_header": _merge_header_public(source_row),
        "source_task_ids": [str(row["id"]) for row in source_tasks],
        "source_material_ids": [str(row["id"]) for row in source_materials],
        "task_id_map": task_id_map,
        "material_id_map": material_id_map,
    }
    repo.add_event(target_project_id, "followup_merged", merge_payload)

    # The copies and event move are complete before deleting source children;
    # this order satisfies the composite foreign key on material.task_id.
    conn.execute("DELETE FROM followup_materials WHERE project_id=?", (source_project_id,))
    conn.execute("DELETE FROM followup_tasks WHERE project_id=?", (source_project_id,))
    conn.execute("DELETE FROM followups WHERE project_id=?", (source_project_id,))
    return repo.state(target_project_id)


def _merge_rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cursor = conn.execute(query, params)
    names = [column[0] for column in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _merge_id_map(
    rows: Sequence[Mapping[str, Any]],
    *,
    occupied: set[str],
    source_project_id: int,
) -> dict[str, str]:
    used = set(occupied)
    mapping: dict[str, str] = {}
    for row in rows:
        old_id = str(row["id"])
        candidate = old_id
        if candidate in used:
            candidate = _merge_child_id(source_project_id, old_id, used)
        mapping[old_id] = candidate
        used.add(candidate)
    return mapping


def _merge_child_id(source_project_id: int, old_id: str, occupied: set[str]) -> str:
    base = f"source-{source_project_id}-{old_id}"
    if len(base) > 256:
        digest = hashlib.sha256(old_id.encode("utf-8")).hexdigest()[:32]
        base = f"source-{source_project_id}-{digest}"
    candidate = base
    suffix = 2
    while candidate in occupied:
        suffix_text = f"-{suffix}"
        candidate = f"{base[:256 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _merge_task_references(value: Any, mapping: Mapping[str, str]) -> list[Any]:
    values = json_value(value, []) or []
    if not isinstance(values, list):
        return []
    result: list[Any] = []
    for item in values:
        key = str(item.get("id")) if isinstance(item, Mapping) and item.get("id") is not None else str(item)
        result.append(mapping.get(key, item))
    return result


def _merge_material_references(value: Any, mapping: Mapping[str, str]) -> list[Any]:
    values = json_value(value, []) or []
    if not isinstance(values, list):
        return []
    result: list[Any] = []
    for item in values:
        key = str(item.get("id")) if isinstance(item, Mapping) and item.get("id") is not None else str(item)
        result.append(mapping.get(key, item))
    return result


def _merge_task_ids(value: Any, mapping: Mapping[str, str]) -> list[str]:
    values = json_value(value, []) or []
    if not isinstance(values, list):
        return []
    return sorted({mapping.get(str(item), str(item)) for item in values})


def _merge_has_human_progress(
    row: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    materials: Sequence[Mapping[str, Any]],
) -> bool:
    """Distinguish a generated preview row from an actual follow-up."""

    if tasks or materials or bool(row.get("is_following")):
        return True
    if str(row.get("stage") or "") != "evaluating":
        return True
    if str(row.get("disposition") or "") != "following":
        return True
    for key in ("owner", "next_action", "note", "outcome", "submitted_at", "submission_channel", "receipt_json"):
        if row.get(key) not in (None, "", "{}", "[]"):
            return True
    return False


def _replace_preview_header(
    conn: sqlite3.Connection,
    target_project_id: int,
    source_row: Mapping[str, Any],
    *,
    changed_tasks: list[str],
) -> None:
    """Replace an unadopted target proposal with the source's live header."""

    conn.execute(
        """UPDATE followups SET
           is_following=?, stage=?, disposition=?, owner=?, next_action=?, note=?, outcome=?,
           submitted_at=?, submission_channel=?, receipt_json=?, plan_json=?, plan_id=?, plan_fingerprint=?,
           source_fingerprint=?, source_version=?, needs_review=1, changed_tasks_json=?, updated_at=?
           WHERE project_id=?""",
        (
            source_row["is_following"],
            source_row["stage"],
            source_row["disposition"],
            source_row["owner"],
            source_row["next_action"],
            source_row["note"],
            source_row["outcome"],
            source_row["submitted_at"],
            source_row["submission_channel"],
            source_row["receipt_json"],
            source_row["plan_json"],
            source_row["plan_id"],
            source_row["plan_fingerprint"],
            source_row["source_fingerprint"],
            source_row["source_version"],
            json_text(changed_tasks),
            now_iso(),
            target_project_id,
        ),
    )


def _insert_merged_header(
    conn: sqlite3.Connection,
    target_project_id: int,
    source_row: Mapping[str, Any],
    *,
    changed_tasks: list[str],
) -> None:
    conn.execute(
        """INSERT INTO followups
           (project_id,is_following,stage,disposition,owner,next_action,note,outcome,
            submitted_at,submission_channel,receipt_json,plan_json,plan_id,plan_fingerprint,
            source_fingerprint,source_version,needs_review,changed_tasks_json,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            target_project_id,
            source_row["is_following"],
            source_row["stage"],
            source_row["disposition"],
            source_row["owner"],
            source_row["next_action"],
            source_row["note"],
            source_row["outcome"],
            source_row["submitted_at"],
            source_row["submission_channel"],
            source_row["receipt_json"],
            source_row["plan_json"],
            source_row["plan_id"],
            source_row["plan_fingerprint"],
            source_row["source_fingerprint"],
            source_row["source_version"],
            1,
            json_text(changed_tasks),
            source_row["created_at"],
            now_iso(),
        ),
    )


def _merge_header_public(row: Mapping[str, Any]) -> dict[str, Any]:
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
        "receipt": json_value(row.get("receipt_json"), None),
        "plan": json_value(row.get("plan_json"), None),
        "plan_id": row["plan_id"],
        "source_fingerprint": row["source_fingerprint"],
        "source_version": row["source_version"],
        "needs_review": bool(row["needs_review"]),
        "changed_tasks": json_value(row.get("changed_tasks_json"), []) or [],
    }


__all__ = ["merge_followup"]
