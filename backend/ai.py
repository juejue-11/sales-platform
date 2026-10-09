from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol

import httpx

from .utils import json_text, source_quote, sha256_text


logger = logging.getLogger(__name__)


# The runtime is deliberately kept behind a very small stdin/stdout bridge.
# These limits protect the API process from an accidentally unbounded model
# response while still leaving ample room for a normal project snapshot.
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_LINE_BYTES = 512 * 1024
DEFAULT_LIMITS = {
    "max_turns": 6,
    "max_tool_calls": 12,
    "timeout_ms": 180_000,
    "max_output_tokens": 4096,
}

_PROGRESS_STAGES = {"started", "model", "tool_start", "tool_end", "completed"}
_TRUSTED_TOOLS = {"read_project_records", "read_company_capability", "read_related_records"}
_PROGRESS_LABELS = {
    "started": "智能体已启动",
    "model": "模型调用",
    "tool_start": "工具调用开始",
    "tool_end": "工具调用完成",
    "completed": "智能体输出已完成",
}


def _runtime_root() -> Path:
    configured = os.getenv("SCREENING_AGENT_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "agent-runtime"


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


def _node_candidates() -> list[Path]:
    runtime = _runtime_root()
    names = ["node.exe", "node"] if os.name == "nt" else ["node"]
    candidates: list[Path] = []
    configured = os.getenv("SCREENING_NODE_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.extend(
            [configured_path / name for name in names]
            if configured_path.is_dir()
            else [configured_path]
        )
    found = shutil.which("node")
    if found:
        candidates.append(Path(found))

    # A packaged deployment may carry a nodebin dependency rather than rely on
    # PATH.  Keep this search explicit and bounded; never crawl the user's
    # whole filesystem looking for a binary.
    candidates.extend([
        runtime / "nodebin" / name for name in names
    ])
    candidates.extend([
        runtime / "node_modules" / ".bin" / name for name in names
    ])
    for package_dir in ("node", "nodebin"):
        candidates.extend(runtime / "node_modules" / package_dir / "bin" / name for name in names)
    try:
        pnpm_dir = runtime / "node_modules" / ".pnpm"
        if pnpm_dir.is_dir():
            for package_dir in pnpm_dir.iterdir():
                if package_dir.is_dir() and package_dir.name.lower().startswith(("node@", "nodebin@")):
                    for package_name in ("node", "nodebin"):
                        candidates.extend(package_dir / "node_modules" / package_name / "bin" / name for name in names)
    except OSError:
        pass
    return candidates


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
            result["label"] = f"工具：{tool}{'失败' if stage == 'tool_end' and event.get('is_error') is True else ''}"
    elif stage == "tool_end" and event.get("is_error") is True:
        result["label"] = "工具调用失败"
    turn = event.get("turn")
    if isinstance(turn, int) and 0 <= turn <= 1000:
        result["turn"] = turn
    return result


def _safe_error_code(code: Any) -> str:
    if not isinstance(code, str):
        return "runtime_error"
    clean = re.sub(r"[^A-Za-z0-9_.-]", "", code)[:64]
    return clean or "runtime_error"


def sanitize_ai_error(exc: BaseException, *, default: str = "AI request failed",
                      secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Keep provider failures actionable without persisting arbitrary payloads."""
    text = str(exc or "").strip()
    if not text:
        return default
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 3:
            text = text.replace(secret, "[redacted]")
    lowered = text.lower()
    secret_markers = ("api_key", "authorization", "bearer ", "client_secret", "access_token", "password")
    if any(marker in lowered for marker in secret_markers):
        return default
    # Error text may come from an SDK or an HTTP library.  Do not retain long
    # model/provider payloads or line breaks in the durable job record.
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{12,})", "[redacted]", text)
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_.:/ ()\[\]-]", "", text)
    return text[:240] or default


class AIError(RuntimeError):
    pass


class AIProvider(Protocol):
    async def complete(self, *, base_url: str, model: str, api_key: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        ...


class OpenAICompatibleProvider:
    """Small HTTP client for OpenAI-compatible Chat Completions endpoints."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 90.0):
        self.transport = transport
        self.timeout = timeout

    async def complete(self, *, base_url: str, model: str, api_key: str,
                       messages: list[dict[str, str]]) -> dict[str, Any]:
        if not base_url or not model or not api_key:
            raise AIError("AI is not configured")
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except Exception as exc:  # do not expose API key or request headers
            raise AIError(f"AI request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise AIError(f"AI request returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise AIError("AI response was not JSON") from exc
        return data


@dataclass(frozen=True)
class PiSDKResult:
    content: str
    usage: dict[str, int]

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
                if isinstance(candidate, int) and candidate > 0:
                    value[key] = int(candidate)
        # Keep the bridge's timeout bounded even if a caller accidentally
        # supplies an enormous number.  A normal run remains three minutes.
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
                      callback: Callable[[dict[str, Any]], Any] | None = None) -> PiSDKResult:
        if not base_url or not model or not api_key:
            raise AIError("AI is not configured")
        node, runner = resolve_sdk_runtime()
        if node is None or runner is None:
            raise AIError("Pi SDK运行时未就绪，请安装Node.js 22.19+并构建agent-runtime/dist/runner.js")
        effective_callback = on_event or callback
        request = {
            "protocol": 1,
            "config": {"base_url": base_url, "model": model, "api_key": api_key},
            "messages": messages,
            "context": context,
            "limits": self._normalized_limits(limits),
        }
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
        timeout_seconds = self._normalized_limits(limits)["timeout_ms"] / 1000.0
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
            process = await asyncio.create_subprocess_exec(str(node), str(runner), **spawn_options)
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            if process.stdin is None or process.stdout is None:
                raise AIError("Pi SDK进程管道不可用")
            try:
                process.stdin.write(encoded + b"\n")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                raise AIError("Pi SDK进程管道不可用") from exc
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (AttributeError, ConnectionError, BrokenPipeError):
                pass

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise AIError("Pi SDK运行超时")
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
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
                    else:
                        raise AIError("Pi SDK进度协议无效")
                elif event_type == "error":
                    code = _safe_error_code(event.get("code"))
                    raise AIError(f"Pi SDK运行失败（{code}）")
                elif event_type == "result":
                    if seen_result:
                        raise AIError("Pi SDK输出包含多个结果")
                    seen_result = True
                    if event.get("engine") != "pi_sdk" or not isinstance(event.get("content"), str) or not event.get("content", "").strip():
                        raise AIError("Pi SDK结果协议无效")
                    usage_value = event.get("usage")
                    if not isinstance(usage_value, dict):
                        raise AIError("Pi SDK结果协议无效")
                    usage = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
                    for key in usage:
                        candidate = usage_value.get(key)
                        if not isinstance(candidate, int) or candidate < 0:
                            raise AIError("Pi SDK结果协议无效")
                        usage[key] = min(candidate, 2_000_000_000)
                    result = PiSDKResult(content=event["content"].strip(), usage=usage)
                else:
                    raise AIError("Pi SDK输出协议无效")

            try:
                return_code = await asyncio.wait_for(process.wait(), timeout=max(0.1, deadline - loop.time()))
            except asyncio.TimeoutError as exc:
                raise AIError("Pi SDK运行超时") from exc
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


def _record_fingerprint(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"), "record_id": record.get("record_id"),
        "source_id": record.get("source_id"), "title": record.get("title"),
        "project_name": record.get("project_name"), "body_text": record.get("body_text"),
        "kind": record.get("kind"), "buyer": record.get("buyer"), "equipment": record.get("equipment"),
        "facts": record.get("facts"), "inferences": record.get("inferences"),
        "published_at": record.get("published_at"), "notice_number": record.get("notice_number"),
        "deadline_at": record.get("deadline_at"), "original_url": record.get("original_url"),
        "canonical_url": record.get("canonical_url"), "parse_status": record.get("parse_status"),
        "related_record_id": record.get("related_record_id"),
    }


def input_fingerprint(project: dict[str, Any], capability_card: dict[str, Any],
                      related_context: list[dict[str, Any]] | None = None) -> str:
    records = project.get("records", [])
    relevant = []
    for record in records:
        if isinstance(record, dict):
            relevant.append(_record_fingerprint(record))
    related: list[dict[str, Any]] = []
    for project_value in related_context or []:
        if not isinstance(project_value, dict):
            continue
        related.append({
            "id": project_value.get("id"), "title": project_value.get("title"),
            "relation": project_value.get("relation"),
            "records": [_record_fingerprint(record) for record in project_value.get("records", [])
                        if isinstance(record, dict)],
        })
    return sha256_text(json_text({"records": relevant, "capability": capability_card, "related": related}))


def build_messages(project: dict[str, Any], capability_card: dict[str, Any],
                   related_context: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    kind = project.get("kind") or "tender"
    system = """你是销售机会判断助手。你只能依据输入的项目正文和公司能力卡回答，不得编造产品、型号、价格、交期、资质或采购事实。必须只返回一个 JSON 对象，不要 Markdown。字段固定为：
decision（recommend|consider|verify|not_recommended）、summary、demand_summary、reasons（数组，每项含 text 和 evidence 数组；每条 evidence 必须含存在的 record_id 和正文原文 quote）、unknowns（字符串数组）、next_actions（字符串数组）、options（0 到 3 项；每项含 id、title、description、suitable_for、tradeoffs 字符串数组、unknowns 字符串数组、next_action）。理由要用通俗语言。quote 必须从对应 record_id 的 body_text 连续逐字复制，保留原文用字、标点和空格；不能改写、拼接不相邻句子，不能引用标题、facts、公司能力卡或另一条记录代替正文。优先选简短且完整的原文片段；原文不存在的信息只能列为待核实，不得作为证据。若输入是新闻或矿业权动态，只能输出核实方向，不能把推测写成采购事实，decision 应为 verify，options 可以为空。若资料不足，使用 verify/consider 并明确待核实内容。"""
    records = []
    for record in project.get("records", []):
        # Keep source text available for evidence while bounding a pathological
        # collector record to a reasonable request size.
        body = str(record.get("body_text") or "")
        if len(body) > 30000:
            body = body[:30000] + "\n[正文已截断，不能引用截断部分以外的内容]"
        records.append({
            "record_id": record.get("id"), "kind": record.get("kind"), "title": record.get("title"),
            "buyer": record.get("buyer"), "published_at": record.get("published_at"),
            "deadline_at": record.get("deadline_at"), "equipment": record.get("equipment"),
            "facts": record.get("facts"), "inferences": record.get("inferences"), "body_text": body,
        })
    value: dict[str, Any] = {
        "project": {"id": project.get("id"), "title": project.get("title"), "kind": kind,
                     "records": records},
        "company_capability_card": capability_card,
    }
    if related_context:
        # Related snapshots are supplied only when the database has an
        # explicit record reference or active human/project link.  They are
        # context for citations, never a replacement for the primary project.
        value["related_projects"] = related_context
    user = json_text(value)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def correction_messages(messages: list[dict[str, str]], error: AIError) -> list[dict[str, str]]:
    """Ask once for a fresh, valid response while retaining all source inputs."""
    return [*messages, {"role": "user", "content": (
        f"上一次输出未通过程序校验：{error}。请根据前面的同一份资料重新生成完整 JSON，"
        "不要解释错误，不要使用 Markdown。检查所有字段类型与 JSON 引号、逗号。"
        "每条 evidence 的 record_id 必须存在；quote 必须连续逐字复制该记录的 body_text，"
        "不得概括、拼接或替换原文。删去无法找到原文支持的理由；"
        "依据不足时使用 verify 并写清待核实事项，不得编造证据。"
    )}]


def extract_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("AI response did not contain message content") from exc
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise AIError("AI response content was empty")
    return content.strip()


def validate_judgment(payload: Any, project: dict[str, Any], model: str,
                      fingerprint: str, related_records: list[dict[str, Any]] | None = None,
                      forbidden_values: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Validate model output before it can enter the judgments table."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AIError("AI response content was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise AIError("AI response must be a JSON object")
    try:
        serialized_payload = json_text(payload)
    except (TypeError, ValueError) as exc:
        raise AIError("AI response contains unsupported values") from exc
    if any(isinstance(value, str) and len(value) >= 3 and value in serialized_payload
           for value in forbidden_values):
        raise AIError("AI response contains restricted credential")
    decision = payload.get("decision")
    if decision not in {"recommend", "consider", "verify", "not_recommended"}:
        raise AIError("invalid AI decision")
    if project.get("kind") == "news" and decision != "verify":
        raise AIError("news judgment must be verify")
    def required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str):
            raise AIError(f"invalid AI field: {name}")
        value = value.strip()
        if not value:
            raise AIError(f"invalid AI field: {name}")
        return value
    summary = required_text("summary")
    demand_summary = required_text("demand_summary")
    unknowns = payload.get("unknowns", [])
    next_actions = payload.get("next_actions", [])
    if not isinstance(unknowns, list) or not all(isinstance(x, str) for x in unknowns):
        raise AIError("invalid unknowns")
    if not isinstance(next_actions, list) or not all(isinstance(x, str) for x in next_actions):
        raise AIError("invalid next_actions")
    record_map: dict[int, dict[str, Any]] = {}
    for record in [*project.get("records", []), *(related_records or [])]:
        if not isinstance(record, dict) or record.get("id") is None:
            continue
        try:
            record_map[int(record["id"])] = record
        except (TypeError, ValueError):
            continue
    reasons = payload.get("reasons", [])
    if not isinstance(reasons, list):
        raise AIError("invalid reasons")
    normalized_reasons = []
    for reason in reasons:
        if not isinstance(reason, dict) or not isinstance(reason.get("text"), str) or not reason.get("text", "").strip():
            raise AIError("invalid reason")
        evidence = reason.get("evidence", [])
        if not isinstance(evidence, list):
            raise AIError("invalid evidence")
        clean_evidence = []
        for item in evidence:
            if not isinstance(item, dict):
                raise AIError("invalid evidence item")
            try:
                record_id = int(item.get("record_id"))
            except (TypeError, ValueError) as exc:
                raise AIError("invalid evidence record_id") from exc
            record = record_map.get(record_id)
            quote = item.get("quote")
            matched_quote = source_quote(quote, str(record.get("body_text") or "")) if record and isinstance(quote, str) else None
            if matched_quote is None:
                raise AIError(f"evidence quote does not match source record (record_id={record_id})")
            clean_evidence.append({"record_id": record_id, "quote": matched_quote})
        normalized_reasons.append({"text": reason["text"].strip(), "evidence": clean_evidence})
    if decision in {"recommend", "consider"}:
        if not normalized_reasons or not any(item["evidence"] for item in normalized_reasons):
            raise AIError("positive AI judgment requires source evidence")
    options = payload.get("options", [])
    if not isinstance(options, list) or len(options) > 3:
        raise AIError("options must contain 0 to 3 items")
    normalized_options = []
    option_ids = set()
    for option in options:
        if not isinstance(option, dict):
            raise AIError("invalid option")
        option_id = option.get("id")
        if not isinstance(option_id, str) or not option_id.strip() or option_id in option_ids:
            raise AIError("invalid option id")
        option_ids.add(option_id)
        fields = {}
        for name in ("title", "description", "suitable_for", "next_action"):
            if not isinstance(option.get(name), str):
                raise AIError(f"invalid option field: {name}")
            fields[name] = option[name].strip()
        for name in ("tradeoffs", "unknowns"):
            value = option.get(name, [])
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise AIError(f"invalid option field: {name}")
            fields[name] = [x.strip() for x in value]
        fields["id"] = option_id.strip()
        normalized_options.append(fields)
    if project.get("kind") == "news" and options:
        # A news lead may have verification actions but no product delivery plan.
        for option in normalized_options:
            material = f"{option['title']} {option['description']}"
            if any(word in material for word in ("供货方案", "报价方案", "采购方案", "交付方案")):
                raise AIError("news judgment contains a procurement option")
    return {
        "created_at": None,
        "model": model,
        "input_fingerprint": fingerprint,
        "stale": False,
        "decision": decision,
        "summary": summary,
        "demand_summary": demand_summary,
        "reasons": normalized_reasons,
        "unknowns": [x.strip() for x in unknowns],
        "next_actions": [x.strip() for x in next_actions],
        "options": normalized_options,
    }
