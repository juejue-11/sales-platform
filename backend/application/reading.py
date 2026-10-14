"""Application boundary for procurement reading.

This module owns model invocation only.  It deliberately does not persist
anything and can therefore be exercised with an injected provider in a
temporary database test.  The normal service selects Pi SDK or single-call
providers before calling it.
"""

from __future__ import annotations

import inspect
import json
import copy
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..analysis.ai import AIError, OpenAICompatibleProvider, PiSDKProvider, PiSDKResult, extract_content
from ..procurement.reading import build_reading_messages, normalize_reading


READING_LIMITS = {
    "max_turns": 3,
    "max_tool_calls": 0,
    "timeout_ms": 180_000,
    "max_output_tokens": 12_000,
}
READING_BATCH_CHARS = 24_000
READING_MAX_BATCHES = 16


class ReadingProtocolMismatch(AIError):
    """An older Pi runner returned the judgment protocol for a reading call.

    The content stays in memory so the existing judgment path can consume it;
    it is never persisted as a reading error or exposed in an operator status.
    """

    def __init__(self, content: str, usage: Mapping[str, int] | None = None):
        super().__init__("reading runtime task protocol is unavailable")
        self.content = content
        self.usage = dict(usage or {})


def _json_payload(content: Any) -> Any:
    if isinstance(content, Mapping):
        return dict(content)
    if not isinstance(content, str):
        raise AIError("采购阅读结果协议无效")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIError("采购阅读结果不是有效 JSON") from exc
    if not isinstance(value, Mapping):
        raise AIError("采购阅读结果必须是 JSON 对象")
    return value


def _contains_secret(value: Any, secret: str) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, Mapping):
        return any(_contains_secret(key, secret) or _contains_secret(item, secret)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item, secret) for item in value)
    return False


async def _invoke_explicit_reader(reader: Any, *, material: Mapping[str, Any], messages: list[dict[str, str]],
                                  config: Mapping[str, Any], on_event: Callable[[dict[str, Any]], Any] | None) -> Any:
    kwargs = {
        "material": material, "messages": messages,
        "base_url": config.get("base_url", ""), "model": config.get("model", ""),
        "api_key": config.get("api_key", ""), "limits": dict(READING_LIMITS), "on_event": on_event,
    }
    try:
        parameters = inspect.signature(reader).parameters
    except (TypeError, ValueError):
        parameters = {}
    if not parameters:
        value = reader()
    else:
        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        value = reader(**({key: value for key, value in kwargs.items() if accepts_kwargs or key in parameters}))
    if isinstance(value, Awaitable):
        value = await value
    return value


