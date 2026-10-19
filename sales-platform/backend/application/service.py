"""Application composition, process lifecycle and durable queue coordination."""
from __future__ import annotations

from typing import Any

import asyncio
import json
import os
import sqlite3
from pathlib import Path
import httpx
from ..analysis.ai import AIProvider, OpenAICompatibleProvider, PiSDKProvider
from ..analysis.analysis_policy import current_analysis_meta
from ..core.config import env_archive_root, env_source_path
from ..storage.db import connect
from .ingest import ImportErrorDetail, import_records
from ..research.project_research import RESEARCH_POLICY_VERSION
from ..core.utils import json_text, json_value, now_iso
from .errors import ServiceError
from .state import ApplicationState
from .settings import ApplicationSettings
from .records import ProjectRecords
from .reading_store import ProcurementReadings
from .reading_jobs import ReadingJobs
from .documents import ProjectDocuments
from .analysis_context import AnalysisContext
from .agent_runs import AgentRuns
from .source_status import SourceStatus
from .projects import ProjectQueries
from .analysis_jobs import AnalysisJobs
from .decisions import ProjectDecisions


class ScreeningService:
    def __init__(self, *, db_path: Path | None = None, source_path: Path | None = None,
                 archive_root: Path | None = None, provider: AIProvider | None = None,
                 research_sources_path: Path | None = None, research_client: httpx.AsyncClient | None = None) -> None:
        from ..core.config import database_path

        db_path = Path(db_path or database_path())
        source = Path(source_path or env_source_path())
        selected_provider = provider
        if selected_provider is None:
            selected_provider = (OpenAICompatibleProvider() if os.getenv("SCREENING_AI_ENGINE", "").strip().lower() == "single_call"
                                 else PiSDKProvider())
        self.state = ApplicationState(
            db=connect(db_path), db_path=db_path, source_path=source,
            archive_root=Path(archive_root or env_archive_root(source)), provider=selected_provider,
            injected_provider=provider is not None, research_sources_path=research_sources_path,
            research_client=research_client,
        )
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._worker_task: asyncio.Task[Any] | None = None
        self._import_task: asyncio.Task[Any] | None = None
        self.settings = ApplicationSettings(state=self.state, queue_pending=self.queue_pending)
        self.records = ProjectRecords(state=self.state)
        self.reading_store = ProcurementReadings(state=self.state, records=self.records, settings=self.settings)
        self.source_status = SourceStatus(state=self.state, settings=self.settings)
        self.analysis_context = AnalysisContext(state=self.state, reading_store=self.reading_store, records=self.records)
        self.agent_runs = AgentRuns(state=self.state, records=self.records)
        self.projects = ProjectQueries(
            state=self.state,
            agent_runs=self.agent_runs,
            reading_store=self.reading_store,
            records=self.records,
            settings=self.settings,
            source_status=self.source_status,
        )
        self.documents = ProjectDocuments(
            state=self.state,
            reading_store=self.reading_store,
            records=self.records,
            settings=self.settings,
            queue_reading=self.queue_reading,
        )
        self.reading_jobs = ReadingJobs(
            state=self.state,
            documents=self.documents,
            reading_store=self.reading_store,
            records=self.records,
            settings=self.settings,
            queue_project=self.queue_project,
            wake_worker=self._wake_worker,
        )
        self.analysis_jobs = AnalysisJobs(
            state=self.state,
            agent_runs=self.agent_runs,
            analysis_context=self.analysis_context,
            documents=self.documents,
            projects=self.projects,
            reading_jobs=self.reading_jobs,
            reading_store=self.reading_store,
            records=self.records,
            settings=self.settings,
            queue_project=self.queue_project,
        )
        self.decisions = ProjectDecisions(
            state=self.state,
            analysis_context=self.analysis_context,
            projects=self.projects,
            reading_store=self.reading_store,
            records=self.records,
            settings=self.settings,
            queue_project=self.queue_project,
        )
        self.settings.initialize_defaults()
        if source_path is None and not os.getenv("SCREENING_IMPORT_PATH"):
            saved_path = self.settings.section("import").get("path")
            if isinstance(saved_path, str) and saved_path.strip():
                self.state.source_path = Path(saved_path)
                if archive_root is None:
                    self.state.archive_root = env_archive_root(self.state.source_path)
        stored_signature = self.settings.get_meta("source_signature")
        if stored_signature:
            try:
                value = json.loads(stored_signature)
                if isinstance(value, list) and len(value) == 3:
                    self.state.last_import_signature = (str(value[0]), value[1], value[2])
            except (TypeError, json.JSONDecodeError):
                self.state.last_import_signature = None

    def _wake_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = self._spawn(self._worker_loop())

    @property
    def db(self) -> sqlite3.Connection:
        return self.state.db

    @property
    def db_path(self) -> Path:
        return self.state.db_path

    @property
    def source_path(self) -> Path:
        return self.state.source_path

    @source_path.setter
    def source_path(self, value: Path) -> None:
        self.state.source_path = value

    @property
    def archive_root(self) -> Path:
        return self.state.archive_root

    @archive_root.setter
    def archive_root(self, value: Path) -> None:
        self.state.archive_root = value

    @property
    def provider(self) -> AIProvider:
        return self.state.provider

    @provider.setter
    def provider(self, value: AIProvider) -> None:
        self.state.provider = value

    def close(self) -> None:
        if self.state.closed:
            return
        self.state.closed = True
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        tasks = (set(self._background_tasks) | set(self.state.run_tasks)) - ({current_task} if current_task else set())
        for task in tasks:
            task.cancel()
        # FastAPI calls close from an active event loop.  Defer the actual DB
        # close until cancelled workers have unwound their child subprocesses;
        # this prevents a late callback from touching a closed connection and
        # gives PiSDKProvider a chance to kill Node on service shutdown.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._close_db()
            return
        if tasks:
            loop.create_task(self._close_after_tasks(tasks))
        else:
            self._close_db()

    async def aclose(self) -> None:
        """Awaitable shutdown used by FastAPI so child processes are reaped."""
        if not self.state.closed:
            self.state.closed = True
        current_task = asyncio.current_task()
        tasks = (set(self._background_tasks) | set(self.state.run_tasks)) - ({current_task} if current_task else set())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._close_db()

    def _close_db(self) -> None:
        with self.state.lock:
            try:
                self.state.db.close()
            except sqlite3.ProgrammingError:
                pass

    async def _close_after_tasks(self, tasks: set[asyncio.Task[Any]]) -> None:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._close_db()

    def start_background(self) -> None:
        if self.state.closed:
            return
        with self.state.lock, self.state.db:
            # A process crash can leave a task marked running. It is safe to
            # return it to the durable queue on the next startup.
            restart_time = now_iso()
            self.state.db.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (restart_time,))
            stale_agent_runs = self.state.db.execute(
                "SELECT id,investigation_json FROM agent_runs WHERE status='running'"
            ).fetchall()
            # The durable job is resumable, but an agent run is an immutable
            # attempt.  Leaving an old run as ``running`` makes the history
            # claim that work is still in progress after the process that
            # owned it has gone away.  Preserve its record and close it with
            # an explicit restart reason; a resumed job creates a new run.
            interrupted_error = "agent run interrupted by service restart"
            for agent_run in stale_agent_runs:
                investigation = json_value(agent_run["investigation_json"], {})
                if not isinstance(investigation, dict):
                    investigation = {}
                investigation["status"] = "failed"
                investigation["stop_reason"] = interrupted_error
                self.state.db.execute(
                    "UPDATE agent_runs SET status='failed',finished_at=?,error=?,investigation_json=? "
                    "WHERE id=? AND status='running'",
                    (restart_time, interrupted_error, json_text(investigation), int(agent_run["id"])),
                )
            # If a follow-up is already durable, the old in-flight attempt
            # must not be resurrected beside it after restart. Keep its
            # failure row for operators and let the queued job read the
            # latest material. A lone running job remains resumable.
            self.state.db.execute(
                """UPDATE procurement_reading_jobs
                      SET status='failed',error=?,finished_at=?,updated_at=?
                    WHERE status='running'
                      AND EXISTS (
                          SELECT 1 FROM procurement_reading_jobs AS followup
                           WHERE followup.project_id=procurement_reading_jobs.project_id
                             AND followup.status='queued'
                      )""",
                ("reading superseded by queued follow-up after restart", restart_time, restart_time),
            )
            self.state.db.execute("UPDATE procurement_reading_jobs SET status='queued',updated_at=? WHERE status='running'",
                            (restart_time,))
            # Readings are append-only: an old pending revision must not be
            # resurrected after a later complete/partial/failed revision was
            # written.  Only the newest row for each project can represent
            # the durable work that needs recovery after a restart.
            pending_projects = self.state.db.execute(
                """SELECT reading.project_id
                   FROM procurement_readings AS reading
                  WHERE reading.status='pending'
                    AND reading.id=(SELECT MAX(latest.id)
                                      FROM procurement_readings AS latest
                                     WHERE latest.project_id=reading.project_id)
                    AND NOT EXISTS (
                        SELECT 1 FROM procurement_reading_jobs AS active
                         WHERE active.project_id=reading.project_id
                           AND active.status IN ('queued','running')
                    )"""
            ).fetchall()
            for row in pending_projects:
                self.state.db.execute(
                    "INSERT INTO procurement_reading_jobs(project_id,force,status,created_at,updated_at) VALUES (?,0,'queued',?,?)",
                    (int(row["project_id"]), now_iso(), now_iso()),
                )
        self._wake_worker()
        if self._import_task is None or self._import_task.done():
            self._import_task = self._spawn(self._import_loop())

    def _spawn(self, coroutine: Any) -> asyncio.Task[Any] | None:
        try:
            task = asyncio.create_task(coroutine)
        except RuntimeError:
            coroutine.close()
            return None
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _worker_loop(self) -> None:
        while not self.state.closed:
            try:
                reading_job = self.state.db.execute(
                    "SELECT id FROM procurement_reading_jobs WHERE status='queued' ORDER BY id LIMIT 1"
                ).fetchone()
                if reading_job is not None:
                    await self.reading_jobs.run_reading_job(int(reading_job["id"]))
                    continue
                job = self.state.db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if job is not None and int(job["id"]) not in self.state.active_jobs:
                    await self.analysis_jobs.run_job(int(job["id"]))
                else:
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.5)

    async def _import_loop(self) -> None:
        while not self.state.closed:
            interval = max(1, int(self.settings.get_settings()["import"].get("poll_seconds", 300) or 300))
            try:
                settings = self.settings.get_settings()["import"]
                if settings.get("auto_import", True):
                    signature = self.source_status.file_signature(self.state.source_path)
                    if signature != self.state.last_import_signature:
                        # Startup import is intentionally asynchronous so the API
                        # can answer health requests while a large export is validated.
                        await self.import_file()
            except FileNotFoundError:
                self.settings.set_meta("import_error", f"source file not found: {self.state.source_path}")
            except ServiceError:
                # import_file has already stored the precise validation/path
                # failure and deliberately left the last successful signature
                # unchanged so this attempt is retried later.
                pass
            except Exception as exc:
                self.settings.set_meta("import_error", f"automatic import failed: {type(exc).__name__}")
            await asyncio.sleep(interval)

    def _new_import_row(self) -> int:
        with self.state.lock, self.state.db:
            row = self.state.db.execute("INSERT INTO imports(path,status,created_at) VALUES (?,'running',?) RETURNING id",
                                  (str(self.state.source_path), now_iso())).fetchone()
            return int(row[0])

    async def import_file(self) -> dict[str, Any]:
        import_id = self._new_import_row()
        import_signature = self.source_status.file_signature(self.state.source_path)
        # Capture bounded related snapshots before the atomic import.  The
        # post-import comparison below catches changes to a related project's
        # body, relation references, or project membership.
        related_context_before = self.analysis_context.related_context_fingerprints()
        try:
            with self.state.lock:
                result = import_records(self.state.db, self.state.source_path)
            with self.state.lock, self.state.db:
                related_changes = self.analysis_context.mark_related_context_changes(related_context_before)
                self.state.db.execute("UPDATE imports SET status='success',inserted=?,updated=?,unchanged=?,total=?,finished_at=? WHERE id=?",
                                (result.inserted, result.updated, result.unchanged, result.total, now_iso(), import_id))
                # This timestamp describes the collector's source update.  It
                # must not be replaced with this application's import time.
                self.state.db.execute("INSERT INTO meta(key,value) VALUES ('data_updated_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (self.source_status.source_data_updated_at() or now_iso(),))
                self.state.db.execute("INSERT INTO meta(key,value) VALUES ('last_import_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (now_iso(),))
                self.state.db.execute("DELETE FROM meta WHERE key='import_error'")
                # Record the signature observed before reading. If the source
                # changes while a read is in progress, the monitor will notice
                # the mismatch and retry instead of treating a partial view as
                # current.
                self.state.last_import_signature = import_signature
                self.state.db.execute("INSERT INTO meta(key,value) VALUES ('source_signature',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (json_text(list(self.state.last_import_signature)),))
            if self.settings.ai_config()["configured"]:
                # A daily incremental import must not rerun an unrelated old
                # backlog merely because its policy/model result is stale.
                for project_id in sorted(set(result.affected_project_ids) | set(related_changes)):
                    if self.reading_store.reading_gate_enabled():
                        # Import changes are a material event.  Persist a
                        # reading job independently of the judgment queue so
                        # a running read/judge cannot lose the new version.
                        self.reading_jobs.queue_reading(project_id, schedule=True)
                    self.queue_project(project_id, schedule=False)
            return {"inserted": result.inserted, "updated": result.updated, "unchanged": result.unchanged,
                    "total": result.total, "message": "导入完成"}
        except (ImportErrorDetail, OSError) as exc:
            error = str(exc)
            with self.state.lock, self.state.db:
                self.state.db.execute("UPDATE imports SET status='failed',error=?,finished_at=? WHERE id=?",
                                (error[:500], now_iso(), import_id))
                self.state.db.execute("INSERT INTO meta(key,value) VALUES ('import_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (error[:500],))
            raise ServiceError(error)
        except Exception as exc:
            with self.state.lock, self.state.db:
                self.state.db.execute("UPDATE imports SET status='failed',error=?,finished_at=? WHERE id=?",
                                (f"{type(exc).__name__}: import rolled back"[:500], now_iso(), import_id))
                self.state.db.execute("INSERT INTO meta(key,value) VALUES ('import_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (f"{type(exc).__name__}: import rolled back",))
            raise

    def queue_project(self, project_id: int, *, force: bool = False, schedule: bool = False) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        config = self.settings.ai_config()
        if not config["configured"]:
            if (config.get("credentials_configured") and config.get("engine") == "pi_sdk"
                    and not config.get("sdk_ready") and isinstance(self.state.provider, PiSDKProvider)):
                return {"job_id": None, "status": "not_configured",
                        "message": "Pi SDK运行时未就绪，请安装Node.js 22.19+并构建agent-runtime/dist/runner.js"}
            return {"job_id": None, "status": "not_configured", "message": "AI 未配置，已保留项目"}
        # Keep a durable material-version marker even when an injected legacy
        # provider is used; that provider will continue to execute the old
        # judgment protocol without an unexpected extra model call.
        # ``force`` belongs to the judgment queue.  A completed reading must
        # remain the current evidence while its dependent judgment is rerun;
        # passing force through here would append a fresh pending reading and
        # hide the result we just produced.
        if self.reading_store.reading_gate_enabled():
            self.reading_jobs.queue_reading(project_id, force=False, schedule=False)
        records = self.records.records_for_project(project_id)
        gate, _ = self.reading_store.gate(records, project_id)
        if gate in {"excluded", "expired", "duplicate", "closed"}:
            return {"job_id": None, "status": "skipped", "message": "该项目当前门槛状态不需要 AI 分析"}
        current = self.records.latest_judgment(project_id)
        if (current and not self.projects.judgment_dict(current)["stale"]
                and current_analysis_meta(json_value(current["analysis_meta_json"], None))
                and current["research_policy_version"] == RESEARCH_POLICY_VERSION and not force):
            return {"job_id": None, "status": "succeeded", "message": "已有最新判断"}
        queued = self.state.db.execute("SELECT id,status FROM jobs WHERE project_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                                 (project_id,)).fetchone()
        if queued:
            return {"job_id": int(queued["id"]), "status": queued["status"], "message": "任务已在队列中"}
        with self.state.lock, self.state.db:
            row = self.state.db.execute("INSERT INTO jobs(project_id,status,created_at,updated_at) VALUES (?,'queued',?,?) RETURNING id",
                                  (project_id, now_iso(), now_iso())).fetchone()
            job_id = int(row[0])
        if schedule:
            self.schedule_job(job_id)
        return {"job_id": job_id, "status": "queued", "message": "已排队"}

    def schedule_job(self, job_id: int) -> None:
        # Scheduling only wakes the single durable worker. It never starts one
        # task per project, which keeps provider calls bounded at concurrency 1.
        self._wake_worker()

    def queue_pending(self, *, schedule: bool = False) -> int:
        count = 0
        for row in self.state.db.execute("SELECT id FROM projects ORDER BY id").fetchall():
            result = self.queue_project(int(row["id"]), schedule=schedule)
            if result.get("job_id") is not None and result.get("status") == "queued":
                count += 1
        return count

    def retry_failed(self) -> int:
        if not self.settings.ai_config()["configured"]:
            return 0
        with self.state.lock, self.state.db:
            # A project can have many immutable attempts.  Retrying every
            # historical failure would replay paid model calls after a later
            # successful attempt already made that failure irrelevant.
            rows = self.state.db.execute(
                """SELECT job.id
                     FROM jobs AS job
                    WHERE job.status='failed'
                      AND NOT EXISTS (
                          SELECT 1 FROM jobs AS newer
                           WHERE newer.project_id=job.project_id
                             AND newer.id>job.id
                      )
                    ORDER BY job.id"""
            ).fetchall()
            for row in rows:
                self.state.db.execute("UPDATE jobs SET status='queued',error=NULL,started_at=NULL,finished_at=NULL,updated_at=? WHERE id=?", (now_iso(), int(row["id"])))
        for row in rows:
            self.schedule_job(int(row["id"]))
        return len(rows)

    def get_settings(self) -> dict[str, Any]:
        return self.settings.get_settings()

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.settings.update_settings(payload)

    def archive_path(self, record_id: int) -> Path:
        return self.records.archive_path(record_id)

    def queue_reading(self, project_id: int, *, force: bool = False, schedule: bool = False) -> dict[str, Any]:
        return self.reading_jobs.queue_reading(project_id, force=force, schedule=schedule)

    def schedule_reading(self, project_id: int, *, force: bool = False) -> None:
        return self.reading_jobs.schedule_reading(project_id, force=force)

    async def run_reading(self, project_id: int, *, force: bool = False, bypass_gate: bool = True) -> dict[str, Any]:
        return await self.reading_jobs.run_reading(project_id, force=force, bypass_gate=bypass_gate)

    async def refresh_project_documents(self, project_id: int) -> dict[str, Any]:
        return await self.documents.refresh_project_documents(project_id)

    def document_snapshot(self, project_id: int, document_id: str, *, sha256: str = "") -> dict[str, Any]:
        return self.documents.document_snapshot(project_id, document_id, sha256=sha256)

    def research_runs(self, project_id: int) -> dict[str, Any]:
        return self.agent_runs.research_runs(project_id)

    def research_run(self, project_id: int, run_id: int) -> dict[str, Any]:
        return self.agent_runs.research_run(project_id, run_id)

    def research_source_snapshot(self, project_id: int, source_ref: str, version: str, *,
                                 page: int | None = None, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        return self.agent_runs.research_source_snapshot(project_id, source_ref, version, page=page, offset=offset, limit=limit)

    def status(self) -> dict[str, Any]:
        return self.source_status.status()

    def summary(self, project_id: int) -> dict[str, Any]:
        return self.projects.summary(project_id)

    def project_detail(self, project_id: int) -> dict[str, Any]:
        return self.projects.project_detail(project_id)

    def list_projects(self, *, q: str = "", gate: str = "", ai_status: str = "", decision: str = "",
                      ai_decision: str = "", deadline_soon: bool = False, kind: str = "", source_id: str = "",
                      category: str = "", region: str = "", buyer: str = "", procurement_stage: str = "",
                      demand_type: str = "", followup_stage: str = "", is_following: bool = False,
                      key_time: str = "", published_days: int = 0,
                      page: int = 1, page_size: int = 30, sort: str = "updated",
                      active_only: bool = False) -> dict[str, Any]:
        return self.projects.list_projects(q=q, gate=gate, ai_status=ai_status, decision=decision, ai_decision=ai_decision, deadline_soon=deadline_soon, kind=kind, source_id=source_id, category=category, region=region, buyer=buyer, procurement_stage=procurement_stage, demand_type=demand_type, followup_stage=followup_stage, is_following=is_following, key_time=key_time, published_days=published_days, page=page, page_size=page_size, sort=sort, active_only=active_only)

    def dashboard(self) -> dict[str, Any]:
        return self.projects.dashboard()

    async def run_job(self, job_id: int) -> None:
        return await self.analysis_jobs.run_job(job_id)

    def save_decision(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.decisions.save_decision(project_id, payload)

    def gate_action(self, project_id: int, action: str, reason: str) -> dict[str, Any]:
        return self.decisions.gate_action(project_id, action, reason)

    def link_projects(self, project_id: int, target_project_id: int, relation: str, note: str) -> dict[str, Any]:
        return self.decisions.link_projects(project_id, target_project_id, relation, note)
