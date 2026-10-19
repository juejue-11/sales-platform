"""Durable reading jobs and procurement model execution."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

import asyncio
from ..analysis.providers import OpenAICompatibleProvider
from ..analysis.runtime_bridge import PiSDKProvider
from ..analysis.errors import sanitize_ai_error
from ..core.utils import now_iso
from .reading import ReadingProtocolMismatch, invoke_reading
from ..procurement.reading import empty_reading
from .state import ApplicationState

if TYPE_CHECKING:
    from .documents import ProjectDocuments
    from .reading_store import ProcurementReadings
    from .records import ProjectRecords
    from .settings import ApplicationSettings


class ReadingJobs:
    def __init__(
        self, *,
        state: ApplicationState,
        documents: ProjectDocuments,
        reading_store: ProcurementReadings,
        records: ProjectRecords,
        settings: ApplicationSettings,
        queue_project: Callable[..., Any],
        wake_worker: Callable[..., Any],
    ) -> None:
        self.state = state
        self.documents = documents
        self.reading_store = reading_store
        self.records = records
        self.settings = settings
        self.queue_project = queue_project
        self.wake_worker = wake_worker

    def queue_reading(self, project_id: int, *, force: bool = False, schedule: bool = False) -> dict[str, Any]:
        """Create a durable pending reading and optionally run it now."""

        project_id = int(self.records.project_row(project_id)["id"])
        material = self.reading_store.reading_material(project_id)
        version = str(material["material_version"])
        config = self.settings.ai_config()
        if not config["configured"]:
            return {"status": "not_configured", "reading": self.reading_store.reading_public(
                self.reading_store.reading_row(project_id, version), version, configured=False)}
        current = self.reading_store.reading_row(project_id, version)
        current_fingerprint = self.reading_store.reading_request_fingerprint(version, config)
        if (current and not force and str(current["input_fingerprint"] or "") == current_fingerprint
                and str(current["status"] or "") == "pending"):
            if schedule:
                self.schedule_reading(project_id, force=force)
            return {"status": "pending", "reading": self.reading_store.reading_public(current, version)}
        if (current and not force and str(current["status"] or "") in {"complete", "partial"}
                and str(current["input_fingerprint"] or "") == current_fingerprint):
            return {"status": str(current["status"]), "reading": self.reading_store.reading_public(current, version)}
        pending = empty_reading(version, status="pending")
        self.reading_store.save_procurement_reading(project_id, pending, material_version=version, config=config)
        if schedule:
            self.schedule_reading(project_id, force=force)
        return {"status": "queued", "reading": self.reading_store.procurement_reading_for_project(project_id)}

    def schedule_reading(self, project_id: int, *, force: bool = False) -> None:
        project_id = int(self.records.project_row(project_id)["id"])
        with self.state.lock, self.state.db:
            queued = self.state.db.execute(
                "SELECT id,force FROM procurement_reading_jobs WHERE project_id=? AND status='queued' "
                "ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if queued is None:
                self.state.db.execute(
                    "INSERT INTO procurement_reading_jobs(project_id,force,status,created_at,updated_at) VALUES (?,?,'queued',?,?)",
                    (project_id, int(force), now_iso(), now_iso()),
                )
            elif force and not bool(queued["force"]):
                # Multiple material changes while an older reading is
                # running share one durable follow-up.  Keep the strongest
                # rerun request without starting a parallel worker.
                self.state.db.execute(
                    "UPDATE procurement_reading_jobs SET force=1,updated_at=? WHERE id=?",
                    (now_iso(), int(queued["id"])),
                )
        self.wake_worker()

    async def run_reading_job(self, reading_job_id: int) -> None:
        with self.state.lock, self.state.db:
            row = self.state.db.execute("SELECT * FROM procurement_reading_jobs WHERE id=?", (reading_job_id,)).fetchone()
            if row is None or row["status"] not in {"queued", "running"}:
                return
            project_id = int(row["project_id"])
            force = bool(row["force"])
            self.state.db.execute(
                "UPDATE procurement_reading_jobs SET status='running',attempts=attempts+1,started_at=?,updated_at=?,error=NULL WHERE id=?",
                (now_iso(), now_iso(), reading_job_id),
            )
        try:
            result = await self.run_reading(project_id, force=force)
            status = "succeeded" if result.get("status") in {"complete", "partial", "not_configured"} else "failed"
            error = result.get("error") or ("reading status: " + str(result.get("status"))) if status == "failed" else None
            with self.state.lock, self.state.db:
                self.state.db.execute(
                    "UPDATE procurement_reading_jobs SET status=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                    (status, str(error or "")[:500] or None, now_iso(), now_iso(), reading_job_id),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            safe_error = sanitize_ai_error(exc, secrets=[str(self.settings.ai_config().get("api_key") or "")])
            with self.state.lock, self.state.db:
                self.state.db.execute(
                    "UPDATE procurement_reading_jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                    (safe_error[:500], now_iso(), now_iso(), reading_job_id),
                )

    async def run_reading(self, project_id: int, *, force: bool = False, bypass_gate: bool = True) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        await self.documents.ensure_reading_documents(project_id)
        material = self.reading_store.reading_material(project_id)
        version = str(material["material_version"])
        config = self.settings.ai_config()
        if not config["configured"]:
            return self.reading_store.save_procurement_reading(
                project_id, empty_reading(version, status="not_configured"), material_version=version, config=config,
            ) if self.reading_store.reading_row(project_id, version) else empty_reading(version, status="not_configured")
        current = self.reading_store.reading_row(project_id, version)
        current_fingerprint = self.reading_store.reading_request_fingerprint(version, config)
        if (current and not force and str(current["status"] or "") in {"complete", "partial"}
                and str(current["input_fingerprint"] or "") == current_fingerprint):
            return self.reading_store.reading_public(current, version)
        # Legacy injected providers implement the judgment protocol only. Do
        # not add a second call to them; explicit read/read_procurement methods
        # opt into the new module while preserving all existing test doubles.
        explicit = callable(getattr(self.state.provider, "read_procurement", None)) or callable(getattr(self.state.provider, "read", None))
        if self.state.injected_provider and not explicit and not bool(getattr(self.state.provider, "supports_procurement_reading", False)):
            return self.reading_store.reading_public(self.reading_store.reading_row(project_id, version), version)
        pending = empty_reading(version, status="pending")
        self.reading_store.save_procurement_reading(project_id, pending, material_version=version, config=config)
        provider: Any = self.state.provider
        if config.get("engine") == "single_call" and isinstance(provider, PiSDKProvider) and not self.state.injected_provider:
            provider = OpenAICompatibleProvider()
        try:
            result = await invoke_reading(provider, material=material, config=config)
            saved = self.reading_store.save_procurement_reading(project_id, result, material_version=version, config=config)
        except ReadingProtocolMismatch:
            # The compatibility caller consumes the returned content as the
            # judgment response in the same job. Keep the reading stage
            # explicitly failed for operators, without storing model output.
            message = "reading runtime task protocol is unavailable"
            failed = empty_reading(version, status="failed", error=message)
            self.reading_store.save_procurement_reading(project_id, failed, material_version=version, config=config, error=message)
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = sanitize_ai_error(exc, secrets=[str(config.get("api_key") or "")])
            failed = empty_reading(version, status="failed", error=message)
            saved = self.reading_store.save_procurement_reading(project_id, failed, material_version=version, config=config, error=message)
        # A newly read material package invalidates the recommendation that
        # depended on the previous package. Preserve the old judgment and
        # queue a fresh one only when this was an explicit reading request;
        # run_job itself is already the active queue item.
        with self.state.lock, self.state.db:
            judgment = self.state.db.execute(
                "SELECT id,stale FROM judgments WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)
            ).fetchone()
            running = self.state.db.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND status='running' LIMIT 1", (project_id,)
            ).fetchone()
            if judgment is not None and (force or saved.get("status") in {"complete", "partial", "failed"}):
                self.state.db.execute("UPDATE judgments SET stale=1 WHERE project_id=? AND stale=0", (project_id,))
                self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
        if judgment is not None and not running and (force or saved.get("status") in {"complete", "partial"}):
            self.queue_project(project_id, force=True, schedule=True)
        return saved
