from __future__ import annotations

import re
from typing import Any

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

def _safe_error_code(code: Any) -> str:
    if not isinstance(code, str):
        return "RUNTIME_ERROR"
    clean = re.sub(r"[^A-Za-z0-9_]", "", code).upper()[:64]
    return clean if clean in _ERROR_CODES else "RUNTIME_ERROR"


_ERROR_METADATA_ALIASES = {
    "position": "position", "pos": "position", "parse_position": "position",
    "line": "line", "column": "column", "col": "column",
    "eof": "eof", "at_eof": "eof", "polluted": "polluted",
    "diagnostic_path": "diagnostic_path", "diagnostics_path": "diagnostic_path",
    "http_status": "http_status",
    "finish_reason": "finish_reason", "parser": "parser", "stage": "stage",
    "retryable": "retryable", "tool_executed": "tool_executed",
    "tool_execution_confirmed": "tool_execution_confirmed",
    "tool_execution_state": "tool_execution_state",
}


def _safe_error_metadata(value: Any) -> dict[str, Any]:
    """Keep only bounded diagnostics; never retain a raw model response."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, output_key in _ERROR_METADATA_ALIASES.items():
        if key not in value or output_key in result:
            continue
        candidate = value[key]
        if isinstance(candidate, bool):
            result[output_key] = candidate
        elif isinstance(candidate, int):
            result[output_key] = max(0, min(candidate, 2_000_000_000))
        elif isinstance(candidate, str):
            # Paths and parser labels are useful to operators, but provider
            # payloads and arbitrary control characters are not.
            allowed = set("._:/ ()[]\\-")
            safe = "".join(
                char for char in candidate
                if char in allowed or "0" <= char <= "9" or "A" <= char <= "Z"
                or "a" <= char <= "z" or "\u4e00" <= char <= "\u9fff"
            )
            if safe:
                result[output_key] = safe[:240]
    return result


_RUNTIME_ERROR_MESSAGES = {
    "TOOL_ARGUMENT_JSON": "工具参数格式错误，工具尚未执行",
    "TOOL_SCHEMA": "工具参数不符合工具模式，工具尚未执行",
    "TOOL_PARSER_ERROR": "工具参数解析失败，工具尚未执行",
    "STREAM_INTERRUPTED": "模型流中断，运行时未确认工具已执行",
    "MODEL_FORMAT_ERROR": "模型输出格式错误，报告内容不是有效 JSON",
}

# Keep the durable descriptions explicit: a tool-argument classification
# means that particular tool call did not run, while an interrupted model
# response means this result is not saved.
_RUNTIME_ERROR_MESSAGES.update({
    "TOOL_ARGUMENT_JSON": "\u8be5\u5de5\u5177\u8c03\u7528\u53c2\u6570 JSON \u65e0\u6548\uff0c\u8be5\u5de5\u5177\u8c03\u7528\u672a\u6267\u884c",
    "TOOL_SCHEMA": "\u8be5\u5de5\u5177\u8c03\u7528\u53c2\u6570\u4e0d\u7b26\u5408\u5de5\u5177 schema\uff0c\u8be5\u5de5\u5177\u8c03\u7528\u672a\u6267\u884c",
    "TOOL_PARSER_ERROR": "\u8be5\u5de5\u5177\u8c03\u7528\u53c2\u6570\u89e3\u6790\u5931\u8d25\uff0c\u8be5\u5de5\u5177\u8c03\u7528\u672a\u6267\u884c",
    "STREAM_INTERRUPTED": "\u6a21\u578b\u54cd\u5e94\u4e2d\u65ad\uff0c\u672c\u6b21\u7ed3\u679c\u672a\u4fdd\u5b58",
    "MODEL_FORMAT_ERROR": "\u6a21\u578b\u8f93\u51fa\u683c\u5f0f\u9519\u8bef\uff0c\u62a5\u544a\u5185\u5bb9\u4e0d\u662f\u6709\u6548 JSON",
})


def _runtime_error_message(code: str, budget: Any) -> str:
    if code == "OUTPUT_TOKEN_LIMIT":
        return f"模型输出达到token上限（{budget}，含思考与答案），请缩短报告或调整输出预算后重试"
    return _RUNTIME_ERROR_MESSAGES.get(code, f"Pi SDK运行失败（{code}）")


def _safe_tool_error(exc: BaseException) -> str:
    """Map a server tool failure to the runner's lower-case safe enum."""
    code = getattr(exc, "code", None)
    if code in {"invalid_request", "research_limit", "source_not_found", "source_unreadable",
                "relation_unconfirmed", "record_not_in_project", "not_configured", "timeout"}:
        return code
    if isinstance(exc, AIError):
        text = str(exc)
        if "无权限" in text:
            return "record_not_in_project"
        if "参数" in text:
            return "invalid_request"
        if "未就绪" in text or "不可用" in text:
            return "not_configured"
        if "不支持" in text:
            return "unsupported"
        if "超时" in text:
            return "timeout"
    return "internal"


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
    def __init__(self, message: str, *, code: str | None = None,
                 metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = _safe_error_code(code) if code is not None else "RUNTIME_ERROR"
        self.metadata = _safe_error_metadata(metadata)
