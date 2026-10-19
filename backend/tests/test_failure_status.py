from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.application.service import ScreeningService
from backend.core.utils import json_text


def _record(record_id: int) -> dict[str, object]:
    return {
        "id": record_id,
        "source_id": f"status-{record_id}",
        "kind": "tender",
        "title": f"notice {record_id}",
        "project_name": f"notice {record_id}",
        "buyer": "buyer",
        "body_text": "announcement",
        "attachments": [],
        "current_version": 1,
        "published_at": "2026-10-01",
        "deadline_at": None,
        "project_key": f"status-project-{record_id}",
    }


def _service(tmp_path: Path, project_count: int = 2) -> ScreeningService:
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(json.dumps(_record(index), ensure_ascii=False) for index in range(1, project_count + 1)),
        encoding="utf-8",
    )
    service = ScreeningService(
        db_path=tmp_path / "screening.sqlite3",
        source_path=source,
        archive_root=tmp_path,
        provider=object(),
    )
    asyncio.run(service.import_file())
    with service.db:
        service.db.execute(
            "UPDATE settings SET value=? WHERE key='ai'",
            (json_text({"base_url": "https://mock.local/v1", "model": "mock", "enabled": True, "api_key": "test-key"}),),
        )
    return service


def _insert_job(service: ScreeningService, project_id: int, status: str, timestamp: str) -> int:
    row = service.db.execute(
        "INSERT INTO jobs(project_id,status,created_at,updated_at,started_at,finished_at,error) "
        "VALUES (?,?,?,?,?,?,?) RETURNING id",
        (
            project_id,
            status,
            timestamp,
            timestamp,
            timestamp if status in {"running", "succeeded", "failed"} else None,
            timestamp if status in {"succeeded", "failed"} else None,
            "old failure" if status == "failed" else None,
        ),
    ).fetchone()
    return int(row[0])


def _insert_reading(service: ScreeningService, project_id: int, status: str, timestamp: str) -> None:
    service.db.execute(
        """INSERT INTO procurement_readings
           (project_id,version,revision,material_version,status,summary,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (project_id, 1, 1, f"fixture-{project_id}", status, status, timestamp, timestamp),
    )


def _insert_reading_job(service: ScreeningService, project_id: int, status: str, timestamp: str) -> int:
    row = service.db.execute(
        """INSERT INTO procurement_reading_jobs
           (project_id,force,status,created_at,updated_at,started_at,finished_at,error)
           VALUES (?,?,?,?,?,?,?,?) RETURNING id""",
        (
            project_id,
            0,
            status,
            timestamp,
            timestamp,
            timestamp if status in {"running", "succeeded", "failed"} else None,
            timestamp if status in {"succeeded", "failed"} else None,
            "old reading failure" if status == "failed" else None,
        ),
    ).fetchone()
    return int(row[0])


def test_status_counts_latest_project_attempts_but_keeps_history_and_retry_scope(tmp_path: Path):
    service = _service(tmp_path)
    try:
        first_failed = _insert_job(service, 1, "failed", "2026-10-10T10:00:00+08:00")
        first_succeeded = _insert_job(service, 1, "succeeded", "2026-10-10T11:00:00+08:00")
        second_failed = _insert_job(service, 2, "failed", "2026-10-10T12:00:00+08:00")
        service.db.commit()

        status = service.status()
        assert status["job_counts"] == {"succeeded": 1, "failed": 1}
        assert [item["id"] for item in status["jobs"]] == [second_failed, first_succeeded, first_failed]

        scheduled: list[int] = []
        service.schedule_job = lambda job_id: scheduled.append(job_id)  # type: ignore[method-assign]
        assert service.retry_failed() == 1
        assert scheduled == [second_failed]
        assert service.db.execute("SELECT status FROM jobs WHERE id=?", (first_failed,)).fetchone()[0] == "failed"
        assert service.db.execute("SELECT status FROM jobs WHERE id=?", (first_succeeded,)).fetchone()[0] == "succeeded"
        assert service.db.execute("SELECT status FROM jobs WHERE id=?", (second_failed,)).fetchone()[0] == "queued"
        retried = service.db.execute("SELECT started_at,finished_at FROM jobs WHERE id=?", (second_failed,)).fetchone()
        assert retried["started_at"] is None and retried["finished_at"] is None
    finally:
        service.close()


def test_reading_counts_follow_latest_reading_without_hiding_current_failure(tmp_path: Path):
    service = _service(tmp_path, project_count=4)
    try:
        _insert_reading_job(service, 1, "failed", "2026-10-10T10:00:00+08:00")
        _insert_reading_job(service, 1, "failed", "2026-10-10T11:00:00+08:00")
        _insert_reading(service, 1, "partial", "2026-10-10T12:00:00+08:00")

        _insert_reading_job(service, 2, "failed", "2026-10-10T13:00:00+08:00")
        _insert_reading(service, 2, "failed", "2026-10-10T14:00:00+08:00")

        _insert_reading(service, 3, "failed", "2026-10-10T15:00:00+08:00")
        _insert_reading_job(service, 3, "running", "2026-10-10T16:00:00+08:00")

        _insert_reading(service, 4, "failed", "2026-10-10T17:00:00+08:00")
        _insert_reading_job(service, 4, "queued", "2026-10-10T18:00:00+08:00")
        service.db.commit()

        status = service.status()
        assert status["reading_job_counts"] == {"succeeded": 1, "failed": 1, "running": 1, "queued": 1}
        assert len(status["reading_jobs"]) == 5
    finally:
        service.close()


def test_restart_closes_orphaned_agent_runs_and_requeues_jobs(tmp_path: Path):
    service = _service(tmp_path)
    try:
        succeeded_job = _insert_job(service, 1, "succeeded", "2026-10-10T10:00:00+08:00")
        running_job = _insert_job(service, 1, "running", "2026-10-10T11:00:00+08:00")
        with service.db:
            orphaned = service.db.execute(
                """INSERT INTO agent_runs
                   (job_id,project_id,engine,status,started_at,investigation_json)
                   VALUES (?,?,?,'running',?,?) RETURNING id""",
                (succeeded_job, 1, "pi_sdk", "2026-10-10T10:00:00+08:00", json_text({"status": "completed"})),
            ).fetchone()[0]
            current = service.db.execute(
                """INSERT INTO agent_runs
                   (job_id,project_id,engine,status,started_at,investigation_json)
                   VALUES (?,?,?,'running',?,?) RETURNING id""",
                (running_job, 1, "pi_sdk", "2026-10-10T11:00:00+08:00", json_text({"status": "running"})),
            ).fetchone()[0]

        service.start_background()

        rows = service.db.execute(
            "SELECT id,status,error,finished_at,investigation_json FROM agent_runs ORDER BY id"
        ).fetchall()
        assert [row["status"] for row in rows] == ["failed", "failed"]
        assert all(row["finished_at"] and "service restart" in row["error"] for row in rows)
        assert all(json.loads(row["investigation_json"])["status"] == "failed" for row in rows)
        assert service.db.execute("SELECT status FROM jobs WHERE id=?", (running_job,)).fetchone()[0] == "queued"
        assert service.db.execute("SELECT status FROM jobs WHERE id=?", (succeeded_job,)).fetchone()[0] == "succeeded"
        assert {int(row["id"]) for row in rows} == {int(orphaned), int(current)}
    finally:
        service.close()
