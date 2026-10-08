from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .ai import AIError, AIProvider, OpenAICompatibleProvider, build_messages, extract_content, input_fingerprint, validate_judgment
from .config import DEFAULT_SETTINGS, env_archive_root, env_source_path
from .db import connect
from .ingest import ImportErrorDetail, ImportResult, import_records
from .rules import business_sort_key, gate_for_project
from .utils import SHANGHAI, json_text, json_value, now_iso, parse_timestamp, safe_join, sha256_text


class ServiceError(RuntimeError):
    pass


class NotFoundError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass


class ScreeningService:
    def __init__(self, *, db_path: Path | None = None, source_path: Path | None = None,
                 archive_root: Path | None = None, provider: AIProvider | None = None) -> None:
        from .config import database_path

        self.db_path = Path(db_path or database_path())
        self.source_path = Path(source_path or env_source_path())
        self.archive_root = Path(archive_root or env_archive_root(self.source_path))
        self.db = connect(self.db_path)
        self.provider: AIProvider = provider or OpenAICompatibleProvider()
        self._db_lock = threading.RLock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
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
        self._closed = True
        for task in list(self._background_tasks):
            task.cancel()
        with self._db_lock:
            self.db.close()

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

    def start_background(self) -> None:
        if self._closed:
            return
        with self._db_lock, self.db:
            # A process crash can leave a task marked running. It is safe to
            # return it to the durable queue on the next startup.
            self.db.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (now_iso(),))
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

    def _ai_config(self) -> dict[str, Any]:
        ai = self._section("ai")
        base_url = str(ai.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        model = str(ai.get("model") or os.getenv("OPENAI_MODEL", ""))
        enabled = bool(ai.get("enabled", True))
        api_key = self._stored_api_key()
        return {"base_url": base_url, "model": model, "enabled": enabled,
                "api_key": api_key, "configured": enabled and bool(base_url and model and api_key)}

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
        version = int(self._get_meta("settings_version") or 1)
        return {"business": business, "ai": ai, "import": import_settings, "version": version}

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
                    incoming = {k: v for k, v in incoming.items() if k not in {"api_key", "clear_api_key", "has_api_key"}}
                current.update(incoming)
                self.db.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                                (section, json_text(current), now_iso()))
                if section == "business" and current != previous:
                    changed_business = True
                if section == "ai" and any(current.get(key) != previous.get(key) for key in ("base_url", "model", "enabled")):
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
        result.pop("source_payload_json", None)
        return result

    def _records_for_project(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute("""SELECT r.* FROM records r JOIN project_records pr ON pr.record_id=r.id
                                WHERE pr.project_id=?""", (project_id,)).fetchall()
        records = [self._record_dict(row) for row in rows]
        return sorted(records, key=business_sort_key, reverse=True)

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

    def _gate(self, records: list[dict[str, Any]], project_id: int) -> tuple[str, list[str]]:
        override_row = self.db.execute("SELECT * FROM project_gate WHERE project_id=?", (project_id,)).fetchone()
        override = dict(override_row) if override_row else None
        return gate_for_project(records, self.get_settings(), now=datetime.now(SHANGHAI), override=override)

    def _judgment_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]), "created_at": row["created_at"], "model": row["model"],
            "input_fingerprint": row["input_fingerprint"], "stale": bool(row["stale"]),
            "decision": row["decision"], "summary": row["summary"], "demand_summary": row["demand_summary"],
            "reasons": json_value(row["reasons_json"], []), "unknowns": json_value(row["unknowns_json"], []),
            "next_actions": json_value(row["next_actions_json"], []), "options": json_value(row["options_json"], []),
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
        return {
            "id": int(project_id), "title": latest.get("title") or f"项目 {project_id}",
            "buyer": latest.get("buyer"), "equipment": sorted({str(x) for r in records for x in r.get("equipment", []) if x}),
            "kind": latest.get("kind") or "tender", "source_id": latest.get("source_id") or "",
            "record_ids": [int(r["id"]) for r in records], "published_at": published,
            "deadline_at": deadline, "updated_at": project_row["updated_at"], "gate_status": gate,
            "gate_reasons": reasons, "ai_status": ai_status,
            "ai_decision": judgment["decision"] if judgment and not judgment["stale"] and
                           gate not in {"excluded", "expired", "closed", "duplicate"} else None,
            "ai_summary": judgment["summary"] if judgment and not judgment["stale"] and
                          gate not in {"excluded", "expired", "closed", "duplicate"} else None,
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
        return {**summary, "records": records, "judgment": history[0] if history else None,
                "judgment_history": history, "decision_history": decision_history,
                "related_projects": [self.summary(pid) for pid in sorted(related_ids)], "versions": version_values}

    def _project_for_record_id(self, record_id: int) -> int | None:
        row = self.db.execute("SELECT project_id FROM project_records WHERE record_id=?", (record_id,)).fetchone()
        return int(row[0]) if row else None

    def list_projects(self, *, q: str = "", gate: str = "", ai_status: str = "", decision: str = "",
                      ai_decision: str = "", deadline_soon: bool = False, kind: str = "", source_id: str = "",
                      page: int = 1, page_size: int = 30, sort: str = "updated") -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        projects = []
        now = datetime.now(SHANGHAI)
        for item in self._all_project_summaries():
            haystack = " ".join([str(item.get("title") or ""), str(item.get("buyer") or ""), str(item.get("ai_summary") or "")]).lower()
            if q and q.lower() not in haystack:
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
        try:
            with self._db_lock:
                result = import_records(self.db, self.source_path)
            with self._db_lock, self.db:
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
                self.queue_pending(schedule=False)
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
            return {"job_id": None, "status": "not_configured", "message": "AI 未配置，已保留项目"}
        records = self._records_for_project(project_id)
        gate, _ = self._gate(records, project_id)
        if gate in {"excluded", "expired", "duplicate", "closed"}:
            return {"job_id": None, "status": "skipped", "message": "该项目当前门槛状态不需要 AI 分析"}
        current = self._latest_judgment(project_id)
        if current and not bool(current["stale"]) and not force:
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

    def _analysis_fingerprint(self, project: dict[str, Any], capability: dict[str, Any], config: dict[str, Any]) -> str:
        return sha256_text(json_text({
            "input": input_fingerprint(project, capability),
            "gate_status": project.get("gate_status"), "gate_reasons": project.get("gate_reasons", []),
            "model": config.get("model"), "base_url": config.get("base_url"),
            "enabled": config.get("enabled"),
        }))

    async def run_job(self, job_id: int) -> None:
        if job_id in self._active_jobs:
            return
        self._active_jobs.add(job_id)
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
                raise AIError("AI is not configured")
            gate, _ = self._gate(self._records_for_project(project_id), project_id)
            if gate in {"excluded", "expired", "duplicate", "closed"}:
                with self._db_lock, self.db:
                    self.db.execute("UPDATE jobs SET status='skipped',error=?,finished_at=?,updated_at=? WHERE id=?",
                                    (f"gate:{gate}", now_iso(), now_iso(), job_id))
                return
            capability = self._section("business")
            fingerprint = self._analysis_fingerprint(project, capability, config)
            with self._db_lock, self.db:
                self.db.execute("UPDATE jobs SET input_fingerprint=?,updated_at=? WHERE id=?", (fingerprint, now_iso(), job_id))
            response = await self.provider.complete(base_url=config["base_url"], model=config["model"],
                                                    api_key=config["api_key"], messages=build_messages(project, capability))
            payload = json.loads(extract_content(response))
            judgment = validate_judgment(payload, project, config["model"], fingerprint)
            capability_has_content = any(str(capability.get(key) or "").strip()
                                         for key in ("company_name", "scope_description", "supported_products",
                                                     "capabilities", "solution_examples", "region_preferences"))
            if not capability_has_content:
                # A configured model may still summarize the demand, but an
                # empty capability card cannot support a positive fit claim or
                # a fabricated supply option.
                judgment["decision"] = "verify"
                judgment["options"] = []
                if "公司能力卡为空，需先补充能力资料" not in judgment["unknowns"]:
                    judgment["unknowns"].append("公司能力卡为空，需先补充能力资料")
            judgment["created_at"] = now_iso()
            # A file import or settings update may have completed while the
            # provider was awaiting the network. Preserve this result only as a
            # stale audit entry, then enqueue a fresh generation from current
            # inputs. It must never become the current recommendation.
            current_project = self.project_detail(project_id)
            current_config = self._ai_config()
            current_capability = self._section("business")
            stale_generation = fingerprint != self._analysis_fingerprint(current_project, current_capability, current_config)
            with self._db_lock, self.db:
                self.db.execute("""INSERT INTO judgments(project_id,created_at,model,input_fingerprint,stale,decision,summary,demand_summary,
                                  reasons_json,unknowns_json,next_actions_json,options_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (project_id, judgment["created_at"], judgment["model"], judgment["input_fingerprint"], int(stale_generation), judgment["decision"],
                                 judgment["summary"], judgment["demand_summary"], json_text(judgment["reasons"]), json_text(judgment["unknowns"]),
                                 json_text(judgment["next_actions"]), json_text(judgment["options"])))
                self.db.execute("UPDATE jobs SET status='succeeded',finished_at=?,updated_at=?,error=NULL WHERE id=?",
                                (now_iso(), now_iso(), job_id))
            if stale_generation and current_config["configured"]:
                self.queue_project(project_id, force=True, schedule=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not retain provider response or request data in the database.
            error = str(exc)
            if "api_key" in error.lower() or "authorization" in error.lower():
                error = "AI request failed"
            with self._db_lock, self.db:
                self.db.execute("UPDATE jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                                (error[:500], now_iso(), now_iso(), job_id))
        finally:
            self._active_jobs.discard(job_id)

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
        options = json_value(judgment["options_json"], []) if judgment and not bool(judgment["stale"]) else []
        if selected is not None:
            if gate in {"expired", "closed", "duplicate", "excluded"}:
                raise ConflictError("不能选择已过期或不可推进项目的方案")
            if not judgment or bool(judgment["stale"]):
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
            self.db.execute("INSERT INTO links(project_id,target_project_id,relation,note,created_at) VALUES (?,?,?,?,?)",
                            (project_id, target_project_id, relation, str(note or ""), now_iso()))
            self.db.execute("UPDATE projects SET updated_at=? WHERE id IN (?,?)", (now_iso(), project_id, target_project_id))
        return {"ok": True, "message": "人工关联已记录，双方源记录和决定均予保留"}

    def status(self) -> dict[str, Any]:
        imports = [dict(row) for row in self.db.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()]
        jobs = []
        for row in self.db.execute("SELECT id,project_id,status,attempts,error,created_at,updated_at FROM jobs ORDER BY id DESC LIMIT 100").fetchall():
            jobs.append(dict(row))
        counts = {str(row["status"]): int(row["count"]) for row in self.db.execute("SELECT status,count(*) AS count FROM jobs GROUP BY status").fetchall()}
        return {"data_updated_at": self._source_data_updated_at(), "last_import_at": self._get_meta("last_import_at"),
                "import_error": self._get_meta("import_error"), "file_stale": self._source_age_stale(),
                "import_pending": self._import_pending(), "source_notes": self._source_notes(include_path=True),
                "imports": imports, "jobs": jobs, "job_counts": counts, "ai_configured": self._ai_config()["configured"]}

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
