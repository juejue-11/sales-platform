from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

from ..documents.document_access import (
    DocumentAccessError,
    validate_document_access_arguments,
)
from ..research.project_research import RESEARCH_TOOLS, validate_research_arguments
from .errors import (
    AIError,
    _runtime_error_message,
    _safe_error_code,
    _safe_error_metadata,
    _safe_tool_error,
)

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_LINE_BYTES = 512 * 1024
DEFAULT_LIMITS = {
    "max_turns": 6,
    "max_tool_calls": 12,
    "timeout_ms": 300_000,
    "max_output_tokens": 16_384,
}
_PROGRESS_STAGES = {"started", "model", "tool_start", "tool_end", "completed"}
_TRUSTED_TOOLS = {"read_project_records", "read_company_capability", "read_related_records",
                  "fetch_project_documents", *RESEARCH_TOOLS}
_TRUSTED_TOOL_LABELS = {
    "read_project_records": "读取项目资料",
    "read_company_capability": "读取公司能力",
    "read_related_records": "读取关联资料",
    "fetch_project_documents": "读取采购附件",
    "search_project_sources": "搜索项目资料",
    "read_research_source": "读取调查来源",
    "assess_source_relation": "核实资料关系",
    "search_research_content": "检索资料内容",
}
_ERROR_CODES = {
    "INVALID_INPUT", "MODEL_CONFIG", "MODEL_ERROR", "OUTPUT_TOKEN_LIMIT", "NO_FINAL",
    "TURN_LIMIT", "TOOL_LIMIT", "TIMEOUT", "INTERNAL",
    # Keep compatibility with older runner builds while still rejecting
    # arbitrary provider/model text from the error event.
    "INVALID_REQUEST", "CONFIG_ERROR", "RUNTIME_ERROR",
    "TOOL_INVALID", "TOOL_FORBIDDEN", "TOOL_UNAVAILABLE", "TOOL_ERROR", "RPC_ERROR",
    # Current runtime classifications.  Keep these as a closed allow-list so
    # provider/parser text never becomes a durable error code.
    "TOOL_ARGUMENT_JSON", "TOOL_SCHEMA", "TOOL_PARSER_ERROR",
    "STREAM_INTERRUPTED", "MODEL_FORMAT_ERROR",
    "RESEARCH_LIMIT", "SOURCE_NOT_FOUND", "SOURCE_UNREADABLE", "RELATION_UNCONFIRMED",
}
_PROGRESS_LABELS = {
    "started": "智能体已启动",
    "model": "模型调用",
    "tool_start": "工具调用开始",
    "tool_end": "工具调用完成",
    "completed": "方案已通过校验",
}
def _runtime_root() -> Path:
    configured = os.getenv("SCREENING_AGENT_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "agent-runtime"


def _runner_path() -> Path | None:
    configured = os.getenv("SCREENING_AGENT_RUNNER")
    candidates = [Path(configured)] if configured else []
    candidates.extend([_runtime_root() / "dist" / "runner.js"])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _node_candidates() -> Iterator[Path]:
    """Yield runtime candidates in priority order without eager filesystem scans."""
    runtime = _runtime_root()
    names = ["node.exe", "node"] if os.name == "nt" else ["node"]
    configured = os.getenv("SCREENING_NODE_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_dir():
            for name in names:
                yield configured_path / name
        else:
            yield configured_path
    found = shutil.which("node")
    if found:
        yield Path(found)

    # A packaged deployment may carry a nodebin dependency rather than rely on
    # PATH.  Keep this search explicit and bounded; never crawl the user's
    # whole filesystem looking for a binary.
    for name in names:
        yield runtime / "nodebin" / name
    for name in names:
        yield runtime / "node_modules" / ".bin" / name
    for package_dir in ("node", "nodebin"):
        for name in names:
            yield runtime / "node_modules" / package_dir / "bin" / name
    try:
        pnpm_dir = runtime / "node_modules" / ".pnpm"
        if pnpm_dir.is_dir():
            for package_dir in pnpm_dir.iterdir():
                if package_dir.is_dir() and package_dir.name.lower().startswith(("node@", "nodebin@")):
                    for package_name in ("node", "nodebin"):
                        for name in names:
                            yield package_dir / "node_modules" / package_name / "bin" / name
    except OSError:
        pass


def resolve_sdk_runtime() -> tuple[Path | None, Path | None]:
    """Return the usable node executable and runner without invoking either."""
    node: Path | None = None
    for candidate in _node_candidates():
        try:
            if candidate.is_file():
                node = candidate.resolve()
                break
        except OSError:
            continue
    return node, _runner_path()


def sdk_ready() -> bool:
    node, runner = resolve_sdk_runtime()
    return bool(node and runner)


def _safe_progress_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an untrusted runtime progress line for the public activity log."""
    stage = event.get("stage")
    if stage not in _PROGRESS_STAGES:
        return None
    # The runner's completed event only means that it emitted JSON.  The
    # service emits the public completion event after validation and storage.
    if stage == "completed":
        return None
    result: dict[str, Any] = {
        "stage": stage,
        # Labels come from this process's fixed map.  A runtime cannot put
        # arbitrary model text or secrets into the activity feed.
        "label": _PROGRESS_LABELS[stage],
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tool = event.get("tool")
    if stage in {"tool_start", "tool_end"} and isinstance(tool, str) and tool in _TRUSTED_TOOLS:
        tool = re.sub(r"[^A-Za-z0-9_.:/-]", "", tool)[:100]
        if tool:
            result["tool"] = tool
            business_label = _TRUSTED_TOOL_LABELS[tool]
            if stage == "tool_start":
                result["label"] = f"{business_label}开始"
            elif event.get("is_error") is True:
                result["label"] = f"{business_label}失败"
            else:
                result["label"] = f"{business_label}完成"
            if event.get("is_error") is True:
                # Internal-only marker consumed by service persistence; this
                # key is never serialized into the public agent_run step.
                result["_is_error"] = True
    elif stage == "tool_end" and event.get("is_error") is True:
        result["label"] = "工具调用失败"
        result["_is_error"] = True
    turn = event.get("turn")
    if isinstance(turn, int) and 0 <= turn <= 1000:
        result["turn"] = turn
    return result
@dataclass(frozen=True)
class PiSDKResult:
    content: str
    usage: dict[str, int]
    # New runners echo the requested protocol task.  ``None`` keeps older
    # injected/runtime doubles compatible; the reading boundary can then
    # decide whether their first response is a legacy judgment response.
    task_kind: str | None = None

    # Mapping-style access keeps the bridge convenient for small integration
    # tests and callers that handle protocol results as JSON objects.
    def __getitem__(self, key: str) -> Any:
        if key in {"content", "usage"}:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in {"content", "usage"} else default


class PiSDKProvider:
    """Run the embedded Pi SDK through the newline JSON protocol.

    The API key is serialized only in the request body on stdin.  It is never
    put in command arguments, the child environment, or diagnostic output.
    """

    def __init__(self, *, max_input_bytes: int = MAX_INPUT_BYTES,
                 max_output_bytes: int = MAX_OUTPUT_BYTES,
                 max_output_line_bytes: int = MAX_OUTPUT_LINE_BYTES) -> None:
        self.max_input_bytes = max(1024, int(max_input_bytes))
        self.max_output_bytes = max(1024, int(max_output_bytes))
        self.max_output_line_bytes = max(1024, int(max_output_line_bytes))

    @staticmethod
    def sdk_ready() -> bool:
        return sdk_ready()

    @staticmethod
    def runtime_paths() -> tuple[Path | None, Path | None]:
        return resolve_sdk_runtime()

    @staticmethod
    def _child_environment() -> dict[str, str]:
        # Keep normal runtime settings (PATH, locale, proxy configuration),
        # but remove common credential variables so the SDK can only receive a
        # key from the explicitly constructed stdin protocol request.
        blocked = {
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
            "COHERE_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "SCREENING_API_KEY", "SCREENING_OPENAI_API_KEY",
        }
        result: dict[str, str] = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if key in blocked or any(token in upper for token in ("API_KEY", "ACCESS_KEY", "TOKEN", "SECRET", "PASSWORD")):
                continue
            result[key] = value
        return result

    @staticmethod
    def _normalized_limits(limits: dict[str, Any] | None) -> dict[str, int]:
        value = dict(DEFAULT_LIMITS)
        if isinstance(limits, dict):
            for key in value:
                candidate = limits.get(key)
                minimum = 0 if key == "max_tool_calls" else 1
                if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= minimum:
                    value[key] = int(candidate)
        # Keep the bridge's timeout bounded even if a caller accidentally
        # supplies an enormous number. A normal run remains five minutes.
        value["timeout_ms"] = min(max(value["timeout_ms"], 100), 24 * 60 * 60 * 1000)
        return value

    async def _emit(self, callback: Callable[[dict[str, Any]], Any] | None,
                    event: dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            value = callback(event)
            if hasattr(value, "__await__"):
                await value
        except asyncio.CancelledError:
            raise
        except Exception:
            # Activity persistence must never turn a valid model result into a
            # failed analysis.  The service logs no callback data.
            return

    @staticmethod
    async def _drain_stderr(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        try:
            while await stream.read(8192):
                pass
        except (asyncio.CancelledError, Exception):
            return

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    process.terminate()
                except (ProcessLookupError, OSError):
                    pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            return

    async def analyze(self, *, base_url: str, model: str, api_key: str,
                      messages: list[dict[str, str]], context: dict[str, Any],
                      limits: dict[str, Any] | None = None,
                      on_event: Callable[[dict[str, Any]], Any] | None = None,
                      callback: Callable[[dict[str, Any]], Any] | None = None,
                      tool_callback: Callable[..., Any] | None = None,
                      task_kind: str | None = None) -> PiSDKResult:
        if not base_url or not model or not api_key:
            raise AIError("AI is not configured")
        node, runner = resolve_sdk_runtime()
        if node is None or runner is None:
            raise AIError("Pi SDK运行时未就绪，请安装Node.js 22.19+并构建agent-runtime/dist/runner.js")
        effective_callback = on_event or callback
        protocol = 2 if tool_callback is not None else 1
        request = {
            "protocol": protocol,
            "config": {"base_url": base_url, "model": model, "api_key": api_key},
            "messages": messages,
            "context": context,
            "limits": self._normalized_limits(limits),
        }
        if task_kind in {"market", "reading"}:
            request["task_kind"] = task_kind
        try:
            encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AIError("Pi SDK输入协议无效") from exc
        if len(encoded) > self.max_input_bytes:
            raise AIError("Pi SDK输入超过大小限制")

        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[Any] | None = None
        total_output = 0
        result: PiSDKResult | None = None
        seen_result = False
        seen_tool_requests: set[str] = set()
        timeout_seconds = self._normalized_limits(limits)["timeout_ms"] / 1000.0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds

        async def await_with_deadline(awaitable: Awaitable[Any]) -> Any:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AIError("Pi SDK运行超时")
            try:
                return await asyncio.wait_for(awaitable, timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise AIError("Pi SDK运行超时") from exc

        try:
            spawn_options: dict[str, Any] = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "env": self._child_environment(),
                "limit": self.max_output_line_bytes,
            }
            if os.name == "nt":
                spawn_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # The deadline starts before process creation and stdin handling.
            # A child that never reads its pipe must not hold the API task
            # indefinitely while drain() waits for back-pressure to clear.
            process = await await_with_deadline(
                asyncio.create_subprocess_exec(str(node), str(runner), **spawn_options)
            )
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            if process.stdin is None or process.stdout is None:
                raise AIError("Pi SDK进程管道不可用")
            try:
                process.stdin.write(encoded + b"\n")
                await await_with_deadline(process.stdin.drain())
            except (BrokenPipeError, ConnectionError) as exc:
                raise AIError("Pi SDK进程管道不可用") from exc
            if protocol == 1:
                process.stdin.close()
                try:
                    await await_with_deadline(process.stdin.wait_closed())
                except (AttributeError, ConnectionError, BrokenPipeError):
                    pass

            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise AIError("Pi SDK运行超时")
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise AIError("Pi SDK运行超时") from exc
                except (asyncio.LimitOverrunError, ValueError) as exc:
                    raise AIError("Pi SDK输出超过单行大小限制") from exc
                if not line:
                    break
                total_output += len(line)
                if total_output > self.max_output_bytes or len(line) > self.max_output_line_bytes:
                    raise AIError("Pi SDK输出超过大小限制")
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AIError("Pi SDK输出协议无效") from exc
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise AIError("Pi SDK输出协议无效")
                event_type = event["type"]
                if event_type == "progress":
                    clean = _safe_progress_event(event)
                    if clean is not None:
                        await self._emit(effective_callback, clean)
                    elif event.get("stage") == "completed":
                        # Completion is trusted only after service-side
                        # judgment validation and persistence.
                        continue
                    else:
                        raise AIError("Pi SDK进度协议无效")
                elif event_type == "tool_request":
                    if protocol != 2 or tool_callback is None:
                        raise AIError("Pi SDK工具协议无效")
                    request_id = event.get("id")
                    if not isinstance(request_id, str) or not request_id or len(request_id) > 128 or request_id in seen_tool_requests:
                        raise AIError("Pi SDK工具协议无效")
                    tool_name = event.get("tool")
                    if tool_name not in {"fetch_project_documents", *RESEARCH_TOOLS}:
                        raise AIError("Pi SDK工具协议无效")
                    arguments = event.get("arguments", {})
                    if tool_name in RESEARCH_TOOLS:
                        try:
                            arguments = validate_research_arguments(tool_name, arguments)
                        except (TypeError, ValueError) as exc:
                            raise AIError("Pi SDK工具协议无效") from exc
                    else:
                        try:
                            validate_document_access_arguments(arguments)
                        except DocumentAccessError as exc:
                            raise AIError("Pi SDK工具协议无效") from exc
                    seen_tool_requests.add(request_id)
                    try:
                        parameters = inspect.signature(tool_callback).parameters
                        named_dispatch = "tool" in parameters or any(
                            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
                        if named_dispatch:
                            response_data = tool_callback(arguments, tool=tool_name)
                        elif tool_name == "fetch_project_documents":
                            # Existing attachment-only callbacks remain compatible.
                            response_data = tool_callback(arguments)
                        else:
                            raise AIError("调查工具不可用")
                        if hasattr(response_data, "__await__"):
                            response_data = await await_with_deadline(response_data)
                        if not isinstance(response_data, dict):
                            raise AIError("工具返回无效")
                        response = {"type": "tool_response", "id": request_id, "ok": True, "data": response_data}
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        response = {"type": "tool_response", "id": request_id, "ok": False,
                                    "error": _safe_tool_error(exc)}
                    try:
                        encoded_response = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    except (TypeError, ValueError) as exc:
                        raise AIError("Pi SDK工具返回协议无效") from exc
                    if len(encoded_response) > self.max_input_bytes:
                        raise AIError("Pi SDK工具返回超过大小限制")
                    if process.stdin is None or process.stdin.is_closing():
                        raise AIError("Pi SDK进程管道不可用")
                    try:
                        process.stdin.write(encoded_response + b"\n")
                        await await_with_deadline(process.stdin.drain())
                    except (BrokenPipeError, ConnectionError) as exc:
                        raise AIError("Pi SDK进程管道不可用") from exc
                elif event_type == "usage":
                    usage_value = event.get("usage")
                    keys = ("model_calls", "tool_calls", "input_tokens", "output_tokens")
                    if (not isinstance(usage_value, dict) or any(
                            isinstance(usage_value.get(key), bool) or
                            not isinstance(usage_value.get(key), int) or usage_value[key] < 0
                            for key in keys)):
                        raise AIError("Pi SDK用量协议无效")
                    await self._emit(effective_callback, {
                        "stage": "usage",
                        "usage": {key: min(usage_value[key], 2_000_000_000) for key in keys},
                    })
                elif event_type == "error":
                    code = _safe_error_code(event.get("code"))
                    raw_metadata = event.get("metadata")
                    if not isinstance(raw_metadata, dict):
                        raw_metadata = {}
                    metadata = _safe_error_metadata(raw_metadata)
                    raise AIError(
                        _runtime_error_message(code, request["limits"]["max_output_tokens"]),
                        code=code,
                        metadata=metadata,
                    )
                elif event_type == "result":
                    if seen_result:
                        raise AIError("Pi SDK输出包含多个结果")
                    seen_result = True
                    if event.get("engine") != "pi_sdk" or not isinstance(event.get("content"), str):
                        raise AIError("Pi SDK结果协议无效")
                    # A confirmed normal stop with empty content is a report
                    # format failure, not a broken runner protocol. Pass its
                    # draft to the existing strict validator/limited formatter.
                    if not event["content"].strip() and event.get("finish_reason") != "stop":
                        raise AIError("Pi SDK结果协议无效")
                    if api_key and api_key in event["content"]:
                        # A model/provider must never be able to echo the
                        # request credential into a durable judgment or API
                        # response.  Do not include the value in this error.
                        raise AIError("Pi SDK结果包含受限凭据")
                    usage_value = event.get("usage")
                    if not isinstance(usage_value, dict):
                        raise AIError("Pi SDK结果协议无效")
                    usage = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
                    for key in usage:
                        candidate = usage_value.get(key)
                        if not isinstance(candidate, int) or candidate < 0:
                            raise AIError("Pi SDK结果协议无效")
                        usage[key] = min(candidate, 2_000_000_000)
                    task_kind_value = event.get("task_kind")
                    if task_kind_value is not None and task_kind_value not in {"market", "reading"}:
                        raise AIError("Pi SDK缁撴灉鍗忚鏃犳晥")
                    result = PiSDKResult(content=event["content"].strip(), usage=usage,
                                         task_kind=task_kind_value)
                    if protocol == 2 and process.stdin is not None and not process.stdin.is_closing():
                        # Do not wait for stdout EOF before closing: a duplex
                        # runner may emit result and then await request-stream
                        # EOF before terminating.
                        process.stdin.close()
                else:
                    raise AIError("Pi SDK输出协议无效")

            if protocol == 2 and process.stdin is not None and not process.stdin.is_closing():
                # A protocol 2 runner may wait for EOF after emitting result.
                process.stdin.close()
                try:
                    await await_with_deadline(process.stdin.wait_closed())
                except (AttributeError, ConnectionError, BrokenPipeError):
                    pass
            try:
                return_code = await await_with_deadline(process.wait())
            except AIError:
                raise
            if return_code != 0:
                raise AIError(f"Pi SDK进程退出（{return_code}）")
            if result is None:
                raise AIError("Pi SDK未返回结果")
            return result
        except asyncio.CancelledError:
            raise
        finally:
            if process is not None and process.returncode is None:
                # Shield cleanup so cancellation cannot leave a live Node
                # process behind while the host service is stopping.
                try:
                    process.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    await asyncio.shield(asyncio.wait_for(process.wait(), timeout=3.0))
                except (asyncio.CancelledError, Exception):
                    pass
            if stderr_task is not None:
                stderr_task.cancel()
                try:
                    await asyncio.shield(stderr_task)
                except (asyncio.CancelledError, Exception):
                    pass

    async def complete(self, *, base_url: str, model: str, api_key: str,
                       messages: list[dict[str, str]]) -> dict[str, Any]:
        # Keep the old provider protocol available for callers that only know
        # AIProvider.  The service uses analyze() so it can pass context and
        # persist progress activity.
        result = await self.analyze(base_url=base_url, model=model, api_key=api_key,
                                    messages=messages, context={})
        return {"choices": [{"message": {"content": result.content}}], "usage": result.usage}
