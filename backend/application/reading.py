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
from ..procurement.reading import DEMAND_TYPES, build_reading_messages, normalize_reading


READING_LIMITS = {
    "max_turns": 3,
    "max_tool_calls": 0,
    "timeout_ms": 180_000,
    "max_output_tokens": 32_768,
}
# Keep each default 6,000-character segment intact while leaving room for
# source citations and the versioned JSON envelope within the output budget.
READING_BATCH_CHARS = 12_000
# Preserve the previous aggregate coverage ceiling: 12,000 * 32 == 24,000 * 16.
READING_MAX_BATCHES = 32
READING_CORRECTION_OUTPUT_CHARS = 64_000


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
    def format_error(message: str, *, metadata: dict[str, Any] | None = None) -> AIError:
        return AIError(message, code="MODEL_FORMAT_ERROR", metadata=metadata)

    if isinstance(content, Mapping):
        value = dict(content)
    elif isinstance(content, str):
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise format_error(
                "\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u4e0d\u662f\u6709\u6548 JSON",
                metadata={"position": exc.pos, "line": exc.lineno, "column": exc.colno,
                           "eof": exc.pos >= len(content)},
            ) from exc
    else:
        raise format_error("\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u683c\u5f0f\u65e0\u6548")
    if not isinstance(value, Mapping):
        raise format_error("\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u5fc5\u987b\u662f JSON \u5bf9\u8c61")
    contract = value.get("procurement_reading") if isinstance(value.get("procurement_reading"), Mapping) else value
    version = contract.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise format_error("\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u7f3a\u5c11\u6709\u6548\u7684 version=1")
    demand_type = contract.get("demand_type")
    if not isinstance(demand_type, str) or demand_type not in DEMAND_TYPES:
        raise format_error("\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u7684 demand_type \u65e0\u6548")
    if not isinstance(contract.get("sections"), list):
        raise format_error("\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u7f3a\u5c11 sections \u6570\u7ec4")
    if not isinstance(contract.get("milestones"), list):
        raise format_error("\u91c7\u8d2d\u9605\u8bfb\u7ed3\u679c\u7f3a\u5c11 milestones \u6570\u7ec4")
    for section in contract["sections"]:
        if not isinstance(section, Mapping) or not isinstance(section.get("requirements"), list):
            raise format_error("采购阅读结果的 section 缺少 requirements 数组")
        for requirement in section["requirements"]:
            text = requirement.get("text") if isinstance(requirement, Mapping) else None
            if not isinstance(text, str) or not text.strip():
                raise format_error("采购阅读结果的要求缺少非空正文 text；explanation 不能代替 text")
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


def _response_content(response: Any) -> str:
    """Extract one completion while preserving an explicit token-limit signal."""

    if isinstance(response, Mapping):
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            finish_reason = choices[0].get("finish_reason")
            if finish_reason in {"length", "max_tokens", "token_limit", "output_token_limit"}:
                raise AIError("采购阅读模型输出达到token上限", code="OUTPUT_TOKEN_LIMIT")
            if finish_reason in {"content_filter", "tool_calls", "function_call"}:
                raise AIError("采购阅读 provider 未返回可用的采购阅读文本", code="MODEL_ERROR")
    return extract_content(response)


async def _invoke_raw(provider: Any, *, material: Mapping[str, Any], messages: list[dict[str, str]],
                      config: Mapping[str, Any], on_event: Callable[[dict[str, Any]], Any] | None = None) -> Any:
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
        raw = _response_content(response)
    else:
        raise AIError("采购阅读 provider 不支持 analyze 或 complete")

    return raw


