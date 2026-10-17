"""Safe, asynchronous boundary to the independent collector CLI.

The screening application deliberately does not import the collector package
or open its SQLite database.  This module communicates with the collector via
its small JSON control protocol and its existing ``python -m collector run``
entry point only.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


MAX_LOG_TAIL = 8_000
MAX_PROTOCOL_OUTPUT = 1_048_576
MAX_ERROR_LENGTH = 500
# The standalone Windows daily task permits two hours. Attachment-heavy full
# runs already exceed thirty minutes, so the platform must use the same budget.
DEFAULT_RUN_TIMEOUT = 7_200.0
DEFAULT_CONTROL_TIMEOUT = 20.0
DEFAULT_IMPORT_POLL_INTERVAL = 1.0

FileSignature = tuple[int, int]


class CollectionError(RuntimeError):
    """An error crossing the collector management boundary."""

    code = "unavailable"


class CollectionBusy(CollectionError):
    code = "busy"


class CollectionInvalid(CollectionError):
    code = "invalid"


class CollectionUnavailable(CollectionError):
    code = "unavailable"


@dataclass
class CollectionTask:
    id: str
    status: str
    source_id: str | None
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    log_tail: str = ""
    import_result: object | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "source_id": self.source_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "log_tail": self.log_tail,
            "import_result": self.import_result,
        }


@dataclass
class _CompletedProcess:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class _ImportMonitor:
    """State shared by the bounded export watcher and its single consumer."""

    consumed: FileSignature | None
    pending: FileSignature | None = None
    last_error: str | None = None
    successful_batches: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _bounded_text(value: Any, limit: int = MAX_LOG_TAIL) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1):]


def _coerce_number(value: Any) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if number.is_integer() else number


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class CollectionManager:
    """Own collector subprocesses and expose the frozen management payload.

    ``screening`` is intentionally typed as ``Any``: the manager only calls
    its public ``import_file`` coroutine and reads its configured source path.
    The collector's config and database are never imported or opened for
    writes here.
    """

    def __init__(
        self,
        screening: Any,
        *,
        root: Path | str | None = None,
        config: Path | str | None = None,
        data: Path | str | None = None,
        python_executable: str | None = None,
        timeout_seconds: float | None = None,
        control_timeout_seconds: float | None = None,
        timeout: float | None = None,
        importer: Callable[[], Awaitable[object] | object] | None = None,
        import_poll_interval: float | None = None,
    ) -> None:
        self.screening = screening
        self.root = Path(root) if root is not None else self._default_root()
        self.data = Path(data) if data is not None else Path(
            os.getenv("SCREENING_COLLECTOR_DATA", str(self.root / "data"))
        )
        self.config = Path(config) if config is not None else Path(
            os.getenv("SCREENING_COLLECTOR_CONFIG", str(self.root / "sources.json"))
        )
        self._python_executable = python_executable
        chosen_timeout = timeout_seconds if timeout_seconds is not None else timeout
        self.run_timeout = max(
            0.01,
            float(chosen_timeout if chosen_timeout is not None else os.getenv(
                "SCREENING_COLLECTOR_TIMEOUT", str(DEFAULT_RUN_TIMEOUT)
            )),
        )
        self.control_timeout = max(
            0.01,
            float(control_timeout_seconds if control_timeout_seconds is not None else min(
                self.run_timeout, DEFAULT_CONTROL_TIMEOUT
            )),
        )
        self._import_poll_interval = max(
            0.01,
            float(import_poll_interval if import_poll_interval is not None else os.getenv(
                "SCREENING_COLLECTOR_IMPORT_POLL_SECONDS", str(DEFAULT_IMPORT_POLL_INTERVAL)
            )),
        )
        self._importer = importer or getattr(screening, "import_file", None)
        if self._importer is None:
            raise TypeError("screening import_file is required")
        self._task: CollectionTask | None = None
        self._task_handle: asyncio.Task[Any] | None = None
        self._processes: set[Any] = set()
        self._start_lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _default_root() -> Path:
        configured = os.getenv("SCREENING_COLLECTOR_ROOT")
        if configured:
            return Path(configured)
        # In a normal checkout the package is ``<screening-root>/backend``;
        # only inspect that project's immediate parent. A worktree that has
        # no sibling must set SCREENING_COLLECTOR_ROOT explicitly, which keeps
        # isolated tests from accidentally selecting a production checkout.
        screening_root = Path(__file__).resolve().parents[2]
        return screening_root.parent / "销售平台信息采集系统"

    def _python(self) -> str:
        if self._python_executable:
            return str(self._python_executable)
        configured = os.getenv("SCREENING_COLLECTOR_PYTHON")
        if configured and configured.strip():
            return configured.strip()
        path_file = self.data / "python_path.txt"
        try:
            value = next(
                (line.strip() for line in path_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()),
                "",
            )
        except (OSError, IndexError):
            value = ""
        return value or sys.executable

    def _secret_values(self) -> tuple[str, ...]:
        values: list[str] = []
        for key, value in os.environ.items():
            upper = key.upper()
            if value and (
                upper in {"OPENAI_API_KEY", "SCREENING_OPENAI_API_KEY"}
                or (upper.startswith("OPENAI_") and ("KEY" in upper or "TOKEN" in upper))
            ):
                values.append(value)
        return tuple(sorted(set(values), key=len, reverse=True))

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in list(env):
            upper = key.upper()
            if upper in {"OPENAI_API_KEY", "SCREENING_OPENAI_API_KEY"} or (
                upper.startswith("OPENAI_") and ("KEY" in upper or "TOKEN" in upper)
            ):
                env.pop(key, None)
        # Rule-based extraction is the collector default. Explicitly disable
        # optional AI switches as a second guard while keeping this boundary
        # independent from the screening application's AI settings.
        env["OPENAI_ENABLED"] = "0"
        env["SCREENING_AI_ENABLED"] = "0"
        env["COLLECTOR_AI_ENABLED"] = "0"
        # Preserve COLLECTOR_SCHEDULER_DISABLED when QA sets it for temporary
        # collector runs; production can still arrange the existing task.
        return env

    def _redact(self, value: Any, limit: int = MAX_ERROR_LENGTH) -> str:
        text = _text(value)
        for secret in self._secret_values():
            text = text.replace(secret, "[redacted]")
        return _bounded_text(text, limit)

    def _creation_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(self.root),
            "env": self._child_env(),
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return kwargs

    async def _terminate_process(self, process: Any) -> None:
        """Stop a collector and its converter descendants when possible."""
        if getattr(process, "returncode", None) is not None:
            return
        pid = getattr(process, "pid", None)
        if os.name == "nt" and pid:
            # Collector attachment conversion can create descendants. Keep
            # this fixed and shell-free so shutdown cannot execute data as a
            # command. A failed taskkill is followed by direct termination.
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(int(pid)), "/T", "/F",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}),
                )
                try:
                    await asyncio.wait_for(killer.communicate(), timeout=5.0)
                except asyncio.TimeoutError:
                    if getattr(killer, "kill", None):
                        killer.kill()
            except (OSError, ValueError):
                pass
        try:
            if getattr(process, "returncode", None) is None and hasattr(process, "terminate"):
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                if hasattr(process, "kill"):
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
        except (ProcessLookupError, AttributeError):
            pass

    async def _execute_process(self, argv: list[str], payload: bytes = b"", *, timeout: float) -> _CompletedProcess:
        try:
            process = await asyncio.create_subprocess_exec(*argv, **self._creation_kwargs())
        except (FileNotFoundError, OSError) as exc:
            raise CollectionUnavailable(f"采集系统不可用：{self._redact(exc)}") from exc
        self._processes.add(process)
        try:
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=timeout)
            except asyncio.TimeoutError as exc:
                await self._terminate_process(process)
                raise CollectionUnavailable("采集命令超时") from exc
            except OSError as exc:
                await self._terminate_process(process)
                raise CollectionUnavailable(f"采集系统不可用：{self._redact(exc)}") from exc
            except asyncio.CancelledError:
                await self._terminate_process(process)
                raise
        finally:
            self._processes.discard(process)
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return _CompletedProcess(
            int(getattr(process, "returncode", 0) or 0),
            # Control status is JSON and can legitimately contain dozens of
            # sources. Keep a separate, larger protocol bound; task display
            # logs are trimmed later in _run_task.
            self._redact(stdout, MAX_PROTOCOL_OUTPUT),
            self._redact(stderr, MAX_LOG_TAIL),
        )

    @staticmethod
    def _json_output(stdout: str) -> dict[str, Any] | None:
        whole = stdout.strip()
        if whole:
            try:
                value = json.loads(whole)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                return value
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    async def _control(self, request: dict[str, Any]) -> dict[str, Any]:
        argv = [self._python(), "-m", "collector.control", "--config", str(self.config), "--data", str(self.data)]
        completed = await self._execute_process(
            argv,
            (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
            timeout=self.control_timeout,
        )
        value = self._json_output(completed.stdout)
        if completed.returncode != 0:
            code = str(value.get("code") or "") if value else ""
            error = self._redact((value or {}).get("error") or completed.stderr or completed.stdout or "采集命令执行失败")
            if code == "busy":
                raise CollectionBusy(error)
            if code == "invalid":
                raise CollectionInvalid(error)
            raise CollectionUnavailable(error)
        if value is None:
            raise CollectionUnavailable(self._redact(completed.stderr or completed.stdout or "采集控制返回了无效 JSON"))
        return value

    @staticmethod
    def _normalize_last_run(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return {
            "status": _text(value.get("status")),
            "started_at": value.get("started_at"),
            "finished_at": value.get("finished_at"),
            "found_count": _coerce_int(value.get("found_count")),
            "fetched_count": _coerce_int(value.get("fetched_count")),
            "error": value.get("error"),
        }

    @classmethod
    def _normalize_source(cls, value: Any) -> dict[str, Any]:
        value = value if isinstance(value, Mapping) else {}
        return {
            "id": _text(value.get("id")),
            "name": _text(value.get("name")),
            "category": _text(value.get("category")),
            "enabled": bool(value.get("enabled", False)),
            "access_status": _text(value.get("access_status"), "未知"),
            "access_note": _text(value.get("access_note")),
            "entry_url": value.get("entry_url"),
            "last_success_at": value.get("last_success_at"),
            "last_failure_at": value.get("last_failure_at"),
            "last_error": value.get("last_error"),
            "last_run": cls._normalize_last_run(value.get("last_run")),
        }

    @staticmethod
    def _normalize_run(value: Any) -> dict[str, Any]:
        value = value if isinstance(value, Mapping) else {}
        stats = value.get("stats")
        if not isinstance(stats, Mapping):
            stats = {}
        return {
            "id": value.get("id"),
            "started_at": value.get("started_at"),
            "finished_at": value.get("finished_at"),
            "status": _text(value.get("status")),
            "stats": {str(key): _coerce_number(item) for key, item in stats.items()},
        }

    @staticmethod
    def _normalize_schedule(value: Any, *, available: bool, message: str) -> dict[str, Any]:
        value = value if isinstance(value, Mapping) else {}
        return {
            "available": bool(value.get("available", available)),
            "registered": bool(value.get("registered", False)),
            "enabled": bool(value.get("enabled", False)),
            "at": value.get("at"),
            "state": _text(value.get("state"), "unavailable" if not available else "unknown"),
            "message": _text(value.get("message"), message),
        }

    def _task_dict(self) -> dict[str, Any] | None:
        return self._task.as_dict() if self._task is not None else None

    def _empty_status(self, message: str) -> dict[str, Any]:
        return {
            "available": False,
            "import_connected": False,
            "message": self._redact(message),
            "running": bool(self._task and self._task.status == "running"),
            "sources": [],
            "runs": [],
            "schedule": self._normalize_schedule({}, available=False, message=self._redact(message)),
            "task": self._task_dict(),
        }

    def _normalize_status(self, value: Mapping[str, Any]) -> dict[str, Any]:
        available = bool(value.get("available", True))
        message = _text(value.get("message"), "采集系统可用" if available else "采集系统不可用")
        sources = value.get("sources")
        runs = value.get("runs")
        return {
            "available": available,
            "import_connected": self._import_connected(),
            "message": self._redact(message),
            "running": bool(value.get("running", False)) or bool(self._task and self._task.status == "running"),
            "sources": [self._normalize_source(item) for item in sources] if isinstance(sources, list) else [],
            "runs": [self._normalize_run(item) for item in runs] if isinstance(runs, list) else [],
            "schedule": self._normalize_schedule(value.get("schedule"), available=available, message=message),
            "task": self._task_dict(),
        }

    async def status(self) -> dict[str, Any]:
        """Read collector status without blocking the FastAPI event loop."""
        if self._closed:
            return self._empty_status("采集管理已关闭")
        try:
            value = await self._control({"action": "status"})
        except CollectionError as exc:
            return self._empty_status(str(exc))
        return self._normalize_status(value)

    def _path_equal(self, left: Path, right: Path) -> bool:
        try:
            left_value = left.resolve(strict=False)
            right_value = right.resolve(strict=False)
        except OSError:
            left_value, right_value = left.absolute(), right.absolute()
        if os.name == "nt":
            return str(left_value).casefold() == str(right_value).casefold()
        return left_value == right_value

    def _configured_export_path(self) -> Path:
        # The collector's CLI contract derives its downstream file solely from
        # --data. Do not guess at unrelated config keys: the only safe import
        # path is this canonical export path.
        return self.data / "export" / "records.jsonl"

    def _export_signature(self) -> FileSignature | None:
        """Return the atomic export's change signature, or None if unavailable."""
        try:
            stat = self._configured_export_path().stat()
        except OSError:
            return None
        # The collector publishes an empty placeholder at run startup. It is
        # not an importable snapshot; wait for the first committed record so a
        # transient placeholder cannot create a durable import error.
        if stat.st_size <= 0:
            return None
        return int(stat.st_mtime_ns), int(stat.st_size)

    async def _watch_export(self, monitor: _ImportMonitor) -> None:
        """Publish the latest changed export signature to one consumer.

        The watcher does no database work and never creates a task per change.
        A pending signature is deliberately overwritten by a newer one while
        an import is in progress; the consumer then imports the newest file
        after the current import finishes.
        """
        while not monitor.stop.is_set():
            signature = self._export_signature()
            if signature is not None and signature != monitor.consumed:
                monitor.pending = signature
                monitor.changed.set()
            try:
                await asyncio.wait_for(monitor.stop.wait(), timeout=self._import_poll_interval)
            except asyncio.TimeoutError:
                continue

    def _record_import_error(self, task: CollectionTask, monitor: _ImportMonitor, exc: Any) -> None:
        error = self._redact(f"筛选数据导入失败：{exc}")
        monitor.last_error = error
        previous = task.import_result
        if isinstance(previous, Mapping):
            result = dict(previous)
            result["error"] = error
            task.import_result = result
        elif previous is None:
            task.import_result = {"error": error}
        else:
            task.import_result = {"result": previous, "error": error}
        task.error = error

    def _record_import_success(self, task: CollectionTask, monitor: _ImportMonitor, value: Any) -> None:
        incoming = dict(value) if isinstance(value, Mapping) else {"result": value}
        previous = task.import_result
        previous_map = dict(previous) if isinstance(previous, Mapping) else {}
        previous_success = monitor.successful_batches > 0
        previous_map.pop("error", None)

        merged = dict(previous_map)
        merged.update(incoming)
        for key in ("inserted", "updated"):
            if key in incoming or key in previous_map:
                merged[key] = _coerce_int(previous_map.get(key)) + _coerce_int(incoming.get(key))

        if previous_success:
            merged["batches"] = (
                _coerce_int(previous_map.get("batches")) or monitor.successful_batches
            ) + 1
        elif "batches" in incoming:
            merged["batches"] = incoming["batches"]

        # ``incoming`` wins for unchanged/total, so these values describe only
        # the most recent snapshot while inserted/updated remain cumulative.
        task.import_result = merged
        monitor.successful_batches += 1
        monitor.last_error = None
        task.error = None

    async def _import_snapshot(
        self,
        task: CollectionTask,
        monitor: _ImportMonitor,
        signature: FileSignature,
    ) -> bool:
        """Consume one changed snapshot through the existing importer."""
        if self._closed:
            return False
        try:
            # The screening service can be reconfigured while collection is
            # running. Validate its live path immediately before invoking the
            # importer so a stale bound callable cannot import another file.
            self._validate_import_path()
            result = self._importer()
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_import_error(task, monitor, exc)
            return False
        self._record_import_success(task, monitor, result)
        monitor.consumed = signature
        return True

    def _import_connected(self) -> bool:
        source = getattr(self.screening, "source_path", None)
        return source is None or self._path_equal(Path(source), self._configured_export_path())

    def _validate_import_path(self) -> None:
        source = getattr(self.screening, "source_path", None)
        if source is None:
            return
        source_path = Path(source)
        export_path = self._configured_export_path()
        if not self._path_equal(source_path, export_path):
            raise CollectionInvalid(
                f"采集结果尚未接入平台，请在系统设置的数据接入中将导入路径设为 {export_path}；当前为 {source_path}"
            )

    async def _validate_before_run(self, source_id: str | None) -> None:
        if source_id is not None and not source_id.strip():
            raise CollectionInvalid("来源 ID 不能为空")
        self._validate_import_path()
        state = await self.status()
        if not state["available"]:
            raise CollectionUnavailable(state["message"] or "采集系统不可用")
        if state["running"]:
            raise CollectionBusy("采集正在运行")
        if source_id is None and not any(item["enabled"] for item in state["sources"]):
            raise CollectionInvalid("没有启用的采集来源，请先启用来源")
        if source_id is not None:
            source_ids = {item["id"] for item in state["sources"]}
            if source_id not in source_ids:
                raise CollectionInvalid(f"未找到采集来源：{source_id}")
            source = next(item for item in state["sources"] if item["id"] == source_id)
            if not source["enabled"]:
                raise CollectionInvalid(f"来源 {source_id} 已停用，请先启用后再采集")

    async def start(self, source_id: str | None = None) -> dict[str, Any]:
        """Start one asynchronous collector task after a live busy check."""
        if source_id is not None:
            source_id = source_id.strip()
        async with self._start_lock:
            if self._closed:
                raise CollectionUnavailable("采集管理已关闭")
            if self._task is not None and self._task.status == "running":
                raise CollectionBusy("采集正在运行")
            await self._validate_before_run(source_id)
            task = CollectionTask(
                id=uuid.uuid4().hex,
                status="running",
                source_id=source_id,
                started_at=_now(),
            )
            self._task = task
            self._task_handle = asyncio.create_task(self._run_task(task), name=f"collector-{task.id}")
            return task.as_dict()

    async def _run_task(self, task: CollectionTask) -> None:
        source_id = task.source_id
        argv = [self._python(), "-m", "collector", "--config", str(self.config), "--data", str(self.data), "run"]
        if source_id is not None:
            argv.extend(["--source", source_id])
        monitor = _ImportMonitor(consumed=self._export_signature())
        process_task: asyncio.Task[_CompletedProcess] | None = None
        watch_task: asyncio.Task[Any] | None = None
        completed: _CompletedProcess | None = None
        try:
            # Capture the pre-run signature before starting the child. An old
            # export that remains untouched is not a result of this run.
            process_task = asyncio.create_task(
                self._execute_process(argv, timeout=self.run_timeout),
                name=f"collector-process-{task.id}",
            )
            watch_task = asyncio.create_task(
                self._watch_export(monitor),
                name=f"collector-export-watch-{task.id}",
            )

            # Keep one importer consumer. The watcher only coalesces the
            # newest signature while this await may be inside import_file.
            while True:
                if monitor.pending is not None:
                    signature = monitor.pending
                    monitor.pending = None
                    if signature == monitor.consumed:
                        # The watcher may have observed the same signature
                        # again while an import was awaiting the database.
                        # The successful consumer already handled it.
                        monitor.changed.clear()
                        continue
                    imported = await self._import_snapshot(task, monitor, signature)
                    if not imported:
                        # A failed snapshot remains eligible for retry while
                        # the child is alive, but retry at the poll cadence
                        # rather than spinning on an already-set Event.
                        if monitor.pending == signature:
                            monitor.pending = None
                        if monitor.pending is None:
                            monitor.changed.clear()
                            if process_task.done():
                                break
                            try:
                                await asyncio.wait_for(
                                    monitor.changed.wait(), timeout=self._import_poll_interval
                                )
                            except asyncio.TimeoutError:
                                pass
                    continue
                if process_task.done():
                    break
                monitor.changed.clear()
                try:
                    await asyncio.wait_for(monitor.changed.wait(), timeout=self._import_poll_interval)
                except asyncio.TimeoutError:
                    pass

            # No more writes can occur once the process task is done. Stop the
            # poller before the bounded final drain so its state cannot race
            # the consumer.
            monitor.stop.set()
            if watch_task is not None:
                watch_task.cancel()
                await asyncio.gather(watch_task, return_exceptions=True)
                watch_task = None

            try:
                completed = process_task.result()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                process_error = exc
            else:
                process_error = None

            # A process can publish immediately before exiting, before the
            # watcher gets its next tick. Drain that latest valid snapshot;
            # after an already-failed final attempt, do not busy-loop.
            while True:
                latest = self._export_signature()
                if latest is None or latest == monitor.consumed:
                    break
                imported = await self._import_snapshot(task, monitor, latest)
                if not imported:
                    break

            if process_error is not None:
                raise process_error

            combined_log = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
            task.log_tail = _bounded_text(combined_log, MAX_LOG_TAIL)
            if completed.returncode == 3:
                raise CollectionBusy("采集正在运行")
            if completed.returncode not in (0, 2):
                detail = completed.stderr or completed.stdout or f"采集进程退出码：{completed.returncode}"
                task.status = "failed"
                task.error = self._redact(detail)
                return

            if completed.returncode == 2:
                task.status = "partial"
                task.error = monitor.last_error or (self._redact(completed.stderr) if completed.stderr else None)
            elif monitor.last_error:
                task.status = "failed"
                task.error = monitor.last_error
            else:
                task.status = "succeeded"
        except CollectionBusy as exc:
            task.status = "failed"
            task.error = self._redact(str(exc))
        except asyncio.CancelledError:
            # The process boundary has already been cancelled/terminated by
            # _execute_process. Retain a bounded task record for diagnostics.
            if task.status == "running":
                task.status = "failed"
                task.error = "采集任务已取消"
            raise
        except CollectionError as exc:
            task.status = "failed"
            task.error = self._redact(str(exc))
        except Exception as exc:  # defensive boundary for background tasks
            task.status = "failed"
            task.error = self._redact(f"采集任务失败：{exc}")
        finally:
            monitor.stop.set()
            if watch_task is not None and not watch_task.done():
                watch_task.cancel()
                await asyncio.gather(watch_task, return_exceptions=True)
            if process_task is not None and not process_task.done():
                process_task.cancel()
                await asyncio.gather(process_task, return_exceptions=True)
            if task.status == "running":
                task.status = "failed"
                task.error = task.error or "采集任务异常结束"
            task.finished_at = _now()

    async def set_source(self, source_id: str, enabled: bool) -> dict[str, str]:
        if not source_id or not source_id.strip():
            raise CollectionInvalid("来源 ID 不能为空")
        if self._closed:
            raise CollectionUnavailable("采集管理已关闭")
        value = await self._control({"action": "source", "source_id": source_id.strip(), "enabled": bool(enabled)})
        return {"message": _text(value.get("message"), "采集来源已更新")}

    async def set_schedule(self, enabled: bool, at: str) -> dict[str, str]:
        if self._closed:
            raise CollectionUnavailable("采集管理已关闭")
        # The standalone Windows runner uses its checkout's default paths.
        # Refuse to schedule a different config/data pair as though it were
        # the same source set; manual collection may still use those paths.
        if not self._path_equal(self.config, self.root / "sources.json") or not self._path_equal(self.data, self.root / "data"):
            raise CollectionInvalid("每日任务使用采集模块的默认来源与数据目录；自定义路径请使用独立调度")
        value = await self._control({"action": "schedule", "enabled": bool(enabled), "at": at})
        return {"message": _text(value.get("message"), "采集计划已更新")}

    async def aclose(self) -> None:
        if self._closed:
            # A second close still waits for no task; the first close owns all
            # process cleanup and leaves an idempotent manager behind.
            return
        self._closed = True
        current = asyncio.current_task()
        task_handle = self._task_handle
        if task_handle is not None and task_handle is current:
            task_handle = None
        if task_handle is not None and not task_handle.done():
            for process in list(self._processes):
                await self._terminate_process(process)
            task_handle.cancel()
            await asyncio.gather(task_handle, return_exceptions=True)
        else:
            for process in list(self._processes):
                await self._terminate_process(process)
        self._processes.clear()

    def close(self) -> None:
        """Best-effort synchronous close for non-FastAPI callers."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
        else:
            loop.create_task(self.aclose())
