from __future__ import annotations

import asyncio
import copy
import inspect
import json
import os
import re
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..analysis.ai import (AIError, AIProvider, OpenAICompatibleProvider, PiSDKProvider, PiSDKResult,
                           bounded_context_body, build_messages, correction_messages, extract_content, input_fingerprint,
                           sanitize_ai_error, validate_judgment)
from ..analysis.briefing import build_briefing
from ..analysis.analysis_policy import (ANALYSIS_POLICY_VERSION, DEFAULT_MARKET_GOAL,
                                        analysis_context, current_analysis_meta)
from ..core.config import DEFAULT_SETTINGS, env_archive_root, env_source_path
from ..storage.db import connect
from .ingest import ImportErrorDetail, ImportResult, import_records
from ..analysis.rules import business_sort_key, gate_for_project
from ..research.project_research import (RESEARCH_POLICY_VERSION, RESEARCH_TOOLS, ResearchSession,
                                          configuration_fingerprint, current_dependencies, list_sources, read_saved_source)
from ..analysis.agent_report import iter_report_evidence
from ..analysis.provider_finalizer import deepseek_formatter_endpoint, strict_format_report
from ..documents.document_access import (DocumentAccessError, build_document_view, build_procurement_context,
                                          validate_document_access_arguments)
from ..core.utils import SHANGHAI, json_text, json_value, now_iso, parse_timestamp, safe_join, sha256_text

from ..documents.public_documents import fetch_record_documents, read_archived_documents

from ..procurement import (build_procurement_summary, document_from_row, document_public_metadata,
                           normalize_document, procurement_fingerprint)
from .reading import READING_LIMITS, ReadingProtocolMismatch, invoke_reading
from ..procurement.reading import (build_material, empty_reading, material_version as reading_material_version)
# Compatibility imports keep the historical ScreeningService private entry
# points available while their implementations live in the pure model module.
from ..procurement.model import (
    DOCUMENT_STATUSES as _DOCUMENT_STATUSES,
    PROCUREMENT_FIELDS as _PROCUREMENT_FIELDS,
    attachment_access_state as _attachment_access_state_impl,
    compact_attachment_note as _compact_attachment_note_impl,
    document_json as _document_json_impl,
    safe_document_id as _safe_document_id_impl,
)


DEFAULT_AGENT_LIMITS = {
    "max_turns": 6,
    "max_tool_calls": 12,
    "timeout_ms": 300000,
    "max_output_tokens": 16384,
}
TRUSTED_AGENT_TOOLS = {"read_project_records", "read_company_capability", "read_related_records",
                       "fetch_project_documents", *RESEARCH_TOOLS}


class ServiceError(RuntimeError):
    pass


class NotFoundError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass


