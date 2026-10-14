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
    merge_followup,
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


def test_fact_sections_are_suggested_checks_without_fake_materials() -> None:
    plan = generate_plan(
        {
            "demand_type": "confirmed",
            "sections": [
                {
                    "id": "buyer",
                    "title": "谁在采购",
                    "requirements": [{"id": "buyer-name", "text": "采购人为某企业", "sources": [{"record_id": 1, "quote": "某企业"}]}],
                },
                {
                    "id": "qualification",
                    "title": "资格与材料",
                    "requirements": [{"id": "license", "text": "提供营业执照", "sources": [{"record_id": 1, "quote": "营业执照"}]}],
                },
            ],
        }
    )
    buyer_task = next(task for task in plan["tasks"] if task["id"].startswith("task-"))
    assert buyer_task["basis"] == "suggested"
    assert len(plan["materials"]) == 1
    assert plan["materials"][0]["name"] == "提供营业执照"
    assert plan["materials"][0]["basis"] == "required"


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


def test_re_adoption_preserves_completed_task_definition(explanation: dict) -> None:
    conn = _db()
    first = adopt_plan(conn, 14, generate_plan(explanation))
    completed = first["tasks"][0]
    update_task(conn, 14, completed["id"], {"status": "done", "completion_note": "已完成"})
    changed_explanation = {
        **explanation,
        "sections": {
            **explanation["sections"],
            "requirements": [{"id": "qualification", "title": "变化后的资格核对", "basis": "required"}],
        },
    }
    second_plan = generate_plan(changed_explanation)
    state = adopt_plan(conn, 14, second_plan)
    preserved = next(task for task in state["tasks"] if task["id"] == completed["id"])
    assert preserved["status"] == "done"
    assert preserved["title"] == completed["title"]
    assert preserved["completion_note"] == "已完成"
    assert completed["id"] in state["changed_tasks"]


def test_foreign_key_prevents_orphan_children() -> None:
    conn = _db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO followup_tasks(project_id,id,title,basis,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (999, "t", "孤儿", "required", "2026-10-14", "2026-10-14"),
        )


def test_canonical_reading_activation_and_task_definition_audit() -> None:
    conn = _db()
    reading = {
        "demand_type": "confirmed",
        "material_version": "canonical-v1",
        "sections": [
            {
                "id": "buyer",
                "title": "Buyer facts",
                "requirements": [
                    {
                        "id": "buyer-name",
                        "text": "The buyer is Acme Procurement",
                        "sources": [{"record_id": 1, "quote": "Acme Procurement"}],
                    },
                    {
                        "id": "scope",
                        "text": "Purchase ten pumps",
                        "sources": [{"record_id": 1, "quote": "ten pumps"}],
                    },
                ],
            },
            {
                "id": "qualification",
                "title": "Qualification materials",
                "requirements": [
                    {
                        "id": "license",
                        "text": "Provide a business license certificate",
                        "sources": [{"record_id": 2, "quote": "business license"}],
                    },
                    {
                        "id": "registration",
                        "text": "Register in the portal before the deadline",
                        "sources": [{"record_id": 2, "quote": "register in the portal"}],
                    },
                ],
            },
        ],
        "milestones": [
            {
                "id": "submit-quote",
                "kind": "submission",
                "title": "Submit quotation",
                "at": "2026-11-02",
                "sources": [{"record_id": 3, "quote": "submit quotation"}],
            }
        ],
    }

    # Reading a project only stores a proposal.  It does not join the
    # follow-up list or create executable tasks/materials.
    preview = get_followup(conn, 20, reading)
    assert preview["is_following"] is False
    assert preview["tasks"] == []
    assert preview["plan"]["eligible"] is True
    assert sum(task["basis"] == "suggested" for task in preview["plan"]["tasks"]) == 1
    assert len(preview["plan"]["materials"]) == 1
    assert preview["plan"]["materials"][0]["name"] == "Provide a business license certificate"
    assert any(task["title"] == "Submit quotation" for task in preview["plan"]["tasks"])
    assert get_followup(conn, 20)["is_following"] is False

    # A user can manually follow a project even when there is no AI plan.
    manual = save_followup(conn, 21, {"is_following": True})
    assert manual["is_following"] is True
    assert manual["plan"] is None
    assert manual["tasks"] == []

    adopted = adopt_plan(conn, 20, preview["plan"])
    task = next(task for task in adopted["tasks"] if task["title"] == "核对/准备Qualification materials")

    with pytest.raises(FollowupValidationError):
        update_task(conn, 20, task["id"], {"title": "Renamed without audit reason"})
    updated = update_task(
        conn,
        20,
        task["id"],
        {"title": "核对新的Qualification materials", "reason": "采购方补充了资格材料说明"},
    )
    assert next(item for item in updated["tasks"] if item["id"] == task["id"])["title"] == "核对新的Qualification materials"
    audit = [event for event in updated["events"] if event["type"] == "task_updated"][-1]
    assert audit["payload"]["definition_changed"] is True
    assert audit["payload"]["reason"] == "采购方补充了资格材料说明"

    # Owner assignment and an ordinary todo/doing transition have no
    # definition-change requirement.  Completion text is its own field and
    # cannot be used as an adjustment reason.
    doing = update_task(conn, 20, task["id"], {"owner": "Alice", "status": "doing"})
    assert next(item for item in doing["tasks"] if item["id"] == task["id"])["owner"] == "Alice"
    done = update_task(conn, 20, task["id"], {"status": "done", "completion_note": "已完成核对"})
    completed = next(item for item in done["tasks"] if item["id"] == task["id"])
    with pytest.raises(FollowupConflictError):
        update_task(
            conn,
            20,
            task["id"],
            {"title": "不得覆盖已完成任务", "reason": "确需补办"},
        )
    preserved = next(item for item in get_followup(conn, 20)["tasks"] if item["id"] == task["id"])
    assert preserved["status"] == "done"
    assert preserved["title"] == completed["title"]
    assert preserved["completion_note"] == "已完成核对"


