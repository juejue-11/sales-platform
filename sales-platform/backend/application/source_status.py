"""Collector source freshness and operational status."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import re
from datetime import datetime, timedelta
from pathlib import Path
from ..core.utils import SHANGHAI, json_value
from .state import ApplicationState

if TYPE_CHECKING:
    from .settings import ApplicationSettings


class SourceStatus:
    def __init__(self, *, state: ApplicationState, settings: ApplicationSettings) -> None:
        self.state = state
        self.settings = settings

    @staticmethod
    def file_signature(path: Path) -> tuple[str, int | None, int | None]:
        resolved = str(Path(path).resolve())
        try:
            stat = Path(path).stat()
            return resolved, int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return resolved, None, None

    def source_data_updated_at(self) -> str | None:
        """Return the collector's update time, rather than this app's import time.

        The export file is the source of truth for freshness.  Keeping this
        separate from ``last_import_at`` avoids presenting a recent import as
        if the collector had run recently.
        """
        try:
            stat = self.state.source_path.stat()
        except OSError:
            return self.settings.get_meta("data_updated_at")
        return datetime.fromtimestamp(stat.st_mtime, SHANGHAI).isoformat(timespec="seconds")

    def source_age_stale(self) -> bool:
        try:
            mtime = self.state.source_path.stat().st_mtime
        except OSError:
            return True
        return datetime.now(SHANGHAI).timestamp() - float(mtime) > timedelta(hours=48).total_seconds()

    def import_pending(self) -> bool:
        return self.file_signature(self.state.source_path) != self.state.last_import_signature

    def collector_data_root(self) -> Path:
        """Resolve the read-only collector data root for trial-status.md.

        The default export lives in ``<collector>/data/export``.  A configured
        export follows the same shape; a bare test/export file is paired with
        its containing directory.  No path derived from record data is used.
        """
        source = self.state.source_path.resolve()
        parent = source.parent
        if parent.name.lower() == "export":
            return parent.parent
        if parent.name.lower() == "data":
            return parent
        return parent

    def source_notes(self, *, include_path: bool = False) -> str:
        rows = self.state.db.execute("SELECT source_id,source_name,equipment_json,deadline_at,buyer,title,body_text FROM records ORDER BY id").fetchall()
        source_names: dict[str, str] = {}
        missing = {"equipment": 0, "deadline_at": 0, "buyer": 0, "body_text": 0}
        for row in rows:
            source_id = str(row["source_id"] or "unknown")
            source_name = str(row["source_name"] or "").strip()
            source_names[source_id] = source_name or source_id
            equipment = json_value(row["equipment_json"], [])
            if not isinstance(equipment, list) or not any(str(value).strip() for value in equipment):
                missing["equipment"] += 1
            if not str(row["deadline_at"] or "").strip():
                missing["deadline_at"] += 1
            if not str(row["buyer"] or "").strip():
                missing["buyer"] += 1
            if not str(row["body_text"] or "").strip():
                missing["body_text"] += 1

        notes: list[str] = []
        if include_path:
            notes.append(f"源文件：{self.state.source_path}")
        if rows:
            names = "、".join(sorted(source_names.values()))
            notes.append(f"当前导出已导入 {len(rows)} 条记录，覆盖 {len(source_names)} 个来源：{names}。")
            labels = {"equipment": "设备信息", "deadline_at": "截止时间", "buyer": "采购方", "body_text": "正文"}
            missing_parts = [f"{labels[key]}缺失{count}条" for key, count in missing.items() if count]
            notes.append("字段缺失提示：" + ("、".join(missing_parts) if missing_parts else "未发现主要字段缺失") + "。")
        else:
            notes.append("当前暂无已导入记录，来源与字段覆盖未知。")
        notes.append("覆盖范围：仅当前导出文件，不能据此推断已覆盖全网公告或全部来源。")

        trial_status = self.collector_data_root() / "trial-status.md"
        try:
            content = trial_status.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            content = ""
        if content:
            # Keep only the operator-facing coverage facts.  The dashboard
            # should not reproduce Markdown or the collector's local path.
            key_tokens = ("首次启动", "当前连续周期", "历史缺失", "连续运行验收")
            trial_lines: list[str] = []
            for raw_line in content.splitlines():
                line = re.sub(r"^[\s#>*`-]+", "", raw_line).strip()
                line = re.sub(r"[`*_]", "", line)
                if line and any(token in line for token in key_tokens):
                    trial_lines.append(line[:300])
            if trial_lines:
                notes.append("试运行状态：\n" + "\n".join(dict.fromkeys(trial_lines)))
            else:
                notes.append("试运行状态：未知（未找到关键覆盖摘要；仅当前导出文件）。")
        else:
            notes.append("试运行状态：未知（未找到关键覆盖摘要；仅当前导出文件）。")
        return "\n".join(notes)

    def status(self) -> dict[str, Any]:
        imports = [dict(row) for row in self.state.db.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()]
        jobs = []
        for row in self.state.db.execute("SELECT id,project_id,status,attempts,error,created_at,updated_at FROM jobs ORDER BY id DESC LIMIT 100").fetchall():
            jobs.append(dict(row))
        # The operation cards describe current project state.  Keep the full
        # attempt ledger above, but count only the newest task for each project
        # so an old failed attempt does not remain a current failure forever.
        counts = {str(row["status"]): int(row["count"]) for row in self.state.db.execute(
            """SELECT status,count(*) AS count
                 FROM jobs
                WHERE id IN (SELECT MAX(id) FROM jobs GROUP BY project_id)
                GROUP BY status"""
        ).fetchall()}
        reading_jobs = [dict(row) for row in self.state.db.execute(
            "SELECT id,project_id,status,attempts,error,created_at,updated_at "
            "FROM procurement_reading_jobs ORDER BY id DESC LIMIT 100"
        ).fetchall()]
        # Reading jobs are also attempts, while the latest persisted reading
        # is the source of truth for what the project currently exposes.  A
        # fresh partial/complete result can therefore settle an older failed
        # queue row; a fresh failed reading must continue to report failure.
        latest_reading_jobs = self.state.db.execute(
            """SELECT job.status,job.started_at,job.updated_at,job.created_at,
                      reading.status AS reading_status,reading.stale AS reading_stale,
                      reading.updated_at AS reading_updated_at
                 FROM procurement_reading_jobs AS job
                 LEFT JOIN procurement_readings AS reading
                   ON reading.id=(SELECT MAX(latest_reading.id)
                                    FROM procurement_readings AS latest_reading
                                   WHERE latest_reading.project_id=job.project_id)
                WHERE job.id=(SELECT MAX(latest_job.id)
                                FROM procurement_reading_jobs AS latest_job
                               WHERE latest_job.project_id=job.project_id)"""
        ).fetchall()
        reading_counts: dict[str, int] = {}
        for row in latest_reading_jobs:
            status = str(row["status"])
            reading_status = str(row["reading_status"] or "")
            job_started = row["started_at"] or row["updated_at"]
            reading_updated = row["reading_updated_at"]
            reading_is_current = bool(reading_status) and not bool(row["reading_stale"]) and str(reading_updated) >= str(job_started)
            if reading_is_current and status not in {"queued", "running"}:
                if reading_status == "failed":
                    status = "failed"
                elif reading_status in {"complete", "partial", "not_configured"}:
                    status = "succeeded"
                elif status == "succeeded" and reading_status == "pending":
                    status = "queued"
            reading_counts[status] = reading_counts.get(status, 0) + 1
        reading_job_counts = reading_counts
        return {"data_updated_at": self.source_data_updated_at(), "last_import_at": self.settings.get_meta("last_import_at"),
                "import_error": self.settings.get_meta("import_error"), "file_stale": self.source_age_stale(),
                "import_pending": self.import_pending(), "source_notes": self.source_notes(include_path=True),
                "imports": imports, "jobs": jobs, "job_counts": counts,
                "reading_jobs": reading_jobs, "reading_job_counts": reading_job_counts,
                "ai_configured": self.settings.ai_config()["configured"]}