class ScreeningService:
    _document_json = staticmethod(_document_json_impl)
    _safe_document_id = staticmethod(_safe_document_id_impl)
    _normalize_document = staticmethod(normalize_document)
    _document_row_value = staticmethod(document_from_row)
    _document_public_metadata = staticmethod(document_public_metadata)
    _procurement_fingerprint = staticmethod(procurement_fingerprint)
    _compact_attachment_note = staticmethod(_compact_attachment_note_impl)
    _attachment_access_state = staticmethod(_attachment_access_state_impl)

    def __init__(self, *, db_path: Path | None = None, source_path: Path | None = None,
                 archive_root: Path | None = None, provider: AIProvider | None = None,
                 research_sources_path: Path | None = None, research_client: httpx.AsyncClient | None = None) -> None:
        from ..core.config import database_path

        self.db_path = Path(db_path or database_path())
        self.source_path = Path(source_path or env_source_path())
        self.archive_root = Path(archive_root or env_archive_root(self.source_path))
        self._research_sources_path = research_sources_path
        self._research_client = research_client
        self.db = connect(self.db_path)
        self._injected_provider = provider is not None
        if provider is not None:
            # Existing callers can inject the old complete() test double (or
            # a Pi-compatible analyze() double) without changing its API.
            self.provider = provider
        elif os.getenv("SCREENING_AI_ENGINE", "").strip().lower() == "single_call":
            self.provider = OpenAICompatibleProvider()
        else:
            self.provider = PiSDKProvider()
        self.provider: AIProvider = self.provider  # type: ignore[assignment]
        self._db_lock = threading.RLock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._run_tasks: set[asyncio.Task[Any]] = set()
        self._worker_task: asyncio.Task[Any] | None = None
        self._import_task: asyncio.Task[Any] | None = None
        self._last_import_signature: tuple[str, int | None, int | None] | None = None
        self._active_jobs: set[int] = set()
        self._closed = False
        self._initialize_defaults()
        stored_signature = self._get_meta("source_signature")
        if stored_signature:
            try:
                value = json.loads(stored_signature)
                if isinstance(value, list) and len(value) == 3:
                    self._last_import_signature = (str(value[0]), value[1], value[2])
            except (TypeError, json.JSONDecodeError):
                self._last_import_signature = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        tasks = (set(self._background_tasks) | set(self._run_tasks)) - ({current_task} if current_task else set())
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
        if not self._closed:
            self._closed = True
        current_task = asyncio.current_task()
        tasks = (set(self._background_tasks) | set(self._run_tasks)) - ({current_task} if current_task else set())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._close_db()

    def _close_db(self) -> None:
        with self._db_lock:
            try:
                self.db.close()
            except sqlite3.ProgrammingError:
                pass

    async def _close_after_tasks(self, tasks: set[asyncio.Task[Any]]) -> None:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._close_db()

    def _initialize_defaults(self) -> None:
        with self._db_lock, self.db:
            for key in ("business", "ai", "import"):
                if self.db.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone() is None:
                    value = copy.deepcopy(DEFAULT_SETTINGS[key])
                    if key == "import":
                        value["path"] = str(self.source_path)
                    if key == "ai":
                        # A key from the process environment remains process-owned
                        # and is never copied into SQLite or API output.
                        value.pop("has_api_key", None)
                    self.db.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?)",
                                    (key, json_text(value), now_iso()))
            if self.db.execute("SELECT 1 FROM meta WHERE key='settings_version'").fetchone() is None:
                self.db.execute("INSERT INTO meta(key,value) VALUES ('settings_version','1')")
            # Preserve old enterprise-fit reports and human choices verbatim.
            # Policy upgrades invalidate reuse; they do not rewrite decisions
            # or initiate paid bulk model calls just by opening the database.
            for judgment in self.db.execute(
                    "SELECT id,project_id,analysis_meta_json FROM judgments WHERE stale=0").fetchall():
                if not current_analysis_meta(json_value(judgment["analysis_meta_json"], None)):
                    self.db.execute("UPDATE judgments SET stale=1 WHERE id=?", (judgment["id"],))
                    self.db.execute("UPDATE projects SET updated_at=? WHERE id=?",
                                    (now_iso(), judgment["project_id"]))
            self.db.execute("INSERT INTO meta(key,value) VALUES ('analysis_policy_version',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ANALYSIS_POLICY_VERSION,))

    def start_background(self) -> None:
        if self._closed:
            return
        with self._db_lock, self.db:
            # A process crash can leave a task marked running. It is safe to
            # return it to the durable queue on the next startup.
            self.db.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (now_iso(),))
            self.db.execute("UPDATE procurement_reading_jobs SET status='queued',updated_at=? WHERE status='running'",
                            (now_iso(),))
            # Readings are append-only: an old pending revision must not be
            # resurrected after a later complete/partial/failed revision was
            # written.  Only the newest row for each project can represent
            # the durable work that needs recovery after a restart.
            pending_projects = self.db.execute(
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
                self.db.execute(
                    "INSERT INTO procurement_reading_jobs(project_id,force,status,created_at,updated_at) VALUES (?,0,'queued',?,?)",
                    (int(row["project_id"]), now_iso(), now_iso()),
                )
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = self._spawn(self._worker_loop())
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
        while not self._closed:
            try:
                reading_job = self.db.execute(
                    "SELECT id FROM procurement_reading_jobs WHERE status='queued' ORDER BY id LIMIT 1"
                ).fetchone()
                if reading_job is not None:
                    await self._run_reading_job(int(reading_job["id"]))
                    continue
                job = self.db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if job is not None and int(job["id"]) not in self._active_jobs:
                    await self.run_job(int(job["id"]))
                else:
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.5)

    async def _import_loop(self) -> None:
        while not self._closed:
            interval = max(1, int(self.get_settings()["import"].get("poll_seconds", 300) or 300))
            try:
                settings = self.get_settings()["import"]
                if settings.get("auto_import", True):
                    signature = self._file_signature(self.source_path)
                    if signature != self._last_import_signature:
                        # Startup import is intentionally asynchronous so the API
                        # can answer health requests while a large export is validated.
                        await self.import_file()
            except FileNotFoundError:
                self._set_meta("import_error", f"source file not found: {self.source_path}")
            except ServiceError:
                # import_file has already stored the precise validation/path
                # failure and deliberately left the last successful signature
                # unchanged so this attempt is retried later.
                pass
            except Exception as exc:
                self._set_meta("import_error", f"automatic import failed: {type(exc).__name__}")
            await asyncio.sleep(interval)

    @staticmethod
    def _file_signature(path: Path) -> tuple[str, int | None, int | None]:
        resolved = str(Path(path).resolve())
        try:
            stat = Path(path).stat()
            return resolved, int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return resolved, None, None

    def _set_meta(self, key: str, value: Any) -> None:
        with self._db_lock, self.db:
            self.db.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(value) if value is not None else None))

    def _get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def _section(self, key: str) -> dict[str, Any]:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        value = json_value(row[0], {}) if row else {}
        return value if isinstance(value, dict) else {}

    def _stored_api_key(self) -> str:
        value = self._section("ai").get("api_key")
        if isinstance(value, str) and value:
            return value
        return os.getenv("OPENAI_API_KEY", "")

    def _ai_engine(self, ai: dict[str, Any] | None = None) -> str:
        # The environment switch is intentionally explicit.  It provides a
        # stable escape hatch for the legacy HTTP tests while the default is
        # always the embedded Pi SDK.
        configured = os.getenv("SCREENING_AI_ENGINE", "").strip().lower()
        if configured == "single_call":
            return "single_call"
        # A persisted/client supplied engine is deliberately ignored.  This
        # keeps the legacy path opt-in and makes Pi SDK the stable default even
        # when upgrading a database created by an older build.
        return "pi_sdk"

    def _ai_config(self) -> dict[str, Any]:
        ai = self._section("ai")
        base_url = str(ai.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        model = str(ai.get("model") or os.getenv("OPENAI_MODEL", ""))
        enabled = bool(ai.get("enabled", True))
        api_key = self._stored_api_key()
        engine = self._ai_engine(ai)
        sdk_is_ready = PiSDKProvider.sdk_ready()
        provider_is_pi = isinstance(self.provider, PiSDKProvider) or callable(getattr(self.provider, "analyze", None))
        runtime_ready = (self._injected_provider or engine != "pi_sdk" or not provider_is_pi
                         or not isinstance(self.provider, PiSDKProvider) or sdk_is_ready)
        credentials_configured = enabled and bool(base_url and model and api_key)
        return {"base_url": base_url, "model": model, "enabled": enabled,
                "api_key": api_key, "engine": engine, "sdk_ready": sdk_is_ready,
                "credentials_configured": credentials_configured,
                "configured": credentials_configured and runtime_ready}

    def get_settings(self) -> dict[str, Any]:
        business = copy.deepcopy(self._section("business"))
        ai = copy.deepcopy(self._section("ai"))
        import_settings = copy.deepcopy(self._section("import"))
        for key, default in DEFAULT_SETTINGS["business"].items():
            business.setdefault(key, copy.deepcopy(default))
        for key, default in DEFAULT_SETTINGS["import"].items():
            import_settings.setdefault(key, copy.deepcopy(default))
        for key, default in DEFAULT_SETTINGS["ai"].items():
            ai.setdefault(key, copy.deepcopy(default))
        # Never expose the actual secret, including through arbitrary extra keys.
        ai.pop("api_key", None)
        ai["has_api_key"] = bool(self._stored_api_key())
        ai["enabled"] = bool(ai.get("enabled"))
        ai["engine"] = self._ai_engine(ai)
        ai["sdk_ready"] = PiSDKProvider.sdk_ready()
        version = int(self._get_meta("settings_version") or 1)
        return {"business": business, "ai": ai, "import": import_settings, "version": version,
                "analysis": {"goal": DEFAULT_MARKET_GOAL, "semantic_version": ANALYSIS_POLICY_VERSION,
                             "company_profile_present": analysis_context(business)["company_profile_present"]}}

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ServiceError("settings body must be an object")
        before = self.get_settings()
        before_ai_config = self._ai_config()
        changed_business = False
        changed_import_path: str | None = None
        changed_ai_runtime = False
        with self._db_lock, self.db:
            for section in ("business", "ai", "import"):
                incoming = payload.get(section)
                if not isinstance(incoming, dict):
                    continue
                current = self._section(section)
                previous = copy.deepcopy(current)
                if section == "ai":
                    if "api_key" in incoming:
                        key_value = incoming.get("api_key")
                        if isinstance(key_value, str) and key_value:
                            current["api_key"] = key_value
                    if incoming.get("clear_api_key") is True:
                        current.pop("api_key", None)
                    # Engine and runtime readiness are process-derived
                    # read-only values.  The legacy HTTP path is enabled only
                    # by SCREENING_AI_ENGINE=single_call, never by persisted
                    # client input.
                    incoming = {k: v for k, v in incoming.items()
                                if k not in {"api_key", "clear_api_key", "has_api_key", "engine", "sdk_ready"}}
                current.update(incoming)
                self.db.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                                (section, json_text(current), now_iso()))
                if section == "business" and current != previous:
                    changed_business = True
                if section == "ai" and any(current.get(key) != previous.get(key) for key in ("base_url", "model", "enabled", "engine")):
                    changed_ai_runtime = True
                if section == "import" and isinstance(current.get("path"), str) and current.get("path") != previous.get("path"):
                    changed_import_path = current["path"]
            version = int(self._get_meta("settings_version") or 1) + 1
            self.db.execute("INSERT INTO meta(key,value) VALUES ('settings_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (str(version),))
            if changed_business:
                self.db.execute("UPDATE judgments SET stale=1")
                self.db.execute("UPDATE projects SET updated_at=?", (now_iso(),))
            if changed_ai_runtime:
                # Endpoint/model/enabled changes alter the interpretation of
                # an AI result.  API key rotation alone does not.
                self.db.execute("UPDATE judgments SET stale=1")
                self.db.execute("UPDATE projects SET updated_at=?", (now_iso(),))
        if changed_import_path:
            self.source_path = Path(changed_import_path)
            self.archive_root = env_archive_root(self.source_path)
        after = self.get_settings()
        after_ai_config = self._ai_config()
        became_configured = not before_ai_config["configured"] and after_ai_config["configured"]
        if after_ai_config["configured"] and (changed_business or changed_ai_runtime or became_configured):
            self.queue_pending(schedule=False)
        return after

    def _record_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key, default in (("equipment_json", []), ("facts_json", []), ("inferences_json", [])):
            out_key = key.removesuffix("_json")
            result[out_key] = json_value(result.pop(key), default)
        payload = json_value(result.pop("source_payload_json", "{}"), {})
        attachments = payload.get("attachments", []) if isinstance(payload, dict) else []
        result["attachments"] = attachments if isinstance(attachments, list) else []
        return result

    def _records_for_project(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute("""SELECT r.* FROM records r JOIN project_records pr ON pr.record_id=r.id
                                WHERE pr.project_id=?""", (project_id,)).fetchall()
        records = [self._record_dict(row) for row in rows]
        return sorted(records, key=business_sort_key, reverse=True)

    def _document_sources_for_records(self, record_ids: list[int] | tuple[int, ...] | None = None,
                                      *, current_only: bool = True) -> list[dict[str, Any]]:
        current_clause = " WHERE is_current=1" if current_only else ""
        if record_ids is None:
            rows = self.db.execute(f"SELECT * FROM procurement_documents{current_clause} ORDER BY record_id,id").fetchall()
        elif not record_ids:
            return []
        else:
            placeholders = ",".join("?" for _ in record_ids)
            rows = self.db.execute(
                f"SELECT * FROM procurement_documents WHERE record_id IN ({placeholders})"
                f"{' AND is_current=1' if current_only else ''} ORDER BY record_id,id",
                tuple(int(value) for value in record_ids),
            ).fetchall()
        return [self._document_row_value(row) for row in rows]

    def _procurement_for_project(self, project_id: int) -> dict[str, Any]:
        record_rows = self.db.execute(
            "SELECT r.id,r.kind,r.body_text,r.source_payload_json FROM records r JOIN project_records pr ON pr.record_id=r.id "
            "WHERE pr.project_id=? ORDER BY r.id", (project_id,)
        ).fetchall()
        record_ids = [int(row["id"]) for row in record_rows]
        documents = self._document_sources_for_records(record_ids)
        refresh = self.db.execute("SELECT * FROM procurement_refreshes WHERE project_id=?", (project_id,)).fetchone()
        return build_procurement_summary(record_rows, documents, refresh)

    def _reading_corrections(self, project_id: int) -> list[dict[str, Any]]:
        """Read optional human corrections without coupling to supplements.py.

        The corrections table is created by the supplements workstream.  This
        branch must also run against databases created before that table
        existed, so the lookup is deliberately capability-detected.
        """

        table = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='procurement_corrections'"
        ).fetchone()
        if table is None:
            return []
        rows = self.db.execute(
            "SELECT id,project_id,requirement_id,text,reason,material_version,created_at "
            "FROM procurement_corrections WHERE project_id=? ORDER BY id", (int(project_id),)
        ).fetchall()
        return [dict(row) for row in rows]

    def _reading_material(self, project_id: int) -> dict[str, Any]:
        records = self._records_for_project(int(project_id))
        documents = self._document_sources_for_records([int(record["id"]) for record in records
                                                         if record.get("id") is not None])
        corrections = self._reading_corrections(int(project_id))
        version = reading_material_version(records, documents, corrections)
        return build_material(records, documents, version=version, corrections=corrections)

    def _reading_row(self, project_id: int, material_version: str | None = None) -> sqlite3.Row | None:
        if material_version:
            return self.db.execute(
                "SELECT * FROM procurement_readings WHERE project_id=? AND material_version=? "
                "ORDER BY id DESC LIMIT 1", (int(project_id), material_version)
            ).fetchone()
        return self.db.execute(
            "SELECT * FROM procurement_readings WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (int(project_id),)
        ).fetchone()

    @staticmethod
    def _reading_json(value: Any, default: Any) -> Any:
        parsed = json_value(value, default)
        return parsed if isinstance(parsed, type(default)) else default

    def _reading_public(self, row: sqlite3.Row | None, current_material_version: str,
                        *, configured: bool | None = None) -> dict[str, Any]:
        configured = self._ai_config()["configured"] if configured is None else configured
        if row is None:
            return empty_reading(current_material_version, status="pending" if configured else "not_configured")
        status = str(row["status"] or "partial")
        if status not in {"pending", "complete", "partial", "failed", "not_configured"}:
            status = "failed"
        stale = str(row["material_version"] or "") != current_material_version
        return {
            "version": int(row["version"] or 1), "id": int(row["id"]), "revision": int(row["revision"] or 1),
            "material_version": str(row["material_version"] or ""), "status": status, "stale": stale,
            "summary": str(row["summary"] or ""), "demand_type": str(row["demand_type"] or "unknown"),
            "stage": str(row["stage"] or ""), "buyer": str(row["buyer"] or ""),
            "region": str(row["region"] or ""), "category": self._reading_json(row["category_json"], []),
            "sections": self._reading_json(row["sections_json"], []),
            "milestones": self._reading_json(row["milestones_json"], []),
            "coverage": self._reading_json(row["coverage_json"], []),
            "conflicts": self._reading_json(row["conflicts_json"], []),
            "unresolved": self._reading_json(row["unresolved_json"], []),
            "error": str(row["error"] or "") if row["error"] else None,
            "updated_at": row["updated_at"],
        }

    def _procurement_reading_for_project(self, project_id: int, *, material: dict[str, Any] | None = None) -> dict[str, Any]:
        material = material or self._reading_material(int(project_id))
        current_version = str(material.get("material_version") or "")
        row = self._reading_row(int(project_id), current_version)
        if row is None:
            # Keep a previous explanation visible as stale evidence when the
            # source package changed. The current row (if any) is selected
            # below only when no exact-version row exists.
            row = self._reading_row(int(project_id))
        # Legacy injected judgment providers do not opt into the procurement
        # reading protocol.  Do not manufacture a pending reading in their
        # project snapshot, which would perturb their existing fingerprints.
        if row is None and not self._reading_gate_enabled():
            return empty_reading(current_version, status="not_configured")
        public = self._reading_public(row, current_version)
        # A pending or failed retry must not erase a previously successful
        # explanation. Expose the old facts with the current processing status
        # and an explicit limitation; gate decisions still inspect the latest
        # row directly, so an incomplete retry cannot make an old deadline
        # look verified.
        if row is not None and str(row["status"] or "") in {"pending", "failed"}:
            fallback = self.db.execute(
                "SELECT * FROM procurement_readings WHERE project_id=? AND material_version=? "
                "AND status IN ('complete','partial') AND id<? ORDER BY id DESC LIMIT 1",
                (int(project_id), current_version, int(row["id"])),
            ).fetchone()
            if fallback is not None:
                previous = self._reading_public(fallback, current_version)
                for key in ("summary", "demand_type", "stage", "buyer", "region", "category",
                            "sections", "milestones", "coverage", "conflicts"):
                    public[key] = previous[key]
                unresolved = list(previous.get("unresolved") or [])
                unresolved.append("本轮采购阅读尚未成功，以上保留上一次已保存说明供复核")
                public["unresolved"] = list(dict.fromkeys(unresolved))
                public["status"] = str(row["status"] or "failed")
                public["id"] = int(row["id"])
                public["revision"] = int(row["revision"] or 1)
                public["error"] = str(row["error"] or "") or None
                public["updated_at"] = row["updated_at"]
        return public

    def _save_procurement_reading(self, project_id: int, reading: Mapping[str, Any], *, material_version: str,
                                  config: Mapping[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        value = dict(reading)
        status = str(value.get("status") or "partial")
        if status not in {"pending", "complete", "partial", "failed", "not_configured"}:
            status = "failed"
        category = value.get("category") if isinstance(value.get("category"), list) else []
        sections = value.get("sections") if isinstance(value.get("sections"), list) else []
        milestones = value.get("milestones") if isinstance(value.get("milestones"), list) else []
        coverage = value.get("coverage") if isinstance(value.get("coverage"), list) else []
        conflicts = value.get("conflicts") if isinstance(value.get("conflicts"), list) else []
        unresolved = value.get("unresolved") if isinstance(value.get("unresolved"), list) else []
        error_value = str(error or value.get("error") or "")[:500] or None
        current = now_iso()
        model = str((config or {}).get("model") or "")[:256]
        fingerprint = self._reading_request_fingerprint(material_version, config or {})
        with self._db_lock, self.db:
            previous = self.db.execute(
                "SELECT COALESCE(MAX(revision),0) FROM procurement_readings WHERE project_id=? AND material_version=?",
                (int(project_id), material_version),
            ).fetchone()
            revision = int(previous[0] or 0) + 1 if previous else 1
            self.db.execute(
                """INSERT INTO procurement_readings
                (project_id,version,revision,material_version,status,stale,summary,demand_type,stage,buyer,region,
                 category_json,sections_json,milestones_json,coverage_json,conflicts_json,unresolved_json,
                 input_fingerprint,model,error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(project_id), int(value.get("version") or 1), revision, material_version, status, 0,
                 str(value.get("summary") or "")[:4_000], str(value.get("demand_type") or "unknown")[:20],
                 str(value.get("stage") or "")[:200], str(value.get("buyer") or "")[:500],
                 str(value.get("region") or "")[:500], json_text(category), json_text(sections),
                 json_text(milestones), json_text(coverage), json_text(conflicts), json_text(unresolved),
                 fingerprint, model, error_value, current, current),
            )
        return self._procurement_reading_for_project(int(project_id))

    @staticmethod
    def _reading_request_fingerprint(material_version: str, config: Mapping[str, Any]) -> str:
        return sha256_text(json_text({"material_version": material_version,
                                      "model": config.get("model"), "base_url": config.get("base_url"),
                                      "engine": config.get("engine"), "enabled": config.get("enabled")}))

    def queue_reading(self, project_id: int, *, force: bool = False, schedule: bool = False) -> dict[str, Any]:
        """Create a durable pending reading and optionally run it now."""

        project_id = int(self._project_row(project_id)["id"])
        material = self._reading_material(project_id)
        version = str(material["material_version"])
        config = self._ai_config()
        if not config["configured"]:
            return {"status": "not_configured", "reading": self._reading_public(
                self._reading_row(project_id, version), version, configured=False)}
        current = self._reading_row(project_id, version)
        current_fingerprint = self._reading_request_fingerprint(version, config)
        if (current and not force and str(current["input_fingerprint"] or "") == current_fingerprint
                and str(current["status"] or "") == "pending"):
            return {"status": "pending", "reading": self._reading_public(current, version)}
        if (current and not force and str(current["status"] or "") in {"complete", "partial"}
                and str(current["input_fingerprint"] or "") == current_fingerprint):
            return {"status": str(current["status"]), "reading": self._reading_public(current, version)}
        pending = empty_reading(version, status="pending")
        self._save_procurement_reading(project_id, pending, material_version=version, config=config)
        if schedule:
            self.schedule_reading(project_id, force=force)
        return {"status": "queued", "reading": self._procurement_reading_for_project(project_id)}

    def schedule_reading(self, project_id: int, *, force: bool = False) -> None:
        project_id = int(self._project_row(project_id)["id"])
        with self._db_lock, self.db:
            queued = self.db.execute(
                "SELECT 1 FROM procurement_reading_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",
                (project_id,),
            ).fetchone()
            if queued is None:
                self.db.execute(
                    "INSERT INTO procurement_reading_jobs(project_id,force,status,created_at,updated_at) VALUES (?,?,'queued',?,?)",
                    (project_id, int(force), now_iso(), now_iso()),
                )
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = self._spawn(self._worker_loop())

    async def _run_reading_job(self, reading_job_id: int) -> None:
        with self._db_lock, self.db:
            row = self.db.execute("SELECT * FROM procurement_reading_jobs WHERE id=?", (reading_job_id,)).fetchone()
            if row is None or row["status"] not in {"queued", "running"}:
                return
            project_id = int(row["project_id"])
            force = bool(row["force"])
            self.db.execute(
                "UPDATE procurement_reading_jobs SET status='running',attempts=attempts+1,started_at=?,updated_at=?,error=NULL WHERE id=?",
                (now_iso(), now_iso(), reading_job_id),
            )
        try:
            result = await self.run_reading(project_id, force=force)
            status = "succeeded" if result.get("status") in {"complete", "partial", "not_configured"} else "failed"
            error = result.get("error") or ("reading status: " + str(result.get("status"))) if status == "failed" else None
            with self._db_lock, self.db:
                self.db.execute(
                    "UPDATE procurement_reading_jobs SET status=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                    (status, str(error or "")[:500] or None, now_iso(), now_iso(), reading_job_id),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            safe_error = sanitize_ai_error(exc, secrets=[str(self._ai_config().get("api_key") or "")])
            with self._db_lock, self.db:
                self.db.execute(
                    "UPDATE procurement_reading_jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                    (safe_error[:500], now_iso(), now_iso(), reading_job_id),
                )

    async def run_reading(self, project_id: int, *, force: bool = False, bypass_gate: bool = True) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        await self._ensure_reading_documents(project_id)
        material = self._reading_material(project_id)
        version = str(material["material_version"])
        config = self._ai_config()
        if not config["configured"]:
            return self._save_procurement_reading(
                project_id, empty_reading(version, status="not_configured"), material_version=version, config=config,
            ) if self._reading_row(project_id, version) else empty_reading(version, status="not_configured")
        current = self._reading_row(project_id, version)
        current_fingerprint = self._reading_request_fingerprint(version, config)
        if (current and not force and str(current["status"] or "") in {"complete", "partial"}
                and str(current["input_fingerprint"] or "") == current_fingerprint):
            return self._reading_public(current, version)
        # Legacy injected providers implement the judgment protocol only. Do
        # not add a second call to them; explicit read/read_procurement methods
        # opt into the new module while preserving all existing test doubles.
        explicit = callable(getattr(self.provider, "read_procurement", None)) or callable(getattr(self.provider, "read", None))
        if self._injected_provider and not explicit and not bool(getattr(self.provider, "supports_procurement_reading", False)):
            return self._reading_public(self._reading_row(project_id, version), version)
        pending = empty_reading(version, status="pending")
        self._save_procurement_reading(project_id, pending, material_version=version, config=config)
        provider: Any = self.provider
        if config.get("engine") == "single_call" and isinstance(provider, PiSDKProvider) and not self._injected_provider:
            provider = OpenAICompatibleProvider()
        try:
            result = await invoke_reading(provider, material=material, config=config)
            saved = self._save_procurement_reading(project_id, result, material_version=version, config=config)
        except ReadingProtocolMismatch:
            # The compatibility caller consumes the returned content as the
            # judgment response in the same job. Keep the reading stage
            # explicitly failed for operators, without storing model output.
            message = "reading runtime task protocol is unavailable"
            failed = empty_reading(version, status="failed", error=message)
            self._save_procurement_reading(project_id, failed, material_version=version, config=config, error=message)
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = sanitize_ai_error(exc, secrets=[str(config.get("api_key") or "")])
            failed = empty_reading(version, status="failed", error=message)
            saved = self._save_procurement_reading(project_id, failed, material_version=version, config=config, error=message)
        # A newly read material package invalidates the recommendation that
        # depended on the previous package. Preserve the old judgment and
        # queue a fresh one only when this was an explicit reading request;
        # run_job itself is already the active queue item.
        with self._db_lock, self.db:
            judgment = self.db.execute(
                "SELECT id,stale FROM judgments WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)
            ).fetchone()
            running = self.db.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND status='running' LIMIT 1", (project_id,)
            ).fetchone()
            if judgment is not None and (force or saved.get("status") in {"complete", "partial", "failed"}):
                self.db.execute("UPDATE judgments SET stale=1 WHERE project_id=? AND stale=0", (project_id,))
                self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
        if judgment is not None and not running and (force or saved.get("status") in {"complete", "partial"}):
            self.queue_project(project_id, force=True, schedule=True)
        return saved

    async def _refresh_record_documents(self, project_id: int, records: list[dict[str, Any]], *,
                                        preloaded: dict[int, list[dict[str, Any]]] | None = None,
                                        queue_reading: bool = True) -> dict[str, Any]:
        """Fetch and persist attachment documents without invoking an AI provider."""
        before = self._procurement_for_project(project_id)
        target_record_ids = [int(record["id"]) for record in records]
        previous = self._document_sources_for_records(target_record_ids)
        fetched: list[dict[str, Any]] = []
        for record in records:
            record_id = int(record["id"])
            request_record = {
                "id": record_id, "record_id": record_id,
                "source_url": record.get("original_url") or record.get("canonical_url") or "",
                "original_url": record.get("original_url") or "",
                "canonical_url": record.get("canonical_url") or "",
                "body_text": record.get("body_text") or "",
                "attachments": record.get("attachments") if isinstance(record.get("attachments"), list) else [],
            }
            try:
                result = (preloaded[record_id] if preloaded is not None and record_id in preloaded
                          else await fetch_record_documents(request_record))
                if not isinstance(result, list):
                    raise ValueError("invalid_document_result")
            except Exception as exc:
                raw_code = str(exc).strip()
                safe_code = raw_code if raw_code in {"auth_required", "source_unreachable", "download_failed",
                                                      "attachment_too_large", "redirect_limit", "invalid_source_url",
                                                      "attachment_outside_source", "non_public_source"} else "fetch_failed"
                result = [{"id": f"fetch-{record_id}", "record_id": record_id,
                           "name": "附件获取失败", "url": request_record["source_url"],
                           "status": "auth_required" if safe_code == "auth_required" else "failed",
                           "error": safe_code}]
            for raw in result:
                document = self._normalize_document(raw, record_id)
                if document is not None:
                    fetched.append(document)

        with self._db_lock, self.db:
            if target_record_ids:
                placeholders = ",".join("?" for _ in target_record_ids)
                # Keep superseded rows for historical citations, but exclude
                # them from the current aggregate after every refresh. An
                # empty result therefore also reflects attachment deletion.
                # User-supplemented files are independent material. A public
                # source refresh must not hide them when the collector's
                # attachment list changes or returns an empty result.
                self.db.execute(f"UPDATE procurement_documents SET is_current=0 WHERE record_id IN ({placeholders}) "
                                "AND id NOT LIKE 'supplement-%'", tuple(target_record_ids))
            # An observed read failure is different from an observed removal.
            # Retain the last successful bytes alongside the failed attempt;
            # the aggregate stays partial and shows both timestamps/statuses.
            for saved in previous:
                failed = [value for value in fetched if value["record_id"] == saved["record_id"]
                          and value["status"] != "parsed"
                          and (value["url"] == saved["url"] or value["id"].startswith("fetch-"))]
                replaced = any(value["record_id"] == saved["record_id"] and value["status"] == "parsed"
                               and value["url"] == saved["url"] for value in fetched)
                if saved["status"] == "parsed" and failed and not replaced:
                    self.db.execute("UPDATE procurement_documents SET is_current=1 WHERE id=?", (saved["id"],))
            for document in fetched:
                existing = self.db.execute("SELECT sha256,status FROM procurement_documents WHERE id=?", (document["id"],)).fetchone()
                if existing and existing["status"] == "parsed" and document["status"] != "parsed":
                    attempt_key = ":".join(document[key] for key in ("id", "status", "error", "sha256"))
                    document["id"] = f"attempt-{sha256_text(attempt_key)[:32]}"
                    document["fetched_at"] = document["fetched_at"] or now_iso()
                if existing and existing["sha256"] and document["sha256"] and existing["sha256"] != document["sha256"]:
                    old_id = document["id"]
                    document["id"] = f"{old_id}-{document['sha256'][:16]}"
                    for item in document["items"]:
                        if item.get("source_document_id") == old_id:
                            item["source_document_id"] = document["id"]
                self.db.execute(
                    """INSERT INTO procurement_documents
                    (id,record_id,name,url,status,pages,sha256,fetched_at,mime_type,byte_size,text,page_texts_json,items_json,error,is_current,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET record_id=excluded.record_id,name=excluded.name,url=excluded.url,
                    status=excluded.status,pages=excluded.pages,sha256=excluded.sha256,fetched_at=excluded.fetched_at,
                    mime_type=excluded.mime_type,byte_size=excluded.byte_size,text=excluded.text,
                    page_texts_json=excluded.page_texts_json,items_json=excluded.items_json,error=excluded.error,
                    is_current=1,updated_at=excluded.updated_at""",
                    (document["id"], document["record_id"], document["name"], document["url"], document["status"],
                     document["pages"], document["sha256"], document["fetched_at"], document["mime_type"],
                     document["byte_size"], document["text"], json_text(document["page_texts"]),
                     json_text(document["items"]), document["error"], 1, now_iso()),
                )
            self.db.execute(
                "INSERT INTO procurement_refreshes(project_id,attempted_at,status,message) VALUES (?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET attempted_at=excluded.attempted_at,status=excluded.status,message=excluded.message",
                (project_id, now_iso(), "succeeded", "附件已刷新" if fetched else "未发现可解析附件"),
            )
            after = self._procurement_for_project(project_id)
            semantic_changed = self._procurement_fingerprint(before) != self._procurement_fingerprint(after)
            if semantic_changed:
                self.db.execute("UPDATE judgments SET stale=1 WHERE project_id=?", (project_id,))
            self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
            for record in records:
                self.db.execute(
                    "INSERT INTO procurement_attachment_fetches(record_id,record_version,status,updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(record_id) DO UPDATE SET record_version=excluded.record_version,status=excluded.status,updated_at=excluded.updated_at",
                    (int(record["id"]), int(record.get("current_version") or 1), "attempted", now_iso()),
                )
        if queue_reading and self._ai_config()["configured"] and self._reading_gate_enabled():
            # Attachment acquisition is a material change. Queue a fresh
            # source reading so cards/detail/研判 can converge on one result.
            self.queue_reading(project_id, force=semantic_changed, schedule=True)
        return after

    async def _ensure_reading_documents(self, project_id: int) -> None:
        """Acquire public attachments once per record version before reading.

        The fetch marker prevents retrying the same network operation on every
        model retry.  The explicit refresh endpoint remains available for a
        user-requested retry and always calls the existing document pipeline.
        """

        records = self._records_for_project(int(project_id))
        candidates = [record for record in records
                      if isinstance(record.get("attachments"), list) and record.get("attachments")]
        if not candidates:
            return
        pending: list[dict[str, Any]] = []
        for record in candidates:
            marker = self.db.execute(
                "SELECT record_version FROM procurement_attachment_fetches WHERE record_id=?",
                (int(record["id"]),),
            ).fetchone()
            if marker is None or int(marker["record_version"] or 0) != int(record.get("current_version") or 1):
                pending.append(record)
        if pending:
            await self._refresh_record_documents(int(project_id), pending, queue_reading=False)

    async def refresh_project_documents(self, project_id: int) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        records = self._records_for_project(project_id)
        try:
            procurement = await self._refresh_record_documents(project_id, records)
            message = "采购附件刷新完成"
        except ServiceError as exc:
            with self._db_lock, self.db:
                self.db.execute(
                    "INSERT INTO procurement_refreshes(project_id,attempted_at,status,message) VALUES (?,?,?,?) "
                    "ON CONFLICT(project_id) DO UPDATE SET attempted_at=excluded.attempted_at,status=excluded.status,message=excluded.message",
                    (project_id, now_iso(), "failed", str(exc)[:240]),
                )
                self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
            procurement = self._procurement_for_project(project_id)
            message = str(exc)
        return {"procurement": procurement, "message": message}

    def document_snapshot(self, project_id: int, document_id: str, *, sha256: str = "") -> dict[str, Any]:
        """Serve the saved version, including superseded files, within its project."""
        self._project_row(project_id)
        row = self.db.execute(
            "SELECT d.* FROM procurement_documents d JOIN project_records pr ON pr.record_id=d.record_id "
            "WHERE pr.project_id=? AND d.id=?", (project_id, document_id),
        ).fetchone()
        if row is None or (sha256 and row["sha256"] != sha256):
            raise NotFoundError("附件快照不存在或版本不匹配")
        document = self._document_row_value(row)
        return {**self._document_public_metadata(document), "text": document["text"],
                "page_texts": document["page_texts"], "items": document["items"]}

    async def _agent_fetch_documents(self, project_id: int, arguments: Any, *,
                                     cache: dict[int, list[dict[str, Any]]] | None = None,
                                     records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            request = validate_document_access_arguments(arguments)
        except DocumentAccessError as exc:
            raise AIError("工具参数无效") from exc
        value = request.record_id
        records = records if records is not None else self._records_for_project(project_id)
        allowed = {int(record["id"]): record for record in records}
        if value is None:
            targets = records
        elif int(value) in allowed:
            targets = [allowed[int(value)]]
        else:
            raise AIError("工具无权限")
        cache = cache if cache is not None else {}
        unread = [record for record in targets if int(record["id"]) not in cache]
        for record in unread:
            archived = await asyncio.to_thread(read_archived_documents, record, self.archive_root)
            # Reuse only a complete set of verified archive files. Otherwise
            # normal acquisition reports missing/failed files explicitly.
            attachments = record.get("attachments", [])
            if (archived and isinstance(attachments, list) and len(archived) == len(attachments)
                    and all(document["status"] == "parsed" for document in archived)):
                await self._refresh_record_documents(project_id, [record], preloaded={int(record["id"]): archived})
                cache[int(record["id"])] = self._document_sources_for_records([int(record["id"])])
            else:
                await self._refresh_record_documents(project_id, [record])
                cache[int(record["id"])] = self._document_sources_for_records([int(record["id"])])
        documents = [document for record in targets for document in cache[int(record["id"])]]
        try:
            view = build_document_view(documents, request)
        except DocumentAccessError as exc:
            raise AIError("工具无权限" if exc.code == "record_not_in_project" else "工具参数无效") from exc
        # A compact summary avoids re-injecting thousands of material rows on
        # each page read; full procurement detail remains in the API and DB.
        procurement = self._procurement_for_project(project_id)
        compact = {key: procurement.get(key) for key in (
            "status", "scope_summary", "delivery_period", "delivery_location", "attachment_status", "attachment_note",
        )}
        return {**view, "procurement": compact}

    @staticmethod
    def _analysis_record(record: dict[str, Any]) -> dict[str, Any]:
        # Keep the context self-contained while avoiding collector payloads and
        # local archive paths.  ``record_id`` is explicit because it is the
        # citation identity used by the Pi agent.
        full_body = str(record.get("body_text") or "")
        attachment_metadata = []
        for attachment in record.get("attachments", []) if isinstance(record.get("attachments"), list) else []:
            if not isinstance(attachment, dict):
                continue
            attachment_metadata.append({key: attachment.get(key) for key in (
                "name", "url", "status", "sha256", "mime_type", "byte_size", "pages",
            )})
        value = {
            "id": record.get("id"),
            "record_id": record.get("id"),
            "source_id": record.get("source_id"),
            "kind": record.get("kind"),
            "title": record.get("title"),
            "project_name": record.get("project_name"),
            "published_at": record.get("published_at"),
            "buyer": record.get("buyer"),
            "notice_number": record.get("notice_number"),
            "deadline_at": record.get("deadline_at"),
            "original_url": record.get("original_url"),
            "canonical_url": record.get("canonical_url"),
            "parse_status": record.get("parse_status"),
            "equipment": record.get("equipment", []),
            "facts": record.get("facts", []),
            "inferences": record.get("inferences", []),
            "body_text": bounded_context_body(full_body),
            # Keep the complete source identity even when the protocol body is
            # truncated, so changes in an unseen tail invalidate the input.
            "body_text_sha256": sha256_text(full_body),
            "attachments": attachment_metadata,
            "attachments_sha256": sha256_text(json_text(attachment_metadata)),
            "related_record_id": record.get("related_record_id"),
        }
        return value

    @staticmethod
    def _merge_job_document_reads(current: list[dict[str, Any]], returned: Any,
                                   target_record_ids: set[int]) -> list[dict[str, Any]]:
        """Accumulate actual read windows without treating unread text as evidence."""
        registry = {(document["record_id"], document["id"]): copy.deepcopy(document) for document in current}
        for document in returned if isinstance(returned, list) else []:
            if not isinstance(document, dict) or document.get("record_id") not in target_record_ids:
                continue
            key = (document["record_id"], document.get("id"))
            saved = registry.get(key)
            if saved is None or saved.get("sha256") != document.get("sha256"):
                saved = {**copy.deepcopy(document), "read_fragments": []}
                registry[key] = saved
            fragments = document.get("page_texts", [])
            for fragment in fragments if isinstance(fragments, list) else []:
                if isinstance(fragment, dict) and fragment.get("text"):
                    value = {"page": fragment.get("page"), "text": str(fragment["text"])}
                    if value not in saved["read_fragments"]:
                        saved["read_fragments"].append(value)
            # A document-level or DOCX window has no page number; retain its
            # continuous text as a separate window, never concatenate reads.
            if document.get("text"):
                value = {"page": None, "text": str(document["text"])}
                if value not in saved["read_fragments"]:
                    saved["read_fragments"].append(value)
        return list(registry.values())

    def _analysis_project_snapshot(self, project_id: int, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        project_row = self._project_row(project_id)
        records = records if records is not None else self._records_for_project(project_id)
        latest = records[0] if records else {}
        gate, gate_reasons = self._gate(records, project_id)
        procurement = self._procurement_for_project(project_id)
        procurement_reading = self._procurement_reading_for_project(project_id)
        return {
            "id": int(project_id),
            "title": latest.get("title") or f"项目 {project_id}",
            "buyer": latest.get("buyer"),
            "kind": latest.get("kind") or "tender",
            "source_id": latest.get("source_id") or "",
            "published_at": latest.get("published_at"),
            "deadline_at": latest.get("deadline_at"),
            "record_ids": [int(record["id"]) for record in records if record.get("id") is not None],
            "gate_status": gate,
            "gate_reasons": gate_reasons,
            "records": [self._analysis_record(record) for record in records],
            # The aggregate contains only server-parsed facts and document
            # metadata. Full text remains available through the trusted tool.
            "procurement": build_procurement_context(procurement),
            "procurement_fingerprint": self._procurement_fingerprint(procurement),
            # The AI judgment consumes the same persisted reading that cards
            # and details expose. A stale reading remains visible as evidence
            # but is explicitly marked for the model and reviewer.
            "procurement_reading": procurement_reading,
            "procurement_reading_fingerprint": sha256_text(json_text(procurement_reading)),
            "updated_at": project_row["updated_at"],
        }

    def _analysis_related_context(self, project_id: int,
                                  records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Build bounded context only from explicit record refs and active links."""
        records = records if records is not None else self._records_for_project(project_id)
        related: dict[int, set[str]] = {}
        for record in records:
            for key in ("possible_duplicate_of", "duplicate_of", "related_record_id"):
                value = record.get(key)
                if value is None:
                    continue
                related_project = self._project_for_record_id(int(value))
                if related_project is not None and related_project != project_id:
                    related.setdefault(related_project, set()).add("record_ref")
        for row in self.db.execute(
                "SELECT project_id,target_project_id,relation FROM links "
                "WHERE active=1 AND relation IN ('same_project','duplicate') "
                "AND (project_id=? OR target_project_id=?)", (project_id, project_id)).fetchall():
            other = int(row["target_project_id"] if int(row["project_id"]) == project_id else row["project_id"])
            if other != project_id:
                related.setdefault(other, set()).add(str(row["relation"]))

        output: list[dict[str, Any]] = []
        record_count = 0
        for related_id in sorted(related)[:5]:
            related_records = self._records_for_project(related_id)
            if record_count >= 20 or not related_records:
                break
            remaining = 20 - record_count
            selected = related_records[:remaining]
            latest = selected[0]
            output.append({
                "id": related_id,
                "title": latest.get("title") or f"项目 {related_id}",
                "relation": ",".join(sorted(related[related_id])),
                "records": [self._analysis_record(record) for record in selected],
            })
            record_count += len(selected)
        return output

    def _analysis_related_validation_records(self, related_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return full source snapshots for exactly the records sent in context.

        The SDK receives bounded bodies with an explicit truncation marker. A
        validator must never treat that marker as source evidence, so it uses
        this private snapshot captured before the provider is awaited.  Record
        IDs are taken only from the internally-built related context and are
        checked against their project membership in SQLite.
        """
        output: list[dict[str, Any]] = []
        for related_project in related_context:
            if not isinstance(related_project, dict):
                continue
            try:
                project_id = int(related_project.get("id"))
            except (TypeError, ValueError):
                continue
            record_ids: list[int] = []
            for record in related_project.get("records", []):
                if not isinstance(record, dict):
                    continue
                try:
                    record_id = int(record.get("id", record.get("record_id")))
                except (TypeError, ValueError):
                    continue
                if record_id not in record_ids:
                    record_ids.append(record_id)
            if not record_ids:
                continue
            placeholders = ",".join("?" for _ in record_ids)
            rows = self.db.execute(
                f"SELECT r.* FROM records r JOIN project_records pr ON pr.record_id=r.id "
                f"WHERE pr.project_id=? AND r.id IN ({placeholders})",
                (project_id, *record_ids),
            ).fetchall()
            by_id = {int(row["id"]): self._record_dict(row) for row in rows}
            output.extend(by_id[record_id] for record_id in record_ids if record_id in by_id)
        return output

    def _related_context_fingerprints(self, project_ids: list[int] | tuple[int, ...] | None = None) -> dict[int, str]:
        """Fingerprint only the bounded related snapshots used by analysis.

        This intentionally excludes the primary project and capability card.
        It lets imports and human links invalidate conclusions that actually
        consumed a related project without expiring older conclusions that
        had no related context or were created before this feature existed.
        """
        if project_ids is None:
            project_ids = [int(row["id"]) for row in self.db.execute("SELECT id FROM projects ORDER BY id").fetchall()]
        fingerprints: dict[int, str] = {}
        for project_id in project_ids:
            try:
                related = self._analysis_related_context(int(project_id))
            except (NotFoundError, sqlite3.Error):
                continue
            fingerprints[int(project_id)] = sha256_text(json_text(related))
        return fingerprints

    def _mark_related_context_changes(self, before: dict[int, str]) -> list[int]:
        """Mark judgments stale where an import/link changed included context."""
        changed: list[int] = []
        after = self._related_context_fingerprints(list(before))
        for project_id, old_fingerprint in before.items():
            new_fingerprint = after.get(project_id)
            if new_fingerprint is None or new_fingerprint == old_fingerprint:
                continue
            self.db.execute("UPDATE judgments SET stale=1 WHERE project_id=?", (project_id,))
            self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
            changed.append(project_id)
        return changed

    def _analysis_context(self, project_id: int, capability: dict[str, Any],
                          records: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        records = records if records is not None else self._records_for_project(project_id)
        related = self._analysis_related_context(project_id, records)
        return {
            "project": self._analysis_project_snapshot(project_id, records),
            **analysis_context(capability),
            "related_projects": copy.deepcopy(related),
            "research": {"policy_version": RESEARCH_POLICY_VERSION,
                         "sources": list_sources(self.db, project_id)},
        }, related

    def _project_row(self, project_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            # Project ids are normally the anchor record id. For a grouped
            # record, accepting its record id as an alias keeps detail links
            # resilient without changing the canonical summary id.
            alias = self.db.execute("SELECT project_id FROM project_records WHERE record_id=?", (project_id,)).fetchone()
            if alias:
                row = self.db.execute("SELECT * FROM projects WHERE id=?", (int(alias["project_id"]),)).fetchone()
        if not row:
            raise NotFoundError("project not found")
        return row

    def _latest_judgment(self, project_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM judgments WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def _latest_job(self, project_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def _latest_decision(self, project_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    def _latest_agent_run(self, project_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM agent_runs WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()

    @staticmethod
    def _agent_run_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        usage = json_value(row["usage_json"], {})
        limits = json_value(row["limits_json"], {})
        steps = json_value(row["steps_json"], [])
        if not isinstance(usage, dict):
            usage = {}
        if not isinstance(limits, dict):
            limits = {}
        if not isinstance(steps, list):
            steps = []
        usage = {key: int(usage.get(key, 0) or 0) for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens")}
        limits = {key: int(limits.get(key, DEFAULT_AGENT_LIMITS[key]) or DEFAULT_AGENT_LIMITS[key])
                  for key in DEFAULT_AGENT_LIMITS}
        safe_steps = []
        for step in steps:
            if not isinstance(step, dict) or step.get("stage") not in {"started", "model", "tool_start", "tool_end", "completed"}:
                continue
            value = {"stage": step["stage"], "label": str(step.get("label") or "")[:120],
                     "at": str(step.get("at") or "")[:80]}
            if isinstance(step.get("tool"), str) and step["tool"]:
                value["tool"] = step["tool"][:100]
            if isinstance(step.get("turn"), int):
                value["turn"] = step["turn"]
            safe_steps.append(value)
        return {
            "id": int(row["id"]), "job_id": int(row["job_id"]), "engine": "pi_sdk",
            "status": row["status"] if row["status"] in {"running", "succeeded", "failed"} else "failed",
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "error": row["error"], "steps": safe_steps, "usage": usage, "limits": limits,
            "investigation": json_value(row["investigation_json"], {}) or None,
        }

    def research_runs(self, project_id: int) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        rows = self.db.execute("SELECT * FROM agent_runs WHERE project_id=? ORDER BY id DESC LIMIT 50",
                               (project_id,)).fetchall()
        total = self.db.execute("SELECT COUNT(*) FROM agent_runs WHERE project_id=?", (project_id,)).fetchone()[0]
        return {"items": [self._agent_run_dict(row) for row in rows], "total": int(total)}

    def research_run(self, project_id: int, run_id: int) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        row = self.db.execute("SELECT * FROM agent_runs WHERE project_id=? AND id=?",
                              (project_id, run_id)).fetchone()
        value = self._agent_run_dict(row)
        if value is None:
            raise NotFoundError("调查运行记录不存在")
        return value

    def research_source_snapshot(self, project_id: int, source_ref: str, version: str, *,
                                 page: int | None = None, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        try:
            return read_saved_source(self.db, project_id, source_ref, version,
                                     page=page, offset=offset, limit=limit)
        except (LookupError, ValueError, PermissionError) as exc:
            raise NotFoundError("调查来源版本不存在或不属于当前项目") from exc

    def _source_data_updated_at(self) -> str | None:
        """Return the collector's update time, rather than this app's import time.

        The export file is the source of truth for freshness.  Keeping this
        separate from ``last_import_at`` avoids presenting a recent import as
        if the collector had run recently.
        """
        try:
            stat = self.source_path.stat()
        except OSError:
            return self._get_meta("data_updated_at")
        return datetime.fromtimestamp(stat.st_mtime, SHANGHAI).isoformat(timespec="seconds")

    def _source_age_stale(self) -> bool:
        try:
            mtime = self.source_path.stat().st_mtime
        except OSError:
            return True
        return datetime.now(SHANGHAI).timestamp() - float(mtime) > timedelta(hours=48).total_seconds()

    def _import_pending(self) -> bool:
        return self._file_signature(self.source_path) != self._last_import_signature

    def _collector_data_root(self) -> Path:
        """Resolve the read-only collector data root for trial-status.md.

        The default export lives in ``<collector>/data/export``.  A configured
        export follows the same shape; a bare test/export file is paired with
        its containing directory.  No path derived from record data is used.
        """
        source = self.source_path.resolve()
        parent = source.parent
        if parent.name.lower() == "export":
            return parent.parent
        if parent.name.lower() == "data":
            return parent
        return parent

    def _source_notes(self, *, include_path: bool = False) -> str:
        rows = self.db.execute("SELECT source_id,source_name,equipment_json,deadline_at,buyer,title,body_text FROM records ORDER BY id").fetchall()
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
            notes.append(f"源文件：{self.source_path}")
        if rows:
            names = "、".join(sorted(source_names.values()))
            notes.append(f"当前导出已导入 {len(rows)} 条记录，覆盖 {len(source_names)} 个来源：{names}。")
            labels = {"equipment": "设备信息", "deadline_at": "截止时间", "buyer": "采购方", "body_text": "正文"}
            missing_parts = [f"{labels[key]}缺失{count}条" for key, count in missing.items() if count]
            notes.append("字段缺失提示：" + ("、".join(missing_parts) if missing_parts else "未发现主要字段缺失") + "。")
        else:
            notes.append("当前暂无已导入记录，来源与字段覆盖未知。")
        notes.append("覆盖范围：仅当前导出文件，不能据此推断已覆盖全网公告或全部来源。")

        trial_status = self._collector_data_root() / "trial-status.md"
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

    def _all_project_summaries(self) -> list[dict[str, Any]]:
        return [self.summary(int(row["id"])) for row in self.db.execute("SELECT id FROM projects ORDER BY id").fetchall()]

    @staticmethod
    def _is_deadline_soon(item: dict[str, Any], *, now: datetime | None = None) -> bool:
        now = now or datetime.now(SHANGHAI)
        deadline = parse_timestamp(item.get("deadline_at"), deadline=True)
        return bool(deadline and now <= deadline <= now + timedelta(days=7) and
                    item.get("gate_status") in {"candidate", "review"})

    @staticmethod
    def _reading_submission_date(item: dict[str, Any]) -> Any:
        reading = item.get("procurement_reading") if isinstance(item, dict) else None
        if not isinstance(reading, dict):
            return None
        milestones = reading.get("milestones")
        if not isinstance(milestones, list):
            return None
        dates = []
        for milestone in milestones:
            if not isinstance(milestone, dict) or milestone.get("kind") != "submission":
                continue
            value = parse_timestamp(milestone.get("at"), deadline=True)
            if value is not None:
                dates.append(value.astimezone(SHANGHAI).date() if value.tzinfo else value.date())
        return min(dates) if dates else None

    def _reading_gate_enabled(self) -> bool:
        return (not self._injected_provider
                or callable(getattr(self.provider, "read_procurement", None))
                or callable(getattr(self.provider, "read", None))
                or bool(getattr(self.provider, "supports_procurement_reading", False)))

    def _gate(self, records: list[dict[str, Any]], project_id: int) -> tuple[str, list[str]]:
        override_row = self.db.execute("SELECT * FROM project_gate WHERE project_id=?", (project_id,)).fetchone()
        override = dict(override_row) if override_row else None
        if not self._reading_gate_enabled():
            return gate_for_project(records, self.get_settings(), now=datetime.now(SHANGHAI), override=override)

        # Collector deadline_at is not known to be a submission deadline.  It
        # may be the public-document acquisition window, so an expired value
        # cannot block the first source-grounded reading.  Preserve all other
        # rule checks and let the normalized submission milestone decide
        # expiration after a successful reading.
        now = datetime.now(SHANGHAI)
        adjusted: list[dict[str, Any]] = []
        for record in records:
            value = dict(record)
            deadline = parse_timestamp(value.get("deadline_at"), deadline=True)
            if deadline and deadline < now:
                value["deadline_at"] = None
            adjusted.append(value)
        gate, reasons = gate_for_project(adjusted, self.get_settings(), now=now, override=override)

        try:
            material = self._reading_material(int(project_id))
            row = self._reading_row(int(project_id), str(material.get("material_version") or ""))
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            row = None
        if row is None:
            if gate == "expired":
                gate = "review"
                reasons.append("reading has not verified a submission deadline")
            return gate, reasons
        status = str(row["status"] or "")
        unresolved = json_value(row["unresolved_json"], [])
        conflicts = json_value(row["conflicts_json"], [])
        milestones = json_value(row["milestones_json"], [])
        verified_submission = None
        if status == "complete" and not unresolved and not conflicts and isinstance(milestones, list):
            for milestone in milestones:
                if not isinstance(milestone, dict) or milestone.get("kind") != "submission":
                    continue
                candidate = parse_timestamp(milestone.get("at"), deadline=True)
                if candidate is not None:
                    verified_submission = candidate
                    break
        if verified_submission is not None and verified_submission < now:
            return "expired", reasons + ["reading verified the submission deadline has passed"]
        if gate == "expired":
            return "review", reasons + ["raw collector deadline is not a verified submission deadline"]
        return gate, reasons

    def _judgment_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        metadata = json_value(row["analysis_meta_json"], None)
        report_value = json_value(row["report_json"], None)
        report = report_value if isinstance(report_value, dict) else None
        dependencies = json_value(row["research_dependencies_json"], [])
        changed_sources = self._research_dependencies_changed(int(row["project_id"]), dependencies)
        run = None
        if row["agent_run_id"] is not None:
            run = self._agent_run_dict(self.db.execute("SELECT * FROM agent_runs WHERE id=? AND project_id=?",
                                      (row["agent_run_id"], row["project_id"])).fetchone())
        investigation = run.get("investigation") if run else None
        changed_configuration = bool(isinstance(investigation, dict)
                                     and investigation.get("configuration_fingerprint")
                                     and investigation["configuration_fingerprint"] != configuration_fingerprint(
                                         self._research_sources_path))
        return {
            "id": int(row["id"]), "created_at": row["created_at"], "model": row["model"],
            "input_fingerprint": row["input_fingerprint"],
            "stale": bool(row["stale"]) or not current_analysis_meta(metadata)
                     or row["research_policy_version"] != RESEARCH_POLICY_VERSION or changed_sources or changed_configuration,
            "analysis_meta": metadata if isinstance(metadata, dict) else None,
            "decision": row["decision"], "summary": row["summary"], "demand_summary": row["demand_summary"],
            "reasons": json_value(row["reasons_json"], []), "unknowns": json_value(row["unknowns_json"], []),
            "next_actions": json_value(row["next_actions_json"], []), "options": json_value(row["options_json"], []),
            "report": report,
            "agent_run_id": int(row["agent_run_id"]) if row["agent_run_id"] is not None else None,
            "investigation": run.get("investigation") if run else {
                "policy_version": RESEARCH_POLICY_VERSION, "status": "not_performed",
                "stop_reason": "该报告未执行主动补查", "events": [], "sources": [], "unresolved": [],
            },
        }

    def summary(self, project_id: int) -> dict[str, Any]:
        project_row = self._project_row(project_id)
        project_id = int(project_row["id"])
        records = self._records_for_project(project_id)
        gate, reasons = self._gate(records, project_id)
        latest = records[0] if records else {}
        judgment_row = self._latest_judgment(project_id)
        judgment = self._judgment_dict(judgment_row)
        job = self._latest_job(project_id)
        ai_configured = self._ai_config()["configured"]
        if gate in {"excluded", "expired", "duplicate", "closed"}:
            ai_status = "skipped"
        elif job and job["status"] in {"queued", "running", "failed"}:
            ai_status = str(job["status"])
        elif judgment and bool(judgment["stale"]):
            ai_status = "stale"
        elif judgment:
            ai_status = "succeeded"
        elif not ai_configured:
            ai_status = "not_configured"
        elif ai_configured:
            ai_status = "idle"
        else:
            ai_status = "not_configured"
        active_judgment = judgment
        if (active_judgment is None or active_judgment["stale"] or
                active_judgment.get("decision") == "blocked" or
                gate in {"excluded", "expired", "closed", "duplicate", "blocked"} or
                ai_status in {"stale", "failed", "skipped"}):
            active_judgment = None
        decision = self._latest_decision(project_id)
        decision_value = {
            "status": decision["status"] if decision else "new",
            "owner": decision["owner"] if decision else "",
            "next_action": decision["next_action"] if decision else "",
            "note": decision["note"] if decision else "",
            "selected_option_id": decision["selected_option_id"] if decision else None,
            "judgment_id": int(decision["judgment_id"]) if decision and decision["judgment_id"] is not None else None,
            "judgment_fingerprint": decision["judgment_fingerprint"] if decision else None,
            "updated_at": decision["created_at"] if decision else None,
        }
        selection_needs_review = bool(decision and decision["selected_option_id"] and
                                      judgment and int(decision["judgment_id"] or -1) != int(judgment["id"]))
        published = latest.get("published_at")
        deadline = latest.get("deadline_at")
        procurement = self._procurement_for_project(project_id)
        procurement_reading = self._procurement_reading_for_project(project_id)
        briefing = build_briefing(latest, procurement=procurement)
        # Keep source-derived briefing fields intact while exposing parsed
        # attachment facts beside them for detail and list clients.
        briefing["procurement"] = procurement
        equipment_values = sorted({str(x) for r in records for x in r.get("equipment", []) if x})
        if not equipment_values:
            # Collector equipment remains authoritative when present.  An
            # evidenced procurement item is a safe fallback for source rows
            # whose equipment field was empty; titles are never consulted.
            equipment_values = sorted({str(item.get("name") or "").strip()
                                       for item in procurement.get("items", [])
                                       if isinstance(item, dict) and str(item.get("name") or "").strip()})
        ai_next_actions = active_judgment.get("next_actions") if active_judgment else []
        if not isinstance(ai_next_actions, list):
            ai_next_actions = []
        opportunity_signals: list[dict[str, Any]] = []
        if active_judgment and isinstance(active_judgment.get("report"), dict):
            raw_signals = active_judgment["report"].get("opportunities", [])
            if isinstance(raw_signals, list):
                opportunity_signals = [item for item in raw_signals if isinstance(item, dict)]
        latest_run = self._latest_agent_run(project_id)
        investigation = json_value(latest_run["investigation_json"], {}) if latest_run else {}
        research_summary = None
        if isinstance(investigation, dict) and investigation.get("status"):
            research_summary = {"status": investigation["status"],
                                "source_count": len(investigation.get("sources", [])),
                                "stop_reason": investigation.get("stop_reason"),
                                "updated_at": latest_run["finished_at"] or latest_run["started_at"]}
        return {
            "id": int(project_id), "title": latest.get("title") or f"项目 {project_id}",
            "buyer": latest.get("buyer"), "equipment": equipment_values,
            "kind": latest.get("kind") or "tender", "source_id": latest.get("source_id") or "",
            "record_ids": [int(r["id"]) for r in records], "published_at": published,
            "deadline_at": deadline, "updated_at": project_row["updated_at"], "gate_status": gate,
            "gate_reasons": reasons, "ai_status": ai_status,
            "ai_decision": active_judgment["decision"] if active_judgment else None,
            "ai_summary": active_judgment["summary"] if active_judgment else None,
            "demand_summary": active_judgment["demand_summary"] if active_judgment else None,
            "ai_next_actions": ai_next_actions,
            "analysis_meta": active_judgment.get("analysis_meta") if active_judgment else None,
            "opportunity_signals": opportunity_signals,
            "research_summary": research_summary,
            "briefing": briefing,
            "procurement": procurement,
            "procurement_reading": procurement_reading,
            "procurement_fingerprint": self._procurement_fingerprint(procurement),
            "procurement_reading_fingerprint": sha256_text(json_text(procurement_reading)),
            "needs_review": gate == "review" or ai_status in {"stale", "failed", "not_configured", "idle"} or
                            (judgment is not None and judgment["decision"] == "verify") or selection_needs_review,
            "decision": decision_value,
        }

    def project_detail(self, project_id: int) -> dict[str, Any]:
        summary = self.summary(project_id)
        project_id = int(summary["id"])
        records = self._records_for_project(project_id)
        judgments = self.db.execute("SELECT * FROM judgments WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        decisions = self.db.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        versions = self.db.execute("""SELECT rv.* FROM record_versions rv JOIN project_records pr ON pr.record_id=rv.record_id
                                     WHERE pr.project_id=? ORDER BY rv.record_id, rv.version DESC""", (project_id,)).fetchall()
        related_ids: set[int] = set()
        for row in self.db.execute("""SELECT r.possible_duplicate_of,r.duplicate_of,r.related_record_id FROM records r
                                     JOIN project_records pr ON pr.record_id=r.id WHERE pr.project_id=?""", (project_id,)).fetchall():
            for key in ("possible_duplicate_of", "duplicate_of", "related_record_id"):
                if row[key] is not None:
                    pid = self._project_for_record_id(int(row[key]))
                    if pid is not None and pid != project_id:
                        related_ids.add(pid)
        for row in self.db.execute("SELECT project_id,target_project_id FROM links WHERE active=1 AND (project_id=? OR target_project_id=?)",
                                   (project_id, project_id)).fetchall():
            other = int(row["target_project_id"] if row["project_id"] == project_id else row["project_id"])
            if other != project_id:
                related_ids.add(other)
        history = [self._judgment_dict(row) for row in judgments]
        decision_history = [{
            "id": int(row["id"]), "status": row["status"], "owner": row["owner"], "next_action": row["next_action"],
            "note": row["note"], "selected_option_id": row["selected_option_id"],
            "judgment_id": int(row["judgment_id"]) if row["judgment_id"] is not None else None,
            "judgment_fingerprint": row["judgment_fingerprint"], "updated_at": row["created_at"],
        } for row in decisions]
        version_values = [dict(row) for row in versions]
        for row in version_values:
            row["id"] = int(row["id"])
        agent_run = self._agent_run_dict(self._latest_agent_run(project_id))
        return {**summary, "records": records, "judgment": history[0] if history else None,
                "judgment_history": history, "decision_history": decision_history,
                "related_projects": [self.summary(pid) for pid in sorted(related_ids)], "versions": version_values,
                "agent_run": agent_run, "research_sources": list_sources(self.db, project_id)}

    def _project_for_record_id(self, record_id: int) -> int | None:
        row = self.db.execute("SELECT project_id FROM project_records WHERE record_id=?", (record_id,)).fetchone()
        return int(row[0]) if row else None

    def list_projects(self, *, q: str = "", gate: str = "", ai_status: str = "", decision: str = "",
                      ai_decision: str = "", deadline_soon: bool = False, kind: str = "", source_id: str = "",
                      category: str = "", region: str = "", buyer: str = "", procurement_stage: str = "",
                      demand_type: str = "", followup_stage: str = "", is_following: bool = False,
                      key_time: str = "",
                      page: int = 1, page_size: int = 30, sort: str = "updated",
                      active_only: bool = False) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        projects = []
        now = datetime.now(SHANGHAI)
        for item in self._all_project_summaries():
            briefing = item.get("briefing") or {}
            equipment = " ".join(str(value) for value in item.get("equipment") or [])
            haystack = " ".join([str(item.get("title") or ""), str(item.get("buyer") or ""),
                                 equipment, str(item.get("ai_summary") or ""),
                                 str(briefing.get("scope_excerpt") or ""),
                                 str((item.get("procurement") or {}).get("scope_summary") or ""),
                                 str((item.get("procurement") or {}).get("delivery_location") or "")]).lower()
            if q and q.lower() not in haystack:
                continue
            if active_only and item["gate_status"] not in {"candidate", "review", "signal"}:
                continue
            if gate and item["gate_status"] != gate:
                continue
            if ai_status and item["ai_status"] != ai_status:
                continue
            if decision and item["decision"]["status"] != decision:
                continue
            # ai_decision is deliberately taken from the summary's current,
            # non-stale judgment field.  Gate status/candidate status cannot
            # stand in for an AI recommendation.
            if ai_decision and item.get("ai_decision") != ai_decision:
                continue
            if deadline_soon and not self._is_deadline_soon(item, now=now):
                continue
            if kind and item["kind"] != kind:
                continue
            if source_id and item["source_id"] != source_id:
                continue
            reading = item.get("procurement_reading") or {}
            if category and not any(category.lower() in str(value).lower()
                                    for value in reading.get("category", []) if value):
                continue
            if region and region.lower() not in str(reading.get("region") or "").lower():
                continue
            if buyer and buyer.lower() not in str(item.get("buyer") or reading.get("buyer") or "").lower():
                continue
            if procurement_stage and str(reading.get("stage") or "") != procurement_stage:
                continue
            if demand_type and str(reading.get("demand_type") or "unknown") != demand_type:
                continue
            if key_time:
                submission_date = self._reading_submission_date(item)
                today = now.astimezone(SHANGHAI).date()
                if key_time == "unknown":
                    if submission_date is not None:
                        continue
                elif key_time in {"today", "7d", "30d"}:
                    days = {"today": 0, "7d": 7, "30d": 30}[key_time]
                    if submission_date is None or not today <= submission_date <= today + timedelta(days=days):
                        continue
            projects.append(item)
        if sort == "deadline":
            projects.sort(key=lambda item: (item.get("deadline_at") is None, item.get("deadline_at") or "9999"))
        else:
            projects.sort(key=lambda item: (item.get("updated_at") or "", item["id"]), reverse=True)
        total = len(projects)
        start = (page - 1) * page_size
        return {"items": projects[start:start + page_size], "total": total, "page": page, "page_size": page_size}

    def dashboard(self) -> dict[str, Any]:
        # Dashboard aggregates are full-dataset counts.  The visible deadline
        # list is intentionally capped separately for the home page.
        projects = self._all_project_summaries()
        gate_counts: dict[str, int] = {}
        ai_counts: dict[str, int] = {}
        ai_decision_counts: dict[str, int] = {}
        decision_counts: dict[str, int] = {}
        now = datetime.now(SHANGHAI)
        deadline_soon = []
        for item in projects:
            gate_counts[item["gate_status"]] = gate_counts.get(item["gate_status"], 0) + 1
            ai_counts[item["ai_status"]] = ai_counts.get(item["ai_status"], 0) + 1
            decision_counts[item["decision"]["status"]] = decision_counts.get(item["decision"]["status"], 0) + 1
            ai_decision = item.get("ai_decision")
            if ai_decision:
                ai_decision_counts[str(ai_decision)] = ai_decision_counts.get(str(ai_decision), 0) + 1
            if self._is_deadline_soon(item, now=now):
                deadline_soon.append(item)
        rows = self.db.execute("SELECT count(*) AS c FROM records").fetchone()
        updated = self._source_data_updated_at()
        file_stale = self._source_age_stale()
        import_pending = self._import_pending()
        return {"total_records": int(rows["c"] if rows else 0), "total_projects": len(projects),
                "gate_counts": gate_counts, "ai_counts": ai_counts, "ai_decision_counts": ai_decision_counts,
                "decision_counts": decision_counts, "deadline_soon": deadline_soon[:30],
                "deadline_soon_count": len(deadline_soon), "data_updated_at": updated,
                "last_import_at": self._get_meta("last_import_at"), "import_error": self._get_meta("import_error"),
                "file_stale": file_stale, "import_pending": import_pending,
                "source_notes": self._source_notes(), "ai_configured": self._ai_config()["configured"]}

    def _new_import_row(self) -> int:
        with self._db_lock, self.db:
            row = self.db.execute("INSERT INTO imports(path,status,created_at) VALUES (?,'running',?) RETURNING id",
                                  (str(self.source_path), now_iso())).fetchone()
            return int(row[0])

    async def import_file(self) -> dict[str, Any]:
        import_id = self._new_import_row()
        import_signature = self._file_signature(self.source_path)
        # Capture bounded related snapshots before the atomic import.  The
        # post-import comparison below catches changes to a related project's
        # body, relation references, or project membership.
        related_context_before = self._related_context_fingerprints()
        try:
            with self._db_lock:
                result = import_records(self.db, self.source_path)
            with self._db_lock, self.db:
                related_changes = self._mark_related_context_changes(related_context_before)
                self.db.execute("UPDATE imports SET status='success',inserted=?,updated=?,unchanged=?,total=?,finished_at=? WHERE id=?",
                                (result.inserted, result.updated, result.unchanged, result.total, now_iso(), import_id))
                # This timestamp describes the collector's source update.  It
                # must not be replaced with this application's import time.
                self.db.execute("INSERT INTO meta(key,value) VALUES ('data_updated_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (self._source_data_updated_at() or now_iso(),))
                self.db.execute("INSERT INTO meta(key,value) VALUES ('last_import_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (now_iso(),))
                self.db.execute("DELETE FROM meta WHERE key='import_error'")
                # Record the signature observed before reading. If the source
                # changes while a read is in progress, the monitor will notice
                # the mismatch and retry instead of treating a partial view as
                # current.
                self._last_import_signature = import_signature
                self.db.execute("INSERT INTO meta(key,value) VALUES ('source_signature',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (json_text(list(self._last_import_signature)),))
            if self._ai_config()["configured"]:
                # A daily incremental import must not rerun an unrelated old
                # backlog merely because its policy/model result is stale.
                for project_id in sorted(set(result.affected_project_ids) | set(related_changes)):
                    if self._reading_gate_enabled():
                        self.queue_reading(project_id, schedule=False)
                    self.queue_project(project_id, schedule=False)
            return {"inserted": result.inserted, "updated": result.updated, "unchanged": result.unchanged,
                    "total": result.total, "message": "导入完成"}
        except (ImportErrorDetail, OSError) as exc:
            error = str(exc)
            with self._db_lock, self.db:
                self.db.execute("UPDATE imports SET status='failed',error=?,finished_at=? WHERE id=?",
                                (error[:500], now_iso(), import_id))
                self.db.execute("INSERT INTO meta(key,value) VALUES ('import_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (error[:500],))
            raise ServiceError(error)
        except Exception as exc:
            with self._db_lock, self.db:
                self.db.execute("UPDATE imports SET status='failed',error=?,finished_at=? WHERE id=?",
                                (f"{type(exc).__name__}: import rolled back"[:500], now_iso(), import_id))
                self.db.execute("INSERT INTO meta(key,value) VALUES ('import_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (f"{type(exc).__name__}: import rolled back",))
            raise

    def queue_project(self, project_id: int, *, force: bool = False, schedule: bool = False) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        config = self._ai_config()
        if not config["configured"]:
            if (config.get("credentials_configured") and config.get("engine") == "pi_sdk"
                    and not config.get("sdk_ready") and isinstance(self.provider, PiSDKProvider)):
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
        if self._reading_gate_enabled():
            self.queue_reading(project_id, force=False, schedule=False)
        records = self._records_for_project(project_id)
        gate, _ = self._gate(records, project_id)
        if gate in {"excluded", "expired", "duplicate", "closed"}:
            return {"job_id": None, "status": "skipped", "message": "该项目当前门槛状态不需要 AI 分析"}
        current = self._latest_judgment(project_id)
        if (current and not self._judgment_dict(current)["stale"]
                and current_analysis_meta(json_value(current["analysis_meta_json"], None))
                and current["research_policy_version"] == RESEARCH_POLICY_VERSION and not force):
            return {"job_id": None, "status": "succeeded", "message": "已有最新判断"}
        queued = self.db.execute("SELECT id,status FROM jobs WHERE project_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                                 (project_id,)).fetchone()
        if queued:
            return {"job_id": int(queued["id"]), "status": queued["status"], "message": "任务已在队列中"}
        with self._db_lock, self.db:
            row = self.db.execute("INSERT INTO jobs(project_id,status,created_at,updated_at) VALUES (?,'queued',?,?) RETURNING id",
                                  (project_id, now_iso(), now_iso())).fetchone()
            job_id = int(row[0])
        if schedule:
            self.schedule_job(job_id)
        return {"job_id": job_id, "status": "queued", "message": "已排队"}

    def schedule_job(self, job_id: int) -> None:
        # Scheduling only wakes the single durable worker. It never starts one
        # task per project, which keeps provider calls bounded at concurrency 1.
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = self._spawn(self._worker_loop())

    def queue_pending(self, *, schedule: bool = False) -> int:
        count = 0
        for row in self.db.execute("SELECT id FROM projects ORDER BY id").fetchall():
            result = self.queue_project(int(row["id"]), schedule=schedule)
            if result.get("job_id") is not None and result.get("status") == "queued":
                count += 1
        return count

    def _analysis_fingerprint(self, project: dict[str, Any], capability: dict[str, Any], config: dict[str, Any],
                              related_context: list[dict[str, Any]] | None = None,
                              research_dependencies: list[dict[str, Any]] | None = None,
                              research_configuration: str | None = None) -> str:
        return sha256_text(json_text({
            "input": input_fingerprint(project, capability, related_context),
            "gate_status": project.get("gate_status"), "gate_reasons": project.get("gate_reasons", []),
            "model": config.get("model"), "base_url": config.get("base_url"),
            "enabled": config.get("enabled"), "engine": config.get("engine"),
            "research_policy": RESEARCH_POLICY_VERSION,
            "research_configuration": research_configuration or configuration_fingerprint(self._research_sources_path),
            "research_dependencies": research_dependencies or [],
        }))

    def _agent_limits(self) -> dict[str, int]:
        return dict(DEFAULT_AGENT_LIMITS)

    def _new_agent_run(self, job_id: int, project_id: int) -> int:
        with self._db_lock, self.db:
            row = self.db.execute(
                "INSERT INTO agent_runs(job_id,project_id,engine,status,started_at,steps_json,usage_json,limits_json) "
                "VALUES (?,?,?,'running',?,?,?,?) RETURNING id",
                (job_id, project_id, "pi_sdk", now_iso(), json_text([]),
                 json_text({"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}),
                 json_text(self._agent_limits())),
            ).fetchone()
            return int(row[0])

    @staticmethod
    def _agent_public_event(event: Any) -> dict[str, Any] | None:
        labels = {
            "started": "智能体已启动", "model": "模型调用", "tool_start": "工具调用开始",
            "tool_end": "工具调用完成", "completed": "方案已通过校验",
        }
        if not isinstance(event, dict) or event.get("stage") not in labels:
            return None
        stage = str(event["stage"])
        value: dict[str, Any] = {"stage": stage, "label": labels[stage], "at": now_iso()}
        tool = event.get("tool")
        if stage in {"tool_start", "tool_end"} and isinstance(tool, str) and tool in TRUSTED_AGENT_TOOLS:
            tool = re.sub(r"[^A-Za-z0-9_.:/-]", "", tool)[:100]
            if tool:
                value["tool"] = tool
                names = {
                    "read_project_records": "读取项目资料",
                    "read_company_capability": "读取公司能力",
                    "read_related_records": "读取关联资料",
                    "fetch_project_documents": "读取采购附件",
                    "search_project_sources": "搜索项目资料",
                    "read_research_source": "读取调查来源",
                    "assess_source_relation": "核实资料关系",
                    "search_research_content": "检索资料内容",
                }
                name = names[tool]
                if stage == "tool_start":
                    value["label"] = f"{name}开始"
                elif event.get("is_error") is True or event.get("_is_error") is True:
                    value["label"] = f"{name}失败"
                else:
                    value["label"] = f"{name}完成"
        elif stage == "tool_end" and event.get("is_error") is True:
            value["label"] = "工具调用失败"
        turn = event.get("turn")
        if isinstance(turn, int) and 0 <= turn <= 1000:
            value["turn"] = turn
        return value

    def _record_agent_event(self, agent_run_id: int, event: Any) -> None:
        clean = self._agent_public_event(event)
        if clean is None:
            return
        try:
            with self._db_lock, self.db:
                row = self.db.execute("SELECT steps_json FROM agent_runs WHERE id=?", (agent_run_id,)).fetchone()
                steps = json_value(row[0], []) if row else []
                if not isinstance(steps, list):
                    steps = []
                steps.append(clean)
                self.db.execute("UPDATE agent_runs SET steps_json=? WHERE id=?", (json_text(steps[-200:]), agent_run_id))
        except sqlite3.ProgrammingError:
            # Closing the host while a provider callback is in flight should
            # not resurrect a DB connection or mask process cleanup.
            return

    def _update_agent_usage(self, agent_run_id: int, usage: dict[str, Any] | None) -> None:
        if not isinstance(usage, dict):
            return
        try:
            with self._db_lock, self.db:
                row = self.db.execute("SELECT usage_json FROM agent_runs WHERE id=?", (agent_run_id,)).fetchone()
                current = json_value(row[0], {}) if row else {}
                if not isinstance(current, dict):
                    current = {}
                for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens"):
                    value = usage.get(key)
                    if isinstance(value, int) and value >= 0:
                        current[key] = int(current.get(key, 0) or 0) + min(value, 2_000_000_000)
                self.db.execute("UPDATE agent_runs SET usage_json=? WHERE id=?", (json_text(current), agent_run_id))
        except sqlite3.ProgrammingError:
            return

    def _finish_agent_run(self, agent_run_id: int, status: str, error: BaseException | str | None = None) -> None:
        if status not in {"succeeded", "failed"}:
            status = "failed"
        safe_error = sanitize_ai_error(error if isinstance(error, BaseException) else RuntimeError(str(error))) if error else None
        try:
            with self._db_lock, self.db:
                self.db.execute("UPDATE agent_runs SET status=?,finished_at=?,error=? WHERE id=?",
                                (status, now_iso(), safe_error, agent_run_id))
        except sqlite3.ProgrammingError:
            return

    def _record_investigation(self, agent_run_id: int, session: ResearchSession, *,
                              status: str | None = None, stop_reason: str | None = None,
                              unresolved: list[str] | None = None, configuration: str | None = None) -> None:
        value = session.investigation(status=status, stop_reason=stop_reason, unresolved=unresolved)
        value["configuration_fingerprint"] = configuration or configuration_fingerprint(self._research_sources_path)
        try:
            with self._db_lock, self.db:
                self.db.execute("UPDATE agent_runs SET investigation_json=? WHERE id=?",
                                (json_text(value), agent_run_id))
        except sqlite3.ProgrammingError:
            return

    @staticmethod
    def _cited_research_dependencies(judgment: dict[str, Any], session: ResearchSession | None) -> list[dict[str, Any]]:
        if session is None:
            return []
        cited = {(evidence["source_ref"], evidence["source_version"])
                 for reason in judgment.get("reasons", []) for evidence in reason.get("evidence", [])
                 if isinstance(evidence, dict) and "source_ref" in evidence and "source_version" in evidence}
        cited.update((evidence["source_ref"], evidence["source_version"])
                     for evidence in iter_report_evidence(judgment.get("report"))
                     if "source_ref" in evidence and "source_version" in evidence)
        return sorted((dependency for dependency in session.dependencies()
                       if (dependency["source_ref"], dependency["source_version"]) in cited),
                      key=lambda dependency: (dependency["source_ref"], dependency["source_version"]))

    def _research_dependencies_changed(self, project_id: int, dependencies: list[dict[str, Any]]) -> bool:
        comparisons = current_dependencies(self.db, project_id, dependencies)
        if len(comparisons) != len(dependencies):
            return True
        return any(current.get("changed") or current.get("relation_status") != saved.get("relation_status")
                   for saved, current in zip(dependencies, comparisons))

    async def run_job(self, job_id: int) -> None:
        if job_id in self._active_jobs:
            return
        self._active_jobs.add(job_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._run_tasks.add(current_task)
        agent_run_id: int | None = None
        research_session: ResearchSession | None = None
        research_configuration_snapshot: str | None = None
        legacy_judgment_content: str | None = None
        legacy_judgment_usage: dict[str, int] = {}
        try:
            with self._db_lock, self.db:
                job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not job or job["status"] not in {"queued", "running"}:
                    return
                attempts = int(job["attempts"] or 0) + 1
                self.db.execute("UPDATE jobs SET status='running',attempts=?,started_at=?,updated_at=?,error=NULL WHERE id=?",
                                (attempts, now_iso(), now_iso(), job_id))
            project_id = int(job["project_id"])
            project = self.project_detail(project_id)
            config = self._ai_config()
            if not config["configured"]:
                if (config.get("credentials_configured") and config.get("engine") == "pi_sdk"
                        and not config.get("sdk_ready") and isinstance(self.provider, PiSDKProvider)):
                    raise AIError("Pi SDK运行时未就绪，请安装Node.js 22.19+并构建agent-runtime/dist/runner.js")
                raise AIError("AI is not configured")
            # Procurement reading is an independent, source-grounded stage.
            # The legacy injected judgment doubles opt out unless they expose
            # read/read_procurement; real Pi SDK and single-call providers
            # always pass through the stage before a judgment is generated.
            explicit_reader = callable(getattr(self.provider, "read_procurement", None)) or callable(getattr(self.provider, "read", None))
            if (not self._injected_provider) or explicit_reader or bool(getattr(self.provider, "supports_procurement_reading", False)):
                # A failed reading remains an explicit, persisted input state.
                # The judgment may still use the primary announcement as a
                # fallback, but its prompt carries the failed/partial reading
                # and unresolved evidence so it cannot present that stage as
                # successfully completed.
                try:
                    reading_result = await self.run_reading(project_id, force=False)
                except ReadingProtocolMismatch as mismatch:
                    # Older Pi runners do not echo task_kind. Their first
                    # response is the legacy judgment draft; reuse it instead
                    # of spending a second model turn before the judgment.
                    legacy_judgment_content = mismatch.content
                    legacy_judgment_usage = mismatch.usage
                else:
                    reading_status = str(reading_result.get("status") or "failed")
                    if reading_status in {"failed", "pending", "not_configured"}:
                        raise AIError("procurement reading is not ready for judgment")
                project = self.project_detail(project_id)
            gate, _ = self._gate(self._records_for_project(project_id), project_id)
            if gate in {"excluded", "expired", "duplicate", "closed"}:
                with self._db_lock, self.db:
                    self.db.execute("UPDATE jobs SET status='skipped',error=?,finished_at=?,updated_at=? WHERE id=?",
                                    (f"gate:{gate}", now_iso(), now_iso(), job_id))
                return
            capability = self._section("business")
            records = self._records_for_project(project_id)
            context, related_context = self._analysis_context(project_id, capability, records)
            # Keep a full, bounded-by-ID validation snapshot separate from the
            # SDK context. It is captured before awaiting the provider so a
            # concurrent import cannot mix a new body with the old fingerprint.
            related_records = self._analysis_related_validation_records(related_context)
            document_sources = self._document_sources_for_records(
                [int(record["id"]) for record in records if record.get("id") is not None]
            )
            research_configuration_snapshot = configuration_fingerprint(self._research_sources_path)
            fingerprint = self._analysis_fingerprint(project, capability, config, related_context,
                                                     research_configuration=research_configuration_snapshot)
            with self._db_lock, self.db:
                self.db.execute("UPDATE jobs SET input_fingerprint=?,updated_at=? WHERE id=?", (fingerprint, now_iso(), job_id))
            messages = build_messages(project, capability, related_context)
            provider: Any = self.provider
            if config.get("engine") == "single_call" and isinstance(provider, PiSDKProvider) and not self._injected_provider:
                provider = OpenAICompatibleProvider()
            uses_agent = config.get("engine") == "pi_sdk" and callable(getattr(provider, "analyze", None))
            if uses_agent:
                document_sources = []
                document_cache: dict[int, list[dict[str, Any]]] = {}
                agent_run_id = self._new_agent_run(job_id, project_id)
                research_session = ResearchSession(
                    self.db, project_id=project_id, job_id=job_id, records=records,
                    db_lock=self._db_lock, sources_path=self._research_sources_path, client=self._research_client,
                )
                self._record_investigation(agent_run_id, research_session, configuration=research_configuration_snapshot)
                fingerprint_project = copy.deepcopy(project)

                async def on_event(event: dict[str, Any]) -> None:
                    if event.get("stage") == "usage":
                        self._update_agent_usage(agent_run_id, event.get("usage"))
                    else:
                        self._record_agent_event(agent_run_id, event)

                async def tool_callback(arguments: dict[str, Any], *, tool: str = "fetch_project_documents") -> dict[str, Any]:
                    nonlocal fingerprint, document_sources
                    if str(config.get("api_key") or "") and config["api_key"] in json_text(arguments):
                        raise AIError("工具参数包含受限凭据")
                    if tool in RESEARCH_TOOLS:
                        try:
                            return await research_session.execute(tool, arguments)
                        finally:
                            self._record_investigation(agent_run_id, research_session,
                                                       configuration=research_configuration_snapshot)
                    if tool != "fetch_project_documents":
                        raise AIError("工具参数无效")
                    value = await self._agent_fetch_documents(project_id, arguments, cache=document_cache, records=records)
                    requested_record_id = arguments.get("record_id") if isinstance(arguments, dict) else None
                    target_record_ids = ({int(record["id"]) for record in records}
                                         if requested_record_id is None else {int(requested_record_id)})
                    document_sources = self._merge_job_document_reads(
                        document_sources, value.get("documents") if isinstance(value, dict) else None,
                        target_record_ids,
                    )
                    # The tool response is part of this job's trusted input.
                    # Keep the original source/capability/config snapshot and
                    # replace only the procurement component. A concurrent
                    # source import is still detected by the final comparison.
                    if isinstance(value, dict) and isinstance(value.get("procurement"), dict):
                        procurement_snapshot = self._procurement_for_project(project_id)
                        fingerprint_project["procurement"] = procurement_snapshot
                        fingerprint_project["procurement_fingerprint"] = self._procurement_fingerprint(procurement_snapshot)
                        # Attachment reads also change the source package
                        # version exposed to the shared reading contract.
                        # Keep the in-job snapshot aligned so the document
                        # fetch remains part of this job rather than looking
                        # like a concurrent change that must be requeued.
                        reading_snapshot = self._procurement_reading_for_project(project_id)
                        fingerprint_project["procurement_reading"] = reading_snapshot
                        fingerprint_project["procurement_reading_fingerprint"] = sha256_text(
                            json_text(reading_snapshot)
                        )
                    fingerprint = self._analysis_fingerprint(
                        fingerprint_project, capability, config, related_context,
                        research_configuration=research_configuration_snapshot,
                    )
                    return value

            usage_totals = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
            if legacy_judgment_content is not None:
                if agent_run_id is not None:
                    self._update_agent_usage(agent_run_id, legacy_judgment_usage)
                for key in usage_totals:
                    value = legacy_judgment_usage.get(key)
                    if isinstance(value, int) and value >= 0:
                        usage_totals[key] += value
            runtime_limits = self._agent_limits()
            format_repair_attempted = False
            for correction in range(2):
                if correction:
                    with self._db_lock, self.db:
                        self.db.execute("UPDATE jobs SET attempts=attempts+1,updated_at=? WHERE id=?",
                                        (now_iso(), job_id))
                # Request failures (including credentials, timeouts and rate limits)
                # are not output errors and must not trigger a correction request.
                if legacy_judgment_content is not None and correction == 0:
                    content = legacy_judgment_content
                    usage = {}
                elif uses_agent:
                    runtime_limits = self._agent_limits()
                    if correction:
                        runtime_limits = {**runtime_limits, "max_turns": min(runtime_limits["max_turns"], 3)}
                    analyze_kwargs = {
                        "base_url": config["base_url"], "model": config["model"], "api_key": config["api_key"],
                        "messages": messages, "context": context, "limits": runtime_limits, "on_event": on_event,
                    }
                    try:
                        parameters = inspect.signature(provider.analyze).parameters  # type: ignore[attr-defined]
                    except (TypeError, ValueError):
                        parameters = {}
                    if "tool_callback" in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD
                                                            for parameter in parameters.values()):
                        analyze_kwargs["tool_callback"] = tool_callback
                    result = await provider.analyze(**analyze_kwargs)  # type: ignore[attr-defined]
                    if isinstance(result, PiSDKResult):
                        content = result.content
                        usage = result.usage
                    elif isinstance(result, dict) and isinstance(result.get("content"), str):
                        content = result["content"]
                        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                    else:
                        raise AIError("Pi SDK结果协议无效")
                    if agent_run_id is not None:
                        self._update_agent_usage(agent_run_id, usage)
                    for key in usage_totals:
                        if isinstance(usage.get(key), int) and usage[key] >= 0:
                            usage_totals[key] += int(usage[key])
                else:
                    response = await provider.complete(base_url=config["base_url"], model=config["model"],
                                                            api_key=config["api_key"], messages=messages)
                    content = extract_content(response)
                try:
                    judgment = validate_judgment(content, project, config["model"], fingerprint,
                                                 related_records=related_records,
                                                 forbidden_values=[str(config.get("api_key") or "")],
                                                 document_sources=document_sources,
                                                 company_material=capability,
                                                 research_sources=research_session.evidence_sources() if research_session else [],
                                                 require_report=isinstance(provider, (PiSDKProvider, OpenAICompatibleProvider)))
                    break
                except AIError as exc:
                    # A malformed final report from the official DeepSeek
                    # provider gets one isolated YAML serialization pass. This
                    # avoids replaying the agent investigation and cannot
                    # execute any business tool; the decoded JSON is still
                    # checked by validate_judgment below.
                    if (uses_agent and isinstance(provider, PiSDKProvider) and not self._injected_provider
                            and exc.code == "MODEL_FORMAT_ERROR"
                            and not format_repair_attempted
                            and deepseek_formatter_endpoint(str(config.get("base_url") or "")) is not None):
                        format_repair_attempted = True
                        with self._db_lock, self.db:
                            self.db.execute("UPDATE jobs SET attempts=attempts+1,updated_at=? WHERE id=?",
                                            (now_iso(), job_id))
                        if agent_run_id is not None:
                            self._record_agent_event(agent_run_id, {"stage": "model"})
                        available_evidence = {
                            "documents": [{key: document.get(key) for key in
                                           ("id", "record_id", "name", "read_fragments")}
                                          for document in document_sources],
                            "research_sources": research_session.evidence_sources() if research_session else [],
                        }
                        repair_messages = correction_messages(
                            messages, exc, previous_output=content,
                            available_evidence=available_evidence,
                        )
                        finalizer_usage_recorded = False

                        def record_finalizer_usage(value: dict[str, int]) -> None:
                            nonlocal finalizer_usage_recorded
                            finalizer_usage_recorded = True
                            if agent_run_id is not None:
                                self._update_agent_usage(agent_run_id, value)

                        finalizer = await strict_format_report(
                            base_url=str(config.get("base_url") or ""),
                            model=str(config.get("model") or ""),
                            api_key=str(config.get("api_key") or ""),
                            messages=repair_messages,
                            previous_output="",
                            available_evidence={},
                            prepared_messages=True,
                            max_output_tokens=int(runtime_limits.get("max_output_tokens", 16_384)),
                            on_usage=record_finalizer_usage,
                        )
                        if agent_run_id is not None and not finalizer_usage_recorded:
                            self._update_agent_usage(agent_run_id, finalizer.usage)
                        content = finalizer.content
                        try:
                            judgment = validate_judgment(
                                content, project, config["model"], fingerprint,
                                related_records=related_records,
                                forbidden_values=[str(config.get("api_key") or "")],
                                document_sources=document_sources,
                                company_material=capability,
                                research_sources=research_session.evidence_sources() if research_session else [],
                                require_report=True,
                            )
                        except AIError as finalizer_error:
                            # A successful format conversion may still fail
                            # trusted evidence or report semantics.  On the
                            # first pass, feed that validation error into the
                            # existing single semantic correction.  Formatter
                            # failures and second-pass failures remain terminal.
                            if correction or finalizer_error.code == "MODEL_FORMAT_ERROR":
                                raise
                            exc = finalizer_error
                        else:
                            break
                    # A semantic correction still gets only its one agent
                    # retry.  The format-only finalizer above is the sole
                    # exception, so a malformed second response can be
                    # repaired without starting another investigation.
                    if correction:
                        raise
                    if agent_run_id is not None:
                        # Retain bounded correction progress in the public
                        # activity feed.  The raw validation error can contain
                        # model text, so only use the trusted stage label.
                        self._record_agent_event(agent_run_id, {"stage": "model"})
                    messages = correction_messages(messages, exc, previous_output=content,
                        available_evidence={
                            "documents": [{key: document.get(key) for key in
                                           ("id", "record_id", "name", "read_fragments")}
                                          for document in document_sources],
                            "research_sources": research_session.evidence_sources() if research_session else [],
                        })
            judgment["created_at"] = now_iso()
            dependencies = self._cited_research_dependencies(judgment, research_session)
            if uses_agent:
                # Only cited versions affect the final report identity. Search
                # candidates and unused reads cannot invalidate the job itself.
                fingerprint = self._analysis_fingerprint(fingerprint_project, capability, config,
                                                         related_context, dependencies, research_configuration_snapshot)
            judgment["input_fingerprint"] = fingerprint
            if agent_run_id is not None and research_session is not None:
                investigation = research_session.investigation()
                has_events = bool(investigation.get("events"))
                status = "limited" if investigation.get("status") == "limited" else (
                    "completed" if has_events else "not_performed")
                self._record_investigation(agent_run_id, research_session, status=status,
                                           stop_reason=investigation.get("stop_reason") or (
                                               "补查已结束，未解决事项见下方" if has_events else "本轮依据已有资料分析，未执行主动补查"),
                                           unresolved=judgment["unknowns"], configuration=research_configuration_snapshot)
            # A file import or settings update may have completed while the
            # provider was awaiting the network. Preserve this result only as a
            # stale audit entry, then enqueue a fresh generation from current
            # inputs. It must never become the current recommendation.
            current_project = self.project_detail(project_id)
            current_config = self._ai_config()
            current_capability = self._section("business")
            current_context, current_related_context = self._analysis_context(project_id, current_capability)
            stale_generation = fingerprint != self._analysis_fingerprint(
                current_project, current_capability, current_config, current_related_context,
                dependencies) or self._research_dependencies_changed(project_id, dependencies)
            with self._db_lock, self.db:
                self.db.execute("""INSERT INTO judgments(project_id,created_at,model,input_fingerprint,stale,decision,summary,demand_summary,
                                  reasons_json,unknowns_json,next_actions_json,options_json,analysis_meta_json,report_json,agent_run_id,
                                  research_dependencies_json,research_policy_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (project_id, judgment["created_at"], judgment["model"], judgment["input_fingerprint"], int(stale_generation), judgment["decision"],
                                 judgment["summary"], judgment["demand_summary"], json_text(judgment["reasons"]), json_text(judgment["unknowns"]),
                                 json_text(judgment["next_actions"]), json_text(judgment["options"]),
                                 json_text(judgment["analysis_meta"]),
                                 json_text(judgment["report"]) if isinstance(judgment.get("report"), dict) else None,
                                 agent_run_id, json_text(dependencies), RESEARCH_POLICY_VERSION))
                self.db.execute("UPDATE jobs SET status='succeeded',finished_at=?,updated_at=?,error=NULL WHERE id=?",
                                (now_iso(), now_iso(), job_id))
            if agent_run_id is not None:
                # Only this trusted event is persisted as completed.  Runner
                # completed notifications are ignored before validation.
                self._record_agent_event(agent_run_id, {"stage": "completed"})
                self._finish_agent_run(agent_run_id, "succeeded")
            if stale_generation and current_config["configured"]:
                self.queue_project(project_id, force=True, schedule=False)
        except asyncio.CancelledError:
            if agent_run_id is not None:
                if research_session is not None:
                    self._record_investigation(agent_run_id, research_session, status="limited", stop_reason="任务已取消",
                                               configuration=research_configuration_snapshot)
                self._finish_agent_run(agent_run_id, "failed", "任务已取消")
            if not self._closed:
                try:
                    with self._db_lock, self.db:
                        self.db.execute("UPDATE jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                                        ("任务已取消", now_iso(), now_iso(), job_id))
                except sqlite3.ProgrammingError:
                    pass
            raise
        except Exception as exc:
            # Do not retain provider response or request data in the database.
            error = sanitize_ai_error(exc, secrets=[str(config.get("api_key") or "")] if isinstance(locals().get("config"), dict) else [])
            if agent_run_id is not None:
                if research_session is not None:
                    self._record_investigation(agent_run_id, research_session, status="failed", stop_reason=error,
                                               configuration=research_configuration_snapshot)
                self._finish_agent_run(agent_run_id, "failed", error)
            try:
                with self._db_lock, self.db:
                    self.db.execute("UPDATE jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                                    (error[:500], now_iso(), now_iso(), job_id))
            except sqlite3.ProgrammingError:
                pass
        finally:
            try:
                if research_session is not None:
                    await research_session.close()
            finally:
                self._active_jobs.discard(job_id)
                if current_task is not None:
                    self._run_tasks.discard(current_task)

    def retry_failed(self) -> int:
        if not self._ai_config()["configured"]:
            return 0
        with self._db_lock, self.db:
            rows = self.db.execute("SELECT id FROM jobs WHERE status='failed' ORDER BY id").fetchall()
            for row in rows:
                self.db.execute("UPDATE jobs SET status='queued',error=NULL,updated_at=? WHERE id=?", (now_iso(), int(row["id"])))
        for row in rows:
            self.schedule_job(int(row["id"]))
        return len(rows)

    def save_decision(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        status = payload.get("status")
        if status not in {"new", "following", "verify", "closed"}:
            raise ServiceError("invalid decision status")
        selected = payload.get("selected_option_id")
        if selected is not None and not isinstance(selected, str):
            raise ServiceError("selected_option_id must be a string or null")
        gate, _ = self._gate(self._records_for_project(project_id), project_id)
        judgment = self._latest_judgment(project_id)
        judgment_is_current = bool(judgment and not self._judgment_dict(judgment)["stale"] and
                                   current_analysis_meta(json_value(judgment["analysis_meta_json"], None)))
        options = json_value(judgment["options_json"], []) if judgment_is_current else []
        if selected is not None:
            if gate in {"expired", "closed", "duplicate", "excluded"}:
                raise ConflictError("不能选择已过期或不可推进项目的方案")
            if not judgment_is_current:
                raise ConflictError("当前判断已过期，请先重新分析")
            requested_judgment_id = payload.get("judgment_id")
            try:
                requested_judgment_id = int(requested_judgment_id) if requested_judgment_id is not None else None
            except (TypeError, ValueError):
                requested_judgment_id = None
            if requested_judgment_id is None or requested_judgment_id != int(judgment["id"]):
                raise ConflictError("方案来自旧判断，请刷新后重新确认")
            if selected not in {str(option.get("id")) for option in options if isinstance(option, dict)}:
                raise ConflictError("方案不存在或已失效")
        fields = {
            "owner": str(payload.get("owner") or ""), "next_action": str(payload.get("next_action") or ""),
            "note": str(payload.get("note") or ""), "selected_option_id": selected,
        }
        judgment_id = int(judgment["id"]) if selected is not None and judgment else None
        judgment_fingerprint = judgment["input_fingerprint"] if selected is not None and judgment else None
        with self._db_lock, self.db:
            self.db.execute("""INSERT INTO decisions(project_id,status,owner,next_action,note,selected_option_id,judgment_id,
                            judgment_fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                            (project_id, status, fields["owner"], fields["next_action"], fields["note"], selected,
                             judgment_id, judgment_fingerprint, now_iso()))
            self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
        return self.summary(project_id)

    def gate_action(self, project_id: int, action: str, reason: str) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        if action not in {"restore", "clear"}:
            raise ServiceError("invalid gate action")
        reason = str(reason or "").strip()
        if not reason:
            raise ServiceError("reason is required")
        with self._db_lock, self.db:
            if action == "restore":
                self.db.execute("INSERT INTO project_gate(project_id,override_action,reason,updated_at) VALUES (?, 'restore', ?, ?) ON CONFLICT(project_id) DO UPDATE SET override_action='restore',reason=excluded.reason,updated_at=excluded.updated_at",
                                (project_id, reason, now_iso()))
            else:
                self.db.execute("DELETE FROM project_gate WHERE project_id=?", (project_id,))
            self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
        if action == "restore" and self._ai_config()["configured"]:
            self.queue_project(project_id, schedule=True)
        return self.summary(project_id)

    def link_projects(self, project_id: int, target_project_id: int, relation: str, note: str) -> dict[str, Any]:
        project_id = int(self._project_row(project_id)["id"])
        target_project_id = int(self._project_row(target_project_id)["id"])
        if project_id == target_project_id:
            raise ConflictError("不能关联项目自身")
        if relation not in {"same_project", "duplicate", "dismiss"}:
            raise ServiceError("invalid relation")
        with self._db_lock, self.db:
            related_context_before = self._related_context_fingerprints((project_id, target_project_id))
            self.db.execute("INSERT INTO links(project_id,target_project_id,relation,note,created_at) VALUES (?,?,?,?,?)",
                            (project_id, target_project_id, relation, str(note or ""), now_iso()))
            self.db.execute("UPDATE projects SET updated_at=? WHERE id IN (?,?)", (now_iso(), project_id, target_project_id))
            self._mark_related_context_changes(related_context_before)
        return {"ok": True, "message": "人工关联已记录，双方源记录和决定均予保留"}

    def status(self) -> dict[str, Any]:
        imports = [dict(row) for row in self.db.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()]
        jobs = []
        for row in self.db.execute("SELECT id,project_id,status,attempts,error,created_at,updated_at FROM jobs ORDER BY id DESC LIMIT 100").fetchall():
            jobs.append(dict(row))
        counts = {str(row["status"]): int(row["count"]) for row in self.db.execute("SELECT status,count(*) AS count FROM jobs GROUP BY status").fetchall()}
        reading_jobs = [dict(row) for row in self.db.execute(
            "SELECT id,project_id,status,attempts,error,created_at,updated_at "
            "FROM procurement_reading_jobs ORDER BY id DESC LIMIT 100"
        ).fetchall()]
        reading_job_counts = {str(row["status"]): int(row["count"]) for row in self.db.execute(
            "SELECT status,count(*) AS count FROM procurement_reading_jobs GROUP BY status"
        ).fetchall()}
        return {"data_updated_at": self._source_data_updated_at(), "last_import_at": self._get_meta("last_import_at"),
                "import_error": self._get_meta("import_error"), "file_stale": self._source_age_stale(),
                "import_pending": self._import_pending(), "source_notes": self._source_notes(include_path=True),
                "imports": imports, "jobs": jobs, "job_counts": counts,
                "reading_jobs": reading_jobs, "reading_job_counts": reading_job_counts,
                "ai_configured": self._ai_config()["configured"]}

    def archive_path(self, record_id: int) -> Path:
        row = self.db.execute("SELECT archive_path FROM records WHERE id=?", (record_id,)).fetchone()
        if not row or not row["archive_path"]:
            raise NotFoundError("archive not found")
        try:
            path = safe_join(self.archive_root, str(row["archive_path"]))
        except ValueError as exc:
            raise ConflictError("archive path is outside allowed directory") from exc
        if not path.is_file():
            raise NotFoundError("archive not found")
        return path