def test_merge_followup_moves_source_history_without_committing(explanation: dict) -> None:
    conn = _db()
    source_plan = get_followup(conn, 30, explanation)["plan"]
    source = adopt_plan(conn, 30, source_plan, owner="Alice")
    task = source["tasks"][0]
    material = source["materials"][0]
    update_task(conn, 30, task["id"], {"status": "done", "completion_note": "source completed"})
    update_material(conn, 30, material["id"], {"status": "provided", "provided_at": "2026-10-20T10:00:00+08:00"})
    source = save_followup(
        conn,
        30,
        {
            "stage": "submitted",
            "submitted_at": "2026-10-20T11:00:00+08:00",
            "submission_channel": "portal",
        },
    )
    source_event_types = [event["type"] for event in source["events"]]

    # The merge participates in the caller's transaction and does not commit
    # on its own.  Rollback proves that the source is still available for a
    # caller that aborts the surrounding project merge.
    conn.execute("BEGIN")
    merged_preview = merge_followup(conn, 30, 31)
    assert conn.in_transaction is True
    assert merged_preview["stage"] == "submitted"
    assert merged_preview["submitted_at"].startswith("2026-10-20T11:00:00")
    assert merged_preview["submission_channel"] == "portal"
    assert merged_preview["owner"] == "Alice"
    assert merged_preview["needs_review"] is True
    conn.rollback()
    assert get_followup(conn, 30)["tasks"]
    assert get_followup(conn, 31)["tasks"] == []

    conn.execute("BEGIN")
    merged = merge_followup(conn, 30, 31)
    assert merged["needs_review"] is True
    assert next(item for item in merged["tasks"] if item["id"] == task["id"])["status"] == "done"
    assert next(item for item in merged["materials"] if item["id"] == material["id"])["status"] == "provided"
    assert set(source_event_types).issubset({event["type"] for event in merged["events"]})
    assert any(event["type"] == "followup_merged" for event in merged["events"])
    assert get_followup(conn, 30)["tasks"] == []
    assert conn.execute("SELECT COUNT(*) FROM followups WHERE project_id=30").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM followup_tasks WHERE project_id=30").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM followup_materials WHERE project_id=30").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM followup_events WHERE project_id=30").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.commit()


def test_merge_followup_keeps_target_header_and_source_conflicts(explanation: dict) -> None:
    conn = _db()
    plan = get_followup(conn, 40, explanation)["plan"]
    source = adopt_plan(conn, 40, plan, owner="Source owner")
    target = adopt_plan(conn, 41, plan, owner="Target owner")
    source_task = source["tasks"][0]
    target_task = target["tasks"][0]
    source_material = source["materials"][0]
    update_task(conn, 40, source_task["id"], {"status": "done", "completion_note": "source done"})
    update_material(conn, 40, source_material["id"], {"status": "provided", "provided_at": "2026-10-20"})
    source = save_followup(
        conn,
        40,
        {
            "stage": "submitted",
            "submitted_at": "2026-10-21T09:00:00+08:00",
            "submission_channel": "email",
        },
    )
    target = save_followup(conn, 41, {"stage": "working", "next_action": "target next step"})
    target_event_types = [event["type"] for event in target["events"]]
    source_event_types = [event["type"] for event in source["events"]]

    merged = merge_followup(conn, 40, 41)
    assert merged["owner"] == "Target owner"
    assert merged["stage"] == "working"
    assert merged["next_action"] == "target next step"
    assert merged["needs_review"] is True
    assert len(merged["tasks"]) == len(target["tasks"]) + len(source["tasks"])
    assert len(merged["materials"]) == len(target["materials"]) + len(source["materials"])
    assert next(item for item in merged["tasks"] if item["id"] == target_task["id"])["status"] == "todo"
    source_copy = next(item for item in merged["tasks"] if item["id"].startswith("source-40-"))
    assert source_copy["status"] == "done"
    assert source_copy["completion_note"] == "source done"
    source_material_copy = next(item for item in merged["materials"] if item["id"].startswith("source-40-"))
    assert source_material_copy["status"] == "provided"
    assert source_material_copy["task_id"] == source_copy["id"]
    assert source_material_copy["id"] in source_copy["materials"]

    merge_event = next(event for event in merged["events"] if event["type"] == "followup_merged")
    source_header = merge_event["payload"]["source_header"]
    assert source_header["owner"] == "Source owner"
    assert source_header["submitted_at"].startswith("2026-10-21T09:00:00")
    assert source_header["submission_channel"] == "email"
    event_types = {event["type"] for event in merged["events"]}
    assert set(target_event_types).issubset(event_types)
    assert set(source_event_types).issubset(event_types)
    assert get_followup(conn, 40)["tasks"] == []
    assert conn.execute("SELECT COUNT(*) FROM followup_events WHERE project_id=40").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