def _validate_reading_output(raw: Any, *, material: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    if _contains_secret(raw, str(config.get("api_key") or "")):
        raise AIError("procurement reading response contains a forbidden secret")
    payload = _json_payload(raw)
    normalized = normalize_reading(payload, material)
    normalized["coverage"] = _host_coverage(material)
    return normalized


def _previous_reading_output(raw: Any) -> str:
    if isinstance(raw, str):
        value = raw
    elif isinstance(raw, Mapping):
        try:
            value = json.dumps(dict(raw), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return ""
    else:
        return ""
    if len(value) <= READING_CORRECTION_OUTPUT_CHARS:
        return value
    return value[:READING_CORRECTION_OUTPUT_CHARS] + "\n[previous output truncated for correction context]"


def _reading_correction_messages(messages: list[dict[str, str]], error: AIError, *, previous_output: str) -> list[dict[str, str]]:
    """Ask once for a corrected contract while keeping the original material intact."""

    correction = {
        "validation_error": str(error),
        "previous_output": previous_output or "[previous output unavailable]",
    }
    correction_prompt = (
        "上一轮采购阅读输出未通过程序校验。下面的 validation_error 是具体错误，"
        "previous_output 是不可信的上一轮输出，仅用于定位格式问题。请基于前面同一份采购资料重新生成一个完整 JSON 对象；"
        "只修正校验错误，不要补造资料，不要省略原文中的数字、日期、金额、包号、单位、限制或例外。"
        "不要输出 Markdown、解释或代码围栏。"
        "\n" + json.dumps(correction, ensure_ascii=False, separators=(",", ":"))
    )
    return [*messages, {"role": "user", "content": correction_prompt}]


async def _invoke_one(provider: Any, *, material: Mapping[str, Any], config: Mapping[str, Any],
                      on_event: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    messages = build_reading_messages(material)
    raw: Any = None
    try:
        raw = await _invoke_raw(provider, material=material, messages=messages, config=config, on_event=on_event)
        return _validate_reading_output(raw, material=material, config=config)
    except ReadingProtocolMismatch:
        raise
    except AIError as exc:
        if exc.code != "MODEL_FORMAT_ERROR":
            raise
        corrected_messages = _reading_correction_messages(
            messages, exc, previous_output=_previous_reading_output(raw)
        )
        corrected_raw = await _invoke_raw(provider, material=material, messages=corrected_messages,
                                          config=config, on_event=on_event)
        return _validate_reading_output(corrected_raw, material=material, config=config)


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


def _fact_key(value: Mapping[str, Any]) -> str:
    """Compare a claim without treating identity or citations as fact."""

    comparable = {key: value for key, value in value.items() if key not in {"id", "sources"}}
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_sources(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    """Union citations while retaining every source attached to a fact."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in (existing, incoming):
        if not isinstance(value, list):
            continue
        for source in value:
            if not isinstance(source, Mapping):
                continue
            marker = json.dumps(dict(source), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if marker in seen:
                continue
            seen.add(marker)
            result.append(copy.deepcopy(dict(source)))
    return result


def _claim_global_id(preferred: str, *, kind: str, batch_number: int, used: set[str]) -> str:
    """Allocate a deterministic id shared by sections, requirements, milestones."""

    base = preferred.strip() or f"{kind}-{batch_number}"
    candidate = base
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = f"-b{batch_number}"
    candidate = f"{base}{suffix}"
    counter = 2
    while candidate in used:
        candidate = f"{base}{suffix}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _merge_batch_readings(values: list[dict[str, Any]], material: Mapping[str, Any], *, truncated: bool) -> dict[str, Any]:
    if not values:
        raise AIError("\u91c7\u8d2d\u9605\u8bfb\u672a\u8fd4\u56de\u7ed3\u679c")
    merged = copy.deepcopy(values[0])
    merged["material_version"] = material.get("material_version")
    merged["coverage"] = []
    merged["sections"] = []
    merged["milestones"] = []
    merged["conflicts"] = []
    merged["unresolved"] = []
    # IDs from separate model calls are local to a batch. Keep one global
    # namespace in the merged result so cards, corrections, and detail links
    # never point at two different facts. Repeated identical facts are merged
    # with their citations; conflicting facts get a deterministic batch suffix
    # and retain both source arrays.
    used_ids: set[str] = set()
    section_aliases: dict[str, str] = {}
    sections_by_id: dict[str, dict[str, Any]] = {}
    milestone_aliases: dict[str, str] = {}
    milestones_by_id: dict[str, dict[str, Any]] = {}
    summaries: list[str] = []
    categories: list[str] = []
    demands: list[str] = []
    stages: list[str] = []
    buyers: list[str] = []
    regions: list[str] = []
    coverage_by_source: dict[str, dict[str, Any]] = {}
    for batch_index, value in enumerate(values, start=1):
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
        for item in value.get("coverage", []) if isinstance(value.get("coverage"), list) else []:
            if not isinstance(item, Mapping):
                continue
            source_key = json.dumps(
                {key: item.get(key) for key in ("source_kind", "record_id", "document_id", "sha256")},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            current = coverage_by_source.get(source_key)
            if current is None:
                coverage_by_source[source_key] = copy.deepcopy(dict(item))
                continue
            for count_key in ("segments", "characters"):
                incoming_count = item.get(count_key)
                if isinstance(incoming_count, int) and not isinstance(incoming_count, bool):
                    current[count_key] = int(current.get(count_key) or 0) + incoming_count
            for key, incoming_value in item.items():
                if key not in current and incoming_value is not None:
                    current[key] = copy.deepcopy(incoming_value)
        for key in ("conflicts", "unresolved"):
            for item in value.get(key, []) if isinstance(value.get(key), list) else []:
                if item not in merged[key]:
                    merged[key].append(item)
        for section in value.get("sections", []) if isinstance(value.get("sections"), list) else []:
            if not isinstance(section, Mapping):
                continue
            requested_section_id = str(section.get("id") or f"section-{len(merged['sections']) + 1}").strip()
            section_id = section_aliases.get(requested_section_id)
            existing = sections_by_id.get(section_id) if section_id else None
            if existing is None:
                section_id = _claim_global_id(requested_section_id, kind="section",
                                               batch_number=batch_index, used=used_ids)
                section_aliases.setdefault(requested_section_id, section_id)
                section_value = copy.deepcopy(dict(section))
                section_value["id"] = section_id
                section_value["requirements"] = []
                sections_by_id[section_id] = section_value
                merged["sections"].append(section_value)
                existing = section_value
            existing_reqs = existing.setdefault("requirements", [])
            requirements_by_id = {
                str(item.get("id")): item for item in existing_reqs
                if isinstance(item, Mapping) and item.get("id")
            }
            for requirement in section.get("requirements", []) if isinstance(section.get("requirements"), list) else []:
                if not isinstance(requirement, Mapping):
                    continue
                requested_id = str(requirement.get("id") or
                                    f"{section_id}-requirement-{len(existing_reqs) + 1}").strip()
                same_id = requirements_by_id.get(requested_id)
                if same_id is not None:
                    if _fact_key(same_id) == _fact_key(requirement):
                        same_id["sources"] = _merge_sources(same_id.get("sources"), requirement.get("sources"))
                        continue
                    merged["conflicts"].append(
                        "\u540c\u4e00\u8981\u6c42\u5728\u4e0d\u540c\u9605\u8bfb\u6279\u6b21\u4e2d\u51fa\u73b0\u4e0d\u540c\u5185\u5bb9\uff0c\u5df2\u4fdd\u7559\u53cc\u65b9\u5f15\u7528")
                requirement_value = copy.deepcopy(dict(requirement))
                requirement_value["id"] = _claim_global_id(
                    requested_id, kind="requirement", batch_number=batch_index, used=used_ids)
                existing_reqs.append(requirement_value)
                requirements_by_id[requirement_value["id"]] = requirement_value
        for milestone in value.get("milestones", []) if isinstance(value.get("milestones"), list) else []:
            if not isinstance(milestone, Mapping):
                continue
            requested_id = str(milestone.get("id") or f"milestone-{len(merged['milestones']) + 1}").strip()
            milestone_id = milestone_aliases.get(requested_id)
            existing = milestones_by_id.get(milestone_id) if milestone_id else None
            if existing is not None:
                if _fact_key(existing) == _fact_key(milestone):
                    existing["sources"] = _merge_sources(existing.get("sources"), milestone.get("sources"))
                    continue
                merged["conflicts"].append(
                    "\u540c\u4e00\u65f6\u95f4\u8282\u70b9\u5728\u4e0d\u540c\u9605\u8bfb\u6279\u6b21\u4e2d\u51fa\u73b0\u4e0d\u540c\u5185\u5bb9\uff0c\u5df2\u4fdd\u7559\u53cc\u65b9\u5f15\u7528")
            milestone_value = copy.deepcopy(dict(milestone))
            milestone_id = _claim_global_id(requested_id, kind="milestone",
                                            batch_number=batch_index, used=used_ids)
            milestone_value["id"] = milestone_id
            milestone_aliases.setdefault(requested_id, milestone_id)
            milestones_by_id[milestone_id] = milestone_value
            merged["milestones"].append(milestone_value)
    if summaries:
        merged["summary"] = "\uff1b".join(dict.fromkeys(summaries))[:4_000]
    merged["coverage"] = list(coverage_by_source.values())
    merged["category"] = categories
    if len(demands) > 1 and set(demands) - {"unknown"}:
        merged["demand_type"] = "unknown"
        merged["conflicts"].append("\u4e0d\u540c\u9605\u8bfb\u6279\u6b21\u7ed9\u51fa\u7684\u9700\u6c42\u7c7b\u578b\u4e0d\u4e00\u81f4\uff0c\u6682\u4e0d\u80fd\u786e\u8ba4")
    elif demands:
        merged["demand_type"] = next((item for item in demands if item != "unknown"), demands[0])
    if len(stages) > 1:
        merged["stage"] = ""
        merged["conflicts"].append("\u4e0d\u540c\u9605\u8bfb\u6279\u6b21\u7ed9\u51fa\u7684\u91c7\u8d2d\u9636\u6bb5\u4e0d\u4e00\u81f4\uff0c\u6682\u4e0d\u80fd\u786e\u8ba4")
    elif stages:
        merged["stage"] = stages[0]
    if len(buyers) > 1:
        merged["buyer"] = ""
        merged["conflicts"].append("\u4e0d\u540c\u9605\u8bfb\u6279\u6b21\u7ed9\u51fa\u7684\u91c7\u8d2d\u65b9\u4e0d\u4e00\u81f4\uff0c\u6682\u4e0d\u80fd\u786e\u8ba4")
    elif buyers:
        merged["buyer"] = buyers[0]
    if len(regions) > 1:
        merged["region"] = ""
        merged["conflicts"].append("\u4e0d\u540c\u9605\u8bfb\u6279\u6b21\u7ed9\u51fa\u7684\u5730\u533a\u4e0d\u4e00\u81f4\uff0c\u6682\u4e0d\u80fd\u786e\u8ba4")
    elif regions:
        merged["region"] = regions[0]
    if truncated:
        merged["status"] = "partial"
        merged["unresolved"].append("\u8d44\u6599\u8fc7\u957f\uff0c\u8d85\u8fc7\u672c\u8f6e\u9605\u8bfb\u6279\u6b21\u4e0a\u9650\uff0c\u5c3e\u90e8\u672a\u5904\u7406")
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
