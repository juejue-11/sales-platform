"""Stable, bounded JSON control interface for the platform backend.

The platform talks to this module as a short-lived child process.  It never
imports collector pipeline code and the status path opens SQLite read-only,
which keeps a UI status request from creating or modifying the collector
database.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import quote

from . import scheduler
from .locking import BusyError, is_running, locked


MAX_REQUEST_BYTES = 1024 * 1024
EXIT_BUSY = 3
EXIT_INVALID = 2
EXIT_UNAVAILABLE = 1


class ControlError(RuntimeError):
    code = "unavailable"
    exit_code = EXIT_UNAVAILABLE


class InvalidRequest(ControlError):
    code = "invalid"
    exit_code = EXIT_INVALID


class Unavailable(ControlError):
    code = "unavailable"
    exit_code = EXIT_UNAVAILABLE


class Busy(ControlError):
    code = "busy"
    exit_code = EXIT_BUSY


def _load_config(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Unavailable(f"unable to read config: {exc}") from exc
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"invalid config JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidRequest("config must be a JSON object")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise InvalidRequest("config.sources must be a list")
    ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise InvalidRequest("each source must be an object")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id:
            raise InvalidRequest("source.id must be a non-empty string")
        if source_id in ids:
            raise InvalidRequest(f"duplicate source id: {source_id}")
        ids.add(source_id)
        if "enabled" in source and not isinstance(source["enabled"], bool):
            raise InvalidRequest(f"source {source_id} enabled must be boolean")
    return payload


def _read_only_connect(path: Path) -> sqlite3.Connection:
    # ``mode=ro`` is essential here: sqlite3.connect(path) creates a database
    # as a side effect of an otherwise harmless status request.
    resolved = path.resolve().as_posix()
    uri_path = quote(resolved, safe="/:" )
    uri = f"file:{uri_path}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=0.25)
    db.row_factory = sqlite3.Row
    return db


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _json_object(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_statuses(db: Optional[sqlite3.Connection], source_ids: set[str]) -> tuple[dict[str, sqlite3.Row], dict[str, sqlite3.Row]]:
    db_sources: dict[str, sqlite3.Row] = {}
    latest_runs: dict[str, sqlite3.Row] = {}
    if db is None:
        return db_sources, latest_runs
    if _table_exists(db, "sources"):
        try:
            for row in db.execute("SELECT * FROM sources"):
                if row["id"] in source_ids:
                    db_sources[row["id"]] = row
        except sqlite3.Error:
            # A database from an older interrupted install is not allowed to
            # make the config list disappear; status falls back to config.
            pass
    if _table_exists(db, "source_runs"):
        try:
            if not source_ids:
                return db_sources, latest_runs
            placeholders = ",".join("?" for _ in source_ids)
            rows = db.execute(
                f"""SELECT sr.* FROM source_runs sr
                    JOIN (SELECT source_id, MAX(id) AS id FROM source_runs
                          WHERE source_id IN ({placeholders}) GROUP BY source_id) latest
                      ON latest.id=sr.id
                    ORDER BY sr.id DESC""",
                tuple(source_ids),
            ).fetchall()
            for row in rows:
                if row["source_id"] in source_ids:
                    latest_runs[row["source_id"]] = row
        except sqlite3.Error:
            pass
    return db_sources, latest_runs


def _status(config_path: Path, data_root: Path) -> dict[str, Any]:
    config = _load_config(config_path)
    running = is_running(data_root)
    db: Optional[sqlite3.Connection] = None
    db_path = data_root / "collector.sqlite3"
    if db_path.exists():
        try:
            db = _read_only_connect(db_path)
        except sqlite3.Error as exc:
            raise Unavailable(f"unable to read collector database: {exc}") from exc
    try:
        source_ids = {source["id"] for source in config["sources"]}
        active_run_id: Optional[int] = None
        run_rows: list[sqlite3.Row] = []
        if db is not None and _table_exists(db, "runs"):
            try:
                active = db.execute(
                    "SELECT id FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                active_run_id = active["id"] if active is not None else None
                run_rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 20").fetchall()
            except sqlite3.Error:
                pass
        db_sources, latest_runs = _source_statuses(db, source_ids)
        source_items: list[dict[str, Any]] = []
        for source in config["sources"]:
            source_id = source["id"]
            runtime = db_sources.get(source_id)
            latest = latest_runs.get(source_id)
            last_run = None
            if latest is not None:
                run_status = latest["status"]
                run_error = latest["error"]
                if run_status == "running" and (
                    not running or active_run_id is None or latest["run_id"] != active_run_id
                ):
                    # Do not silently turn a stale running row into success.
                    run_status = "interrupted"
                    run_error = (run_error + "; " if run_error else "") + \
                        "采集锁未持有或已有更新运行，上一轮采集已中断"
                last_run = {
                    "status": run_status,
                    "started_at": latest["started_at"],
                    "finished_at": latest["finished_at"],
                    "found_count": latest["found_count"],
                    "fetched_count": latest["fetched_count"],
                    "error": run_error,
                }
            source_items.append({
                "id": source_id,
                "name": source.get("name", source_id),
                "category": source.get("category", ""),
                "enabled": source.get("enabled", False),
                "access_status": (
                    source.get("access_status")
                    if source.get("access_status") is not None
                    else (runtime["access_status"] if runtime is not None else "unverified")
                ),
                "access_note": (
                    source.get("access_note")
                    if source.get("access_note") is not None
                    else (runtime["access_note"] if runtime is not None else "")
                ),
                "entry_url": source.get("entry_url"),
                "last_success_at": (
                    runtime["last_success_at"] if runtime is not None
                    else source.get("last_success_at")
                ),
                "last_failure_at": (
                    runtime["last_failure_at"] if runtime is not None
                    else source.get("last_failure_at")
                ),
                "last_error": (
                    runtime["last_error"] if runtime is not None
                    else source.get("last_error")
                ),
                "last_run": last_run,
            })

        runs: list[dict[str, Any]] = []
        for row in run_rows:
            run_status = row["status"]
            stats = _json_object(row["stats_json"])
            if run_status == "running" and (
                not running or active_run_id is None or row["id"] != active_run_id
            ):
                run_status = "interrupted"
            runs.append({
                "id": row["id"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "status": run_status,
                "stats": stats,
            })
        schedule = scheduler.get_status().as_dict()
        return {
            "available": True,
            "message": "采集正在运行" if running else "就绪",
            "running": running,
            "sources": source_items,
            "runs": runs,
            "schedule": schedule,
        }
    finally:
        if db is not None:
            db.close()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _set_source(config_path: Path, data_root: Path, request: Mapping[str, Any]) -> dict[str, str]:
    source_id = request.get("source_id")
    enabled = request.get("enabled")
    if not isinstance(source_id, str) or not source_id:
        raise InvalidRequest("source_id must be a non-empty string")
    if not isinstance(enabled, bool):
        raise InvalidRequest("enabled must be boolean")
    try:
        with locked(data_root):
            config = _load_config(config_path)
            for source in config["sources"]:
                if source["id"] == source_id:
                    source["enabled"] = enabled
                    _atomic_write_json(config_path, config)
                    return {"message": f"来源 {source_id} 已{'启用' if enabled else '停用'}"}
            raise InvalidRequest(f"unknown source: {source_id}")
    except BusyError as exc:
        raise Busy(str(exc)) from exc


def _set_schedule(data_root: Path, request: Mapping[str, Any]) -> dict[str, str]:
    enabled = request.get("enabled")
    at = request.get("at")
    if not isinstance(enabled, bool):
        raise InvalidRequest("enabled must be boolean")
    try:
        scheduler.validate_at(at)
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc
    try:
        with locked(data_root):
            try:
                return {"message": scheduler.set_schedule(enabled, at)}
            except scheduler.SchedulerError as exc:
                raise Unavailable(str(exc)) from exc
    except BusyError as exc:
        raise Busy(str(exc)) from exc


def handle(request: Mapping[str, Any], *, config: Path, data: Path) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise InvalidRequest("request must be a JSON object")
    action = request.get("action")
    if action == "status":
        return _status(config, data)
    if action == "source":
        return _set_source(config, data, request)
    if action == "schedule":
        return _set_schedule(data, request)
    raise InvalidRequest("action must be one of status, source, schedule")


def _read_request() -> Mapping[str, Any]:
    raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
    if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise InvalidRequest("request is too large")
    if not raw.strip():
        raise InvalidRequest("request is empty")
    try:
        request = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"invalid request JSON: {exc}") from exc
    if not isinstance(request, Mapping):
        raise InvalidRequest("request must be a JSON object")
    return request


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="collector control protocol")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        response = handle(_read_request(), config=args.config, data=args.data)
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        return 0
    except ControlError as exc:
        print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False,
                         separators=(",", ":")))
        return exc.exit_code
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "code": "unavailable"}, ensure_ascii=False,
                         separators=(",", ":")))
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["handle", "main"]