async def _invoke_one(provider: Any, *, material: Mapping[str, Any], config: Mapping[str, Any],
                      on_event: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    messages = build_reading_messages(material)
    explicit_reader = getattr(provider, "read_procurement", None) or getattr(provider, "read", None)
    if callable(explicit_reader):
        raw = await _invoke_explicit_reader(explicit_reader, material=material, messages=messages,
                                             config=config, on_event=on_event)
    elif callable(getattr(provider, "analyze", None)):
        # PiSDKProvider and Pi-compatible injected providers use the same
        # bounded duplex protocol as market analysis.  No tool callback is
        # supplied, so the runtime cannot fetch untrusted material outside the
        # versioned package.
        analyze = provider.analyze
        kwargs: dict[str, Any] = {
            "base_url": config.get("base_url", ""), "model": config.get("model", ""),
            "api_key": config.get("api_key", ""), "messages": messages,
            "context": {"project": {"kind": "procurement_reading", "material_version": material.get("material_version")},
                         "company_capability_card": {}, "related_projects": [],
                         "reading_material": material},
            "limits": dict(READING_LIMITS), "on_event": on_event, "task_kind": "reading",
        }
        try:
            parameters = inspect.signature(analyze).parameters
        except (TypeError, ValueError):
            parameters = {}
        if parameters and not any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            kwargs = {key: value for key, value in kwargs.items() if key in parameters}
        raw = analyze(**kwargs)
        if isinstance(raw, Awaitable):
            raw = await raw
        if isinstance(raw, PiSDKResult):
            if raw.task_kind is None:
                # Pre-reading runtimes have no task marker and would otherwise
                # consume one model turn before the normal judgment call.
                # Preserve their response for the legacy judgment validator.
                raise ReadingProtocolMismatch(raw.content, raw.usage)
            if raw.task_kind != "reading":
                raise AIError("reading runtime returned the wrong task")
        if hasattr(raw, "content"):
            raw = raw.content
        elif isinstance(raw, Mapping) and isinstance(raw.get("content"), str):
            raw = raw["content"]
    elif callable(getattr(provider, "complete", None)):
        response = provider.complete(base_url=str(config.get("base_url") or ""), model=str(config.get("model") or ""),
                                     api_key=str(config.get("api_key") or ""), messages=messages)
        if isinstance(response, Awaitable):
            response = await response
        raw = extract_content(response)
    else:
        raise AIError("采购阅读 provider 不支持 analyze 或 complete")
    payload = _json_payload(raw)
    if _contains_secret(payload, str(config.get("api_key") or "")):
        raise AIError("procurement reading response contains a forbidden secret")
    normalized = normalize_reading(payload, material)
    normalized["coverage"] = _host_coverage(material)
    return normalized


def _batched_material(material: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return bounded packages whose segment text is fully accounted for."""

    segments = [segment for segment in material.get("segments", []) if isinstance(segment, Mapping)]
    if not segments:
        return [dict(material)]
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_chars = 0
    for segment in segments:
        size = len(str(segment.get("text") or ""))
        if current and current_chars + size > READING_BATCH_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += size
    if current:
        batches.append(current)
    result: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(batches[:READING_MAX_BATCHES]):
        source_ids = {str(segment.get("source_id") or "") for segment in batch}
        value = copy.deepcopy(dict(material))
        value["segments"] = [dict(segment) for segment in batch]
        value["sources"] = []
        segments_by_source: dict[str, list[Mapping[str, Any]]] = {}
        for segment in batch:
            segments_by_source.setdefault(str(segment.get("source_id") or ""), []).append(segment)
        for source in material.get("sources", []):
            if not isinstance(source, Mapping) or str(source.get("source_id") or "") not in source_ids:
                continue
            source_value = dict(source)
            source_segments = segments_by_source.get(str(source.get("source_id") or ""), [])
            source_value["text"] = "".join(str(segment.get("text") or "") for segment in source_segments)
            if isinstance(source.get("page_texts"), list):
                page_segments: dict[Any, list[str]] = {}
                for segment in source_segments:
                    page_segments.setdefault(segment.get("page"), []).append(str(segment.get("text") or ""))
                source_value["page_texts"] = [
                    {**dict(page), "text": "".join(page_segments.get(page.get("page"), []))}
                    for page in source.get("page_texts", [])
                    if isinstance(page, Mapping) and page.get("page") in page_segments
                ]
            value["sources"].append(source_value)
        value["coverage"] = [dict(item) for item in material.get("coverage", [])
                              if isinstance(item, Mapping) and (
                                  f"{item.get('source_kind')}-{item.get('document_id') or item.get('record_id')}" in source_ids
                                  or any(str(source.get("source_id") or "") in source_ids
                                         and str(source.get("source_kind")) == str(item.get("source_kind"))
                                         and source.get("record_id") == item.get("record_id")
                                         for source in material.get("sources", []) if isinstance(source, Mapping)))]
        value["batch"] = {"index": batch_index + 1, "total": min(len(batches), READING_MAX_BATCHES)}
        result.append(value)
    if len(batches) > READING_MAX_BATCHES:
        # The caller adds the exact unresolved tail marker; do not hide it in
        # a model claim that the full file was read.
        result.append({"_unread_tail": True, "remaining_segments": batches[READING_MAX_BATCHES:]})
    return result


def _host_coverage(material: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Count only segments actually sent to the model in this batch."""

    by_source: dict[str, dict[str, int]] = {}
    for segment in material.get("segments", []) if isinstance(material.get("segments"), list) else []:
        if not isinstance(segment, Mapping):
            continue
        source_id = str(segment.get("source_id") or "")
        entry = by_source.setdefault(source_id, {"segments": 0, "characters": 0})
        entry["segments"] += 1
        entry["characters"] += len(str(segment.get("text") or ""))
    result: list[dict[str, Any]] = []
    for source in material.get("sources", []) if isinstance(material.get("sources"), list) else []:
        if not isinstance(source, Mapping):
            continue
        counts = by_source.get(str(source.get("source_id") or ""))
        if counts is None:
            continue
        value = {key: source.get(key) for key in ("source_kind", "record_id", "document_id", "sha256")
                 if source.get(key) is not None}
        value.update(counts)
        value["status"] = "model_processed"
        result.append(value)
    return result


def _merge_batch_readings(values: list[dict[str, Any]], material: Mapping[str, Any], *, truncated: bool) -> dict[str, Any]:
    if not values:
        raise AIError("采购阅读未返回结果")
    merged = copy.deepcopy(values[0])
    merged["material_version"] = material.get("material_version")
    merged["coverage"] = []
    merged["sections"] = []
    merged["milestones"] = []
    merged["conflicts"] = []
    merged["unresolved"] = []
    seen_sections: set[str] = set()
    seen_milestones: set[str] = set()
    summaries: list[str] = []
    categories: list[str] = []
    demands: list[str] = []
    stages: list[str] = []
    buyers: list[str] = []
    regions: list[str] = []
    for value in values:
        if value.get("summary"):
            summaries.append(str(value["summary"]))
        raw_category = value.get("category") if isinstance(value.get("category"), list) else []
        for item in raw_category:
            text_value = str(item).strip()
            if text_value and text_value not in categories:
                categories.append(text_value)
        for target, key in ((demands, "demand_type"), (stages, "stage"),
                            (buyers, "buyer"), (regions, "region")):
            text_value = str(value.get(key) or "").strip()
            if text_value and text_value not in target:
                target.append(text_value)
        for key in ("coverage", "conflicts", "unresolved"):
            for item in value.get(key, []) if isinstance(value.get(key), list) else []:
                if item not in merged[key]:
                    merged[key].append(item)
        for section in value.get("sections", []) if isinstance(value.get("sections"), list) else []:
            if not isinstance(section, Mapping):
                continue
            section_value = dict(section)
            section_id = str(section_value.get("id") or f"section-{len(merged['sections']) + 1}")
            if section_id in seen_sections:
                existing = next((item for item in merged["sections"] if item.get("id") == section_id), None)
                if existing is not None:
                    existing_reqs = existing.setdefault("requirements", [])
                    for requirement in section_value.get("requirements", []) if isinstance(section_value.get("requirements"), list) else []:
                        if requirement not in existing_reqs:
                            existing_reqs.append(requirement)
                continue
            seen_sections.add(section_id)
            merged["sections"].append(section_value)
        for milestone in value.get("milestones", []) if isinstance(value.get("milestones"), list) else []:
            if not isinstance(milestone, Mapping):
                continue
            milestone_value = dict(milestone)
            milestone_id = str(milestone_value.get("id") or f"milestone-{len(merged['milestones']) + 1}")
            if milestone_id in seen_milestones:
                existing = next((item for item in merged["milestones"] if item.get("id") == milestone_id), None)
                if existing is not None and milestone_value != existing and milestone_value not in merged["milestones"]:
                    merged["conflicts"].append(f"milestone {milestone_id} has conflicting values across reading batches")
                    milestone_value["id"] = f"{milestone_id}-conflict-{len(merged['milestones']) + 1}"
                    merged["milestones"].append(milestone_value)
                continue
            seen_milestones.add(milestone_id)
            merged["milestones"].append(milestone_value)
    if summaries:
        merged["summary"] = "；".join(dict.fromkeys(summaries))[:4_000]
    merged["category"] = categories
    if len(demands) > 1 and set(demands) - {"unknown"}:
        merged["demand_type"] = "unknown"
        merged["conflicts"].append("demand_type differs across reading batches")
    elif demands:
        merged["demand_type"] = next((item for item in demands if item != "unknown"), demands[0])
    if len(stages) > 1:
        merged["stage"] = ""
        merged["conflicts"].append("stage differs across reading batches")
    elif stages:
        merged["stage"] = stages[0]
    if len(buyers) > 1:
        merged["buyer"] = ""
        merged["conflicts"].append("buyer differs across reading batches")
    elif buyers:
        merged["buyer"] = buyers[0]
    if len(regions) > 1:
        merged["region"] = ""
        merged["conflicts"].append("region differs across reading batches")
    elif regions:
        merged["region"] = regions[0]
    if truncated:
        merged["status"] = "partial"
        merged["unresolved"].append("资料过长，超过本轮阅读批次上限，尾部未处理")
    elif any(value.get("status") == "partial" for value in values):
        merged["status"] = "partial"
    return merged


async def invoke_reading(provider: Any, *, material: Mapping[str, Any], config: Mapping[str, Any],
                         on_event: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Run bounded model calls for every available segment and merge results."""

    batches = _batched_material(material)
    values: list[dict[str, Any]] = []
    truncated = False
    for batch in batches:
        if batch.get("_unread_tail"):
            truncated = True
            continue
        values.append(await _invoke_one(provider, material=batch, config=config, on_event=on_event))
    return _merge_batch_readings(values, material, truncated=truncated)


__all__ = ["READING_BATCH_CHARS", "READING_LIMITS", "READING_MAX_BATCHES", "ReadingProtocolMismatch",
           "invoke_reading"]
