"""Public AI integration entry points.

The implementation is split by responsibility:

* errors owns safe error values and credential redaction.
* providers owns direct model adapters.
* runtime_bridge owns the Node/Pi SDK process protocol.
* prompts owns context shaping, prompts, and input fingerprints.
* validation owns model output and evidence validation.

This module keeps the established import surface for application callers while
making each implementation dependency point explicit.
"""
from .errors import (
    AIError,
    _runtime_error_message,
    _safe_error_code,
    _safe_error_metadata,
    _safe_tool_error,
    sanitize_ai_error,
)
from .providers import AIProvider, OpenAICompatibleProvider
from .prompts import (
    CONTEXT_TRUNCATION_NOTICE,
    MAX_CONTEXT_BODY_CHARS,
    bounded_context_body,
    build_messages,
    correction_evidence,
    correction_messages,
    input_fingerprint,
)
from .runtime_bridge import (
    DEFAULT_LIMITS,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_LINE_BYTES,
    PiSDKProvider,
    PiSDKResult,
    _node_candidates,
    _runner_path,
    _runtime_root,
    _safe_progress_event,
    resolve_sdk_runtime,
    sdk_ready,
)
from .validation import extract_content, validate_judgment

__all__ = [
    "AIError",
    "AIProvider",
    "OpenAICompatibleProvider",
    "PiSDKProvider",
    "PiSDKResult",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_OUTPUT_LINE_BYTES",
    "MAX_CONTEXT_BODY_CHARS",
    "CONTEXT_TRUNCATION_NOTICE",
    "DEFAULT_LIMITS",
    "bounded_context_body",
    "build_messages",
    "correction_evidence",
    "correction_messages",
    "extract_content",
    "input_fingerprint",
    "sanitize_ai_error",
    "validate_judgment",
    "resolve_sdk_runtime",
    "sdk_ready",
]
