from __future__ import annotations

import sqlite3

import pytest

from backend.followup import (
    FollowupConflictError,
    FollowupNotFoundError,
    FollowupValidationError,
    adopt_plan,
    ensure_schema,
    generate_plan,
    get_followup,
    note_material_change,
    save_followup,
    update_material,
    update_task,
)
from backend.followup.planning import plan_fingerprint


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    return connection


@pytest.fixture
def explanation() -> dict:
    return {
        "kind": "tender",
        "confirmed": True,
        "procedure": "public tender",
        "material_version": "v1",
        "sections": {
            "requirements": [
                {
                    "id": "qualification",
                    "title": "核对资格条件",
                    "basis": "required",
                    "materials": [{"id": "license", "name": "营业执照"}],
                },
                {"id": "optional", "title": "准备样本", "basis": "conditional", "conditional": True},
            ],
            "milestones": [
                {
                    "id": "submit",
                    "title": "提交投标文件",
                    "basis": "required",
                    "due_at": "2026-11-01T10:00:00+08:00",
                }
            ],
        },
    }


def test_plan_uses_reading_requirements_and_keeps_news_as_lead() -> None:
    plan = generate_plan(
        {
            "kind": "news_lead",
            "is_potential_lead": True,
            "sections": {"requirements": [{"title": "不要生成投标流程"}]},
        }
    )
    assert plan["eligible"] is False
    assert plan["tasks"] == []
    assert plan["materials"] == []


def test_canonical_reading_sections_group_requirements_into_materials() -> None:
    plan = generate_plan(
        {
            "demand_type": "confirmed",
            "material_version": "m1-a",
            "sections": [
                {
                    "id": "qualification",
                    "title": "资格要求",
                    "requirements": [
                        {"id": "r-license", "text": "营业执照扫描件", "sources": [{"record_id": 1, "quote": "营业执照"}]},
                        {"id": "r-condition", "text": "如适用时提供授权书", "sources": [{"record_id": 1, "quote": "授权书"}]},
                    ],
                }
            ],
            "milestones": [{"id": "m-submit", "kind": "submission", "label": "提交报价", "at": "2026-11-02"}],
        }
    )
    assert plan["eligible"] is True
    assert [task["title"] for task in plan["tasks"]] == ["核对/准备资格要求", "提交报价"]
    assert len(plan["materials"]) == 2
    assert {material["basis"] for material in plan["materials"]} == {"required", "conditional"}
    assert all(material["task_id"] == plan["tasks"][0]["id"] for material in plan["materials"])


def test_adoption_is_explicit_idempotent_and_preserves_progress(explanation: dict) -> None:
    conn = _db()
    first = get_followup(conn, 10, explanation)
    assert first["tasks"] == []
    plan = first["plan"]
    assert plan is not None
    adopted = adopt_plan(conn, 10, plan, owner="Alice")
    assert len(adopted["tasks"]) == 2
    assert adopted["owner"] == "Alice"
    adoption_events = [event for event in adopted["events"] if event["type"] == "plan_adopted"]
    assert len(adoption_events) == 1

    task_id = adopted["tasks"][0]["id"]
    done = update_task(conn, 10, task_id, {"status": "done", "completion_note": "已核对"})
    assert done["tasks"][0]["status"] == "done"
    repeated = adopt_plan(conn, 10, plan, owner="Bob")
    assert repeated["owner"] == "Alice"
    assert repeated["tasks"][0]["status"] == "done"
    assert len([event for event in repeated["events"] if event["type"] == "plan_adopted"]) == 1


def test_submitted_is_explicit_and_ai_check_does_not_submit(explanation: dict) -> None:
    conn = _db()
    plan = get_followup(conn, 11, explanation)["plan"]
    state = adopt_plan(conn, 11, plan)
    material = state["materials"][0]
    checked = update_material(conn, 11, material["id"], {"ai_check_status": "passed"})
    assert checked["materials"][0]["ai_check_status"] == "passed"
    assert checked["materials"][0]["status"] == "not_prepared"
    assert checked["stage"] == "evaluating"
    with pytest.raises(FollowupValidationError):
        save_followup(conn, 11, {"stage": "submitted"})
    submitted = save_followup(
        conn,
        11,
        {"stage": "submitted", "submitted_at": "2026-10-20T10:00:00+08:00", "submission_channel": "平台"},
    )
    assert submitted["stage"] == "submitted"
    assert submitted["submitted_at"].startswith("2026-10-20T10:00:00")
    with pytest.raises(FollowupConflictError):
        save_followup(
            conn,
            11,
            {"stage": "submitted", "submitted_at": "2026-10-21T10:00:00+08:00", "submission_channel": "平台"},
        )


def test_strict_ids_dates_and_skip_reason(explanation: dict) -> None:
    conn = _db()
    plan = get_followup(conn, 12, explanation)["plan"]
    state = adopt_plan(conn, 12, plan)
    task_id = state["tasks"][0]["id"]
    with pytest.raises(FollowupNotFoundError):
        update_task(conn, 12, "unknown", {"status": "doing"})
    with pytest.raises(FollowupValidationError):
        update_task(conn, 12, task_id, {"due_at": "tomorrow"})
    with pytest.raises(FollowupValidationError):
        update_task(conn, 12, task_id, {"status": "skipped", "skipped_reason": "  "})
    skipped = update_task(conn, 12, task_id, {"status": "skipped", "skipped_reason": "本次不参与"})
    assert skipped["tasks"][0]["skipped_reason"] == "本次不参与"
    with pytest.raises(FollowupNotFoundError):
        update_material(conn, 12, "unknown", {"status": "provided"})
    with pytest.raises(FollowupValidationError):
        save_followup(conn, 12, {"stage": "not-a-stage"})


def test_source_change_marks_review_without_rolling_back_task(explanation: dict) -> None:
    conn = _db()
    plan = get_followup(conn, 13, explanation)["plan"]
    state = adopt_plan(conn, 13, plan)
    task_id = state["tasks"][0]["id"]
    state = update_task(conn, 13, task_id, {"status": "done", "completion_note": "完成"})
    changed_reading = {**explanation, "material_version": "v2"}
    changed = note_material_change(conn, 13, changed_reading)
    assert changed["needs_review"] is True
    assert task_id in changed["changed_tasks"]
    assert next(task["status"] for task in changed["tasks"] if task["id"] == task_id) == "done"
    assert any(event["type"] == "source_changed" for event in changed["events"])


def test_foreign_key_prevents_orphan_children() -> None:
    conn = _db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO followup_tasks(project_id,id,title,basis,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (999, "t", "孤儿", "required", "2026-10-14", "2026-10-14"),
        )

