"""Pure procurement material reading contract.

The reading module deliberately knows nothing about SQLite, HTTP clients, or
the application service.  It turns a versioned procurement material package
into bounded reading segments, builds the model prompt, and validates the
model's structured explanation against the supplied source text.  Persistence
and model selection stay in ``backend.application.service``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..core.utils import json_text, sha256_text
from .dates import normalize_timestamp, timestamp_supported


READING_VERSION = 1
READING_STATUSES = {"pending", "complete", "partial", "failed", "not_configured"}
DEMAND_TYPES = {"confirmed", "lead", "unknown"}
MILESTONE_KINDS = {"document_access", "registration", "question", "submission", "opening", "other"}
# Stable values consumed by the workbench filter.  Keep display labels in the
# client; a free-form model phrase would make filtering and sorting brittle.
READING_STAGES = {
    "discovery", "confirmed", "registration", "document_access", "question",
    "bidding", "submission", "opening", "result", "finished",
}
SOURCE_KINDS = {"announcement", "attachment"}
DEFAULT_SEGMENT_CHARS = 6_000
MAX_SEGMENTS = 1_000
MAX_SOURCE_TEXT = 500_000


def _text(value: Any, limit: int = 20_000) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def segment_text(text: Any, *, max_chars: int = DEFAULT_SEGMENT_CHARS) -> list[dict[str, Any]]:
    """Split text into contiguous, non-overlapping segments without loss.

    A segment boundary is moved to nearby whitespace or Chinese punctuation
    when possible.  The returned ``start``/``end`` offsets refer to the exact
    original string, so concatenating ``text`` from all segments reproduces
    the input byte-for-byte.  Empty input produces no segment.
    """

    value = str(text or "")
    try:
        size = max(128, min(int(max_chars), 32_000))
    except (TypeError, ValueError):
        size = DEFAULT_SEGMENT_CHARS
    if not value:
        return []
    result: list[dict[str, Any]] = []
    start = 0
    length = len(value)
    while start < length and len(result) < MAX_SEGMENTS:
        end = min(length, start + size)
        if end < length:
            # Prefer a punctuation/line/space boundary in the last quarter of
            # the segment.  The boundary itself remains part of the segment.
            window_start = start + max(1, int(size * 0.70))
            candidates = [
                value.rfind(mark, window_start, end)
                for mark in ("\n", "。", "；", ";", "！", "？", "!", "?", " ", "\t")
            ]
            boundary = max(candidates)
            if boundary >= window_start:
                end = boundary + 1
        if end <= start:
            end = min(length, start + size)
        result.append({"id": f"segment-{len(result) + 1}", "start": start, "end": end,
                       "text": value[start:end]})
        start = end
    if start < length:
        # This is only reachable for pathological input above MAX_SEGMENTS.
        # Keep the uncovered tail visible instead of claiming complete
        # coverage.
        result.append({"id": f"segment-{len(result) + 1}", "start": start, "end": length,
                       "text": value[start:]})
    return result


def _source_ref(*, source_kind: str, record_id: Any, document_id: Any = None,
                page: Any = None, quote: Any = "", sha256: Any = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "source_kind": source_kind if source_kind in SOURCE_KINDS else "announcement",
        "record_id": _int(record_id),
        "quote": _text(quote, 50_000),
    }
    if document_id:
        value["document_id"] = _text(document_id, 200)
    page_value = _int(page)
    if page_value and page_value > 0:
        value["page"] = page_value
    if sha256:
        value["sha256"] = _text(sha256, 128)
    return value


def _page_fragments(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    pages = document.get("page_texts")
    result: list[dict[str, Any]] = []
    if isinstance(pages, list):
        for raw in pages:
            if not isinstance(raw, Mapping):
                continue
            text = str(raw.get("text") or "")[:200_000]
            if not text:
                continue
            page = _int(raw.get("page")) or 1
            result.append({"page": max(1, page), "text": text})
    if result:
        return result
    text = str(document.get("text") or "")[:MAX_SOURCE_TEXT]
    return [{"page": None, "text": text}] if text else []


def material_version(records: Iterable[Mapping[str, Any]], documents: Iterable[Mapping[str, Any]],
                    corrections: Iterable[Mapping[str, Any]] = ()) -> str:
    """Return a stable semantic version for the exact material available.

    Fetch timestamps are intentionally ignored.  Document bytes, current
    record versions, access/error state, and correction audit entries are part
    of the identity so a reading cannot silently survive changed evidence.
    """

    record_values = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        body = str(record.get("body_text") or "")
        record_values.append({
            "id": _int(record.get("id")),
            "version": record.get("current_version"),
            "kind": record.get("kind"),
            "body_sha256": sha256_text(body),
            "body_length": len(body),
            "title": record.get("title"),
            "buyer": record.get("buyer"),
            "published_at": record.get("published_at"),
            "deadline_at": record.get("deadline_at"),
        })
    document_values = []
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        document_values.append({key: document.get(key) for key in (
            "id", "record_id", "status", "sha256", "pages", "byte_size", "error", "is_current",
        )} | {"text_sha256": sha256_text(str(document.get("text") or ""))})
    correction_values = []
    for correction in corrections:
        if not isinstance(correction, Mapping):
            continue
        correction_values.append({key: correction.get(key) for key in (
            "id", "project_id", "requirement_id", "text", "reason", "material_version",
        )})
    value = {"version": READING_VERSION, "records": sorted(record_values, key=lambda item: item["id"] or -1),
             "documents": sorted(document_values, key=lambda item: str(item.get("id") or "")),
             "corrections": sorted(correction_values, key=lambda item: int(item.get("id") or 0))}
    return f"m{READING_VERSION}-{sha256_text(json_text(value))[:32]}"


def build_material(records: Iterable[Mapping[str, Any]], documents: Iterable[Mapping[str, Any]],
                   *, version: str, corrections: Iterable[Mapping[str, Any]] = (),
                   segment_chars: int = DEFAULT_SEGMENT_CHARS) -> dict[str, Any]:
    """Build the complete, source-addressable package given to a reader."""

    record_list = [dict(record) for record in records if isinstance(record, Mapping)]
    document_list = [dict(document) for document in documents if isinstance(document, Mapping)]
    correction_list = [dict(correction) for correction in corrections if isinstance(correction, Mapping)]
    sources: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for record in record_list:
        record_id = _int(record.get("id"))
        body = str(record.get("body_text") or "")[:MAX_SOURCE_TEXT]
        source_id = f"announcement-{record_id}"
        source_segments = segment_text(body, max_chars=segment_chars)
        sources.append({"source_id": source_id, "source_kind": "announcement", "record_id": record_id,
                        "title": _text(record.get("title"), 400), "kind": _text(record.get("kind"), 40),
                        "sha256": sha256_text(body), "text": body})
        for segment in source_segments:
            segments.append({**segment, "source_id": source_id, "source_kind": "announcement",
                             "record_id": record_id, "page": None})
        coverage.append({"source_kind": "announcement", "record_id": record_id,
                         "segments": len(source_segments), "characters": len(body),
                         "status": "provided" if body else "unavailable"})
    for document in document_list:
        document_id = _text(document.get("id"), 200)
        record_id = _int(document.get("record_id"))
        status = _text(document.get("status"), 40) or "failed"
        fragments = _page_fragments(document)
        document_source_id = f"attachment-{document_id}"
        all_text = "\n".join(fragment["text"] for fragment in fragments)
        sources.append({"source_id": document_source_id, "source_kind": "attachment", "record_id": record_id,
                        "document_id": document_id, "name": _text(document.get("name"), 400),
                        "status": status, "sha256": _text(document.get("sha256"), 128),
                        "text": all_text, "page_texts": fragments})
        count = 0
        for fragment in fragments:
            for segment in segment_text(fragment["text"], max_chars=segment_chars):
                segments.append({**segment, "source_id": document_source_id, "source_kind": "attachment",
                                 "record_id": record_id, "document_id": document_id,
                                 "page": fragment.get("page")})
                count += 1
        coverage.append({"source_kind": "attachment", "record_id": record_id, "document_id": document_id,
                         "sha256": _text(document.get("sha256"), 128), "segments": count,
                         "characters": len(all_text), "status": "provided" if status == "parsed" and all_text else status})
    return {"version": READING_VERSION, "material_version": version, "records": record_list,
            "documents": document_list, "corrections": correction_list, "sources": sources,
            "segments": segments, "coverage": coverage}


def _compact_source_for_prompt(source: Mapping[str, Any]) -> dict[str, Any]:
    value = {key: source.get(key) for key in ("source_id", "source_kind", "record_id", "document_id",
                                                "name", "kind", "status", "sha256") if source.get(key) is not None}
    value["text"] = str(source.get("text") or "")
    if isinstance(source.get("page_texts"), list):
        value["page_texts"] = source["page_texts"]
    return value


def build_reading_messages(material: Mapping[str, Any]) -> list[dict[str, str]]:
    """Create the model request shared by Pi SDK and single-call providers."""

    system = """你是采购资料阅读助手。只处理本次采购资料包中的公告正文和附件，不把其他标包、导航、背景新闻或行业常识当成本次采购事实。输出一个完整 JSON 对象，顶层必须有 version=1、summary、demand_type、stage、buyer、region、category、sections、milestones、coverage、conflicts、unresolved。demand_type 只能是 confirmed、lead、unknown；新闻或建设/技改线索只能为 lead 或 unknown，不能写成已确认招标。"""
    system += """以无损压缩方式整理要求：每条 requirements 只放在最匹配的一个 section 中。同一主题、主体、标段和适用条件下、共同构成一个义务或流程的相关条款应合并为一条简洁 requirement，保留必要细节与限定；不要把同一要求复制到多个 section，也不要把字段、联系方式、并列条件或流程的每一步机械拆成微型 requirement。只有不同主体、动作、适用条件、后果、标段/日期或可独立执行的义务，才分别成条；不得因此漏掉真实独立要求。text 尽量简洁，但必须保留会影响判断的日期、数字、金额、包号、单位、否定、例外、限制和适用范围。同一标段的设备型号、数量、批次和供货限制可合并在该标段的 scope requirement 中，但不同标段仍保留对应边界；同一资格主题的相关条件、证明材料和例外可合并并保留每个限定。采购方 section 中招标人及代理机构的名称、地址、联系人、电话和邮箱可合并为主体信息；注册、购买、上传、签到、解密等例行平台操作可写成一条简短流程，除非某一步有独立截止、费用、资格或失败后果。没有实质参与义务的信息可留在 summary 中，不强行生成 requirement。合并后的 requirement 至少要有一条连续 quote 同时支持该条 text 中全部数字、日期、金额、包号、限制和例外；优先只提供这一条足够的 quote。不要把一个合并要求的支持事实拆到多个无法各自支持完整 claim 的短 quote；若没有能覆盖完整 claim 的连续原文，就保留为分别可验证的 requirements，非义务联系方式留在 summary。quote 必须是本轮资料包中的实际连续原文，原样保留数字、日期、包号、单位、例外、限制和原始标点，不改写、不拼接。explanation 默认写空字符串，仅在解释术语或条件的必要含义时填写，不增加资料未证实的事实，绝不复述 text 或 quote。每个 source 必须包含 source_kind（announcement 或 attachment）、record_id、quote；附件还要带服务器提供的 document_id，可带 page 和 sha256。milestones.kind 必须区分 document_access、registration、question、submission、opening、other；获取截止与提交截止分别保存，不能合并成一个截止日期。多包分别保存，正文和更正的冲突保留在 conflicts 和 unresolved，不选择性覆盖。资料受限或读取失败时明确 unresolved，不能用空白摘要冒充完成；已提供的资料仍应完整覆盖。"""
    system += """sections 中每项固定为 {"id":"buyer","title":"谁在采购","requirements":[{"id":"buyer-1","text":"采购方要求正文","explanation":"必要解释，可为空字符串","sources":[{"source_kind":"announcement","record_id":1,"quote":"资料中的连续原文"}]}]}。示例值需替换为本轮真实内容。每条要求必须有非空字符串 text，explanation 只作必要补充，不能只填 explanation 或用其他字段名代替 text；无法形成要求的内容写入 unresolved。"""
    system += """milestones 每项固定为 {"id":"submission-1","kind":"submission","label":"投标递交截止","at":"2026-10-23T13:30:00+08:00","sources":[{"source_kind":"announcement","record_id":1,"quote":"递交截止时间：2026年10月23日13:30（北京时间）"}]}，示例必须替换为实际资料。at 必须使用 ISO 8601；北京时间明确为 +08:00；原文仅有日期则用 YYYY-MM-DD，不补造小时分钟。每个节点只写一个时间，开始和截止分别生成节点，label 写节点名称，不重复日期；每个 sources.quote 必须保留完整日期及对应时间。未给出具体日期、仅写详见采购文件或截止前的，不生成确定日期。"""
    system += """summary 只做整体采购概览，可指出本次适用包等关键范围，但不要把完整的包级要求、资格限制或时间表重复到 summary 或多个 section。sections 按谁在采购、买什么、交付、资格、办理节点、材料费用、怎样参加、特殊要求等实际内容组织，id 分别使用 buyer、scope、delivery、qualification、schedule、materials、participation、special；只输出有实际要求的 sections，不能因此遗漏真实独立要求；长资料要覆盖所有 segments。coverage 中保留每个来源的覆盖状态。若有 corrections，必须显式处理；无法采纳的纠正写入 unresolved。不要输出 Markdown、思维过程、URL、本地路径或凭据。"""
    system += " Stage must be exactly one of: discovery, confirmed, registration, document_access, question, bidding, submission, opening, result, finished; use an empty string when unknown."
    payload = {"version": READING_VERSION, "material_version": material.get("material_version"),
               "sources": [_compact_source_for_prompt(source) for source in material.get("sources", [])
                           if isinstance(source, Mapping)],
               "segments": [segment for segment in material.get("segments", []) if isinstance(segment, Mapping)],
               "coverage": material.get("coverage", []), "corrections": material.get("corrections", [])}
    return [{"role": "system", "content": system}, {"role": "user", "content": json_text(payload)}]


def _source_texts(material: Mapping[str, Any]) -> dict[tuple[str, int | None, str | None], list[str]]:
    result: dict[tuple[str, int | None, str | None], list[str]] = {}
    for source in material.get("sources", []) if isinstance(material.get("sources"), list) else []:
        if not isinstance(source, Mapping):
            continue
        key = (str(source.get("source_kind") or ""), _int(source.get("record_id")),
               _text(source.get("document_id"), 200) or None)
        values = [str(source.get("text") or "")]
        values.extend(str(page.get("text") or "") for page in source.get("page_texts", [])
                      if isinstance(page, Mapping))
        result[key] = values
    return result


def _valid_source(raw: Any, material: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    source_kind = _text(raw.get("source_kind"), 30)
    if source_kind not in SOURCE_KINDS:
        return None
    record_id = _int(raw.get("record_id"))
    document_id = _text(raw.get("document_id"), 200) or None
    quote = str(raw.get("quote") or "")
    if record_id is None or not quote:
        return None
    if source_kind == "attachment" and not document_id:
        return None
    page = _int(raw.get("page"))
    texts = _source_texts(material).get((source_kind, record_id, document_id), [])
    if source_kind == "announcement":
        texts = _source_texts(material).get((source_kind, record_id, None), [])
    if page is not None and source_kind == "attachment":
        page_texts = []
        for source in material.get("sources", []) if isinstance(material.get("sources"), list) else []:
            if not isinstance(source, Mapping) or source.get("source_kind") != "attachment":
                continue
            if _int(source.get("record_id")) != record_id or _text(source.get("document_id"), 200) != document_id:
                continue
            page_texts = [str(page_value.get("text") or "") for page_value in source.get("page_texts", [])
                          if isinstance(page_value, Mapping) and _int(page_value.get("page")) == page]
            break
        if not page_texts:
            return None
        texts = page_texts
    # Exact containment is the primary check. A normalized whitespace match is
    # accepted only for collector line wrapping, while the returned quote stays
    # exactly as the model supplied it for user review.
    valid = any(quote in text for text in texts if text)
    if not valid:
        compact_quote = re.sub(r"\s+", "", quote)
        valid = bool(compact_quote) and any(compact_quote in re.sub(r"\s+", "", text) for text in texts if text)
    if not valid:
        return None
    value = _source_ref(source_kind=source_kind, record_id=record_id, document_id=document_id,
                        page=page, quote=quote, sha256=raw.get("sha256"))
    if source_kind == "attachment":
        allowed = {str(source.get("sha256") or "") for source in material.get("sources", [])
                   if isinstance(source, Mapping) and source.get("source_kind") == "attachment"
                   and _int(source.get("record_id")) == record_id and _text(source.get("document_id"), 200) == document_id}
        if value.get("sha256") and value["sha256"] not in allowed:
            value.pop("sha256", None)
        if page and not any(_int(page_value.get("page")) == page for source in material.get("sources", [])
                            if isinstance(source, Mapping) and source.get("document_id") == document_id
                            for page_value in source.get("page_texts", []) if isinstance(page_value, Mapping)):
            value.pop("page", None)
    return value


def _claim_tokens(value: str) -> list[str]:
    compact = re.sub(r"\s+", "", value)
    # Normalize ISO timestamps before extracting numeric claims.  Timezone
    # offsets and optional seconds are transport details, not source facts.
    iso_pattern = re.compile(
        r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?"
    )
    compact = iso_pattern.sub(
        lambda match: f"{match.group(1)}{chr(24180)}{int(match.group(2))}{chr(26376)}"
                      f"{int(match.group(3))}{chr(26085)}{match.group(4)}:{match.group(5)}",
        compact,
    )
    units = "".join(chr(code) for code in (
        24180, 26376, 26085, 26102, 20998, 19975, 20803, 22871, 21488, 20214,
        21544, 20844, 31859, 21253, 26631, 27573, 21495,
    ))
    chinese_numbers = "".join(chr(code) for code in (
        19968, 20108, 19977, 22235, 20116, 20845, 19971, 20843, 20061, 21313,
        30334, 21315, 19975,
    ))
    pattern = (r"\d+(?:\.\d+)?[" + units + r"]?"
               r"|[" + chinese_numbers + r"]+[" + units + r"]")
    tokens = re.findall(pattern, compact)
    return list(dict.fromkeys(tokens))


def _claim_supported(claim: str, source: Mapping[str, Any]) -> bool:
    quote = re.sub(r"\s+", "", str(source.get("quote") or ""))
    compact_claim = re.sub(r"\s+", "", claim)
    if not quote:
        return False
    for token in _claim_tokens(claim):
        if token not in quote:
            return False
    # Preserve a qualifier when the claim discusses the corresponding subject.
    # Long quotes may contain unrelated restrictions (for example, a purchase
    # instruction next to a date), so a global protected-word comparison would
    # incorrectly reject otherwise valid date citations.
    not_accept = chr(19981) + chr(25509) + chr(21463)
    joint_bid = chr(32852) + chr(21512) + chr(20307)
    not_allow = chr(19981) + chr(20801) + chr(35768)
    allow = chr(20801) + chr(35768)
    must_not = chr(19981) + chr(24471)
    get_value = chr(24471)
    delegate_only = chr(20165) + chr(33021) + chr(22996) + chr(25176)
    delegate = chr(22996) + chr(25176)
    only_accept = chr(20165) + chr(25509) + chr(21463)
    public_transfer = chr(20844) + chr(23545) + chr(20844)
    transfer = chr(36716) + chr(36134)
    deemed = chr(35270) + chr(21516)
    replace = chr(26367) + chr(20195)
    performance = chr(19994) + chr(32489)
    if not_accept in quote and joint_bid in compact_claim and not_accept not in compact_claim:
        return False
    if not_allow in quote and allow in compact_claim and not_allow not in compact_claim:
        return False
    if must_not in quote and get_value in compact_claim and must_not not in compact_claim:
        return False
    if delegate_only in quote and delegate in compact_claim and delegate_only not in compact_claim:
        return False
    if only_accept in quote and (public_transfer in compact_claim or transfer in compact_claim)             and only_accept not in compact_claim:
        return False
    if deemed in quote and (replace in compact_claim or performance in compact_claim)             and deemed not in compact_claim and replace not in compact_claim:
        return False
    return True


def _lot_number(value: Any) -> str | None:
    """Return a comparable package number from values such as ``02包``."""

    compact = re.sub(r"\s+", "", str(value or ""))
    match = re.search(r"(\d+|[一二三四五六七八九十百千万]+)包", compact)
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return str(int(token))
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}
    if token in values:
        return str(values[token])
    if len(token) == 2 and token[0] == "十" and token[1] in values:
        return str(10 + values[token[1]])
    if len(token) == 2 and token[1] == "十" and token[0] in values:
        return str(values[token[0]] * 10)
    return None


def _explicit_scope_lots(material: Mapping[str, Any]) -> set[str]:
    """Find an explicit ``this tender is package X`` scope marker.

    The marker is used only as a post-model consistency check.  It never
    invents a lot from a title, a general lot table, or a numeric citation.
    """

    values: set[str] = set()
    pattern = re.compile(
        r"本次(?:招标|采购)(?:内容|范围)?为(?:本项目)?([0-9０-９一二三四五六七八九十百千万]+)包"
    )
    for source in material.get("sources", []) if isinstance(material.get("sources"), list) else []:
        if not isinstance(source, Mapping):
            continue
        compact = re.sub(r"\s+", "", str(source.get("text") or ""))
        for match in pattern.finditer(compact):
            lot = _lot_number(match.group(1) + "包")
            if lot:
                values.add(lot)
    return values


def _valid_iso_date(value: str, quote: str) -> bool:
    """Compare complete date/time pairs, allowing equivalent source formats."""
    return timestamp_supported(value, quote)


def _normalize_sources(value: Any, material: Mapping[str, Any], unresolved: list[str], *, claim: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return result
    for raw in value[:20]:
        source = _valid_source(raw, material)
        if source is None:
            unresolved.append("存在无法在本次公告或附件中定位的引用，已要求重新核对原文")
            continue
        if claim and not _claim_supported(claim, source):
            unresolved.append("引用片段未保留该要求中的全部数字、日期、包号或限制，已要求重新核对原文")
            continue
        if source not in result:
            result.append(source)
    return result


def normalize_reading(payload: Any, material: Mapping[str, Any], *, status: str = "complete",
                      error: str = "") -> dict[str, Any]:
    """Validate and normalize one model response against the material package."""

    raw = payload if isinstance(payload, Mapping) else {}
    if isinstance(raw.get("procurement_reading"), Mapping):
        raw = raw["procurement_reading"]
    unresolved: list[str] = []
    if error:
        unresolved.append(_text(error, 240))
    summary = _text(raw.get("summary"), 4_000)
    demand_type = _text(raw.get("demand_type"), 20)
    if demand_type not in DEMAND_TYPES:
        demand_type = "unknown"
    record_kinds = {str(record.get("kind") or "").lower() for record in material.get("records", [])
                    if isinstance(record, Mapping)}
    if record_kinds and record_kinds.issubset({"news", "article"}) and demand_type == "confirmed":
        demand_type = "lead"
        unresolved.append("新闻或线索资料不能单独确认招标需求")
    stage = _text(raw.get("stage"), 40)
    if stage not in READING_STAGES:
        stage = ""
    buyer = _text(raw.get("buyer"), 500)
    region = _text(raw.get("region"), 500)
    source_text = "".join(
        str(source.get("text") or "")
        for source in material.get("sources", []) if isinstance(source, Mapping)
    )
    compact_source_text = re.sub(r"\s+", "", source_text)
    if buyer and re.sub(r"\s+", "", buyer) not in compact_source_text:
        unresolved.append("\u91c7\u8d2d\u65b9\u4fe1\u606f\u7f3a\u5c11\u53ef\u5b9a\u4f4d\u7684\u516c\u544a\u6216\u9644\u4ef6\u4f9d\u636e")
        buyer = ""
    if region and re.sub(r"\s+", "", region) not in compact_source_text:
        unresolved.append("\u5730\u533a\u4fe1\u606f\u7f3a\u5c11\u53ef\u5b9a\u4f4d\u7684\u516c\u544a\u6216\u9644\u4ef6\u4f9d\u636e")
        region = ""
    category = [_text(value, 200) for value in raw.get("category", [])[:30]
                ] if isinstance(raw.get("category"), list) else []
    scope_lots = _explicit_scope_lots(material)
    if len(scope_lots) > 1:
        unresolved.append("原文明确的本次适用包范围存在冲突，包级要求暂不能确认")
    sections: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for section_index, section_raw in enumerate(raw.get("sections", []) if isinstance(raw.get("sections"), list) else []):
        if not isinstance(section_raw, Mapping):
            continue
        section_id = _text(section_raw.get("id"), 100) or f"section-{section_index + 1}"
        if section_id in seen_ids:
            section_id = f"{section_id}-{section_index + 1}"
        seen_ids.add(section_id)
        requirements: list[dict[str, Any]] = []
        raw_requirements = section_raw.get("requirements", [])
        if not isinstance(raw_requirements, list):
            raw_requirements = []
        for req_index, req_raw in enumerate(raw_requirements[:200]):
            if not isinstance(req_raw, Mapping):
                continue
            req_id = _text(req_raw.get("id"), 120) or f"{section_id}-requirement-{req_index + 1}"
            if req_id in seen_ids:
                req_id = f"{req_id}-{req_index + 1}"
            seen_ids.add(req_id)
            req_unresolved: list[str] = []
            claim = " ".join(_text(req_raw.get(key), 8_000) for key in ("text", "explanation", "lot"))
            sources = _normalize_sources(req_raw.get("sources"), material, req_unresolved, claim=claim)
            if not sources:
                req_unresolved.append("该要求缺少可定位的公告或附件原文")
            unresolved.extend(req_unresolved)
            item: dict[str, Any] = {"id": req_id, "text": _text(req_raw.get("text"), 8_000),
                                    "explanation": _text(req_raw.get("explanation"), 8_000), "sources": sources}
            for key in ("lot", "status"):
                if req_raw.get(key) is not None:
                    item[key] = _text(req_raw.get(key), 200)
            lot = _lot_number(item.get("lot"))
            if lot and scope_lots and (len(scope_lots) != 1 or lot not in scope_lots):
                item["status"] = "uncertain"
                req_unresolved.append(
                    f"要求标注为{item.get('lot')}，但原文明确的本次适用包范围为"
                    f"{','.join(sorted(scope_lots))}包，不能把该要求作为当前包的已确认事实"
                )
                unresolved.append(req_unresolved[-1])
            if not sources:
                item["status"] = "uncertain"
            requirements.append(item)
        if requirements or _text(section_raw.get("title")):
            sections.append({"id": section_id, "title": _text(section_raw.get("title"), 500) or section_id,
                             "requirements": requirements})
    milestones: list[dict[str, Any]] = []
    raw_milestones = raw.get("milestones", [])
    if not isinstance(raw_milestones, list):
        raw_milestones = []
    for index, milestone_raw in enumerate(raw_milestones[:100]):
        if not isinstance(milestone_raw, Mapping):
            continue
        milestone_id = _text(milestone_raw.get("id"), 100) or f"milestone-{index + 1}"
        if milestone_id in seen_ids:
            milestone_id = f"{milestone_id}-{index + 1}"
        while milestone_id in seen_ids:
            milestone_id = f"{milestone_id}-2"
        seen_ids.add(milestone_id)
        kind = _text(milestone_raw.get("kind"), 40)
        if kind not in MILESTONE_KINDS:
            kind = "other"
        milestone_unresolved: list[str] = []
        # Date semantics must be checked as a whole. Generic numeric token
        # containment rejects 09:00 against 9:00 and can cross-pair range ends.
        sources = _normalize_sources(milestone_raw.get("sources"), material, milestone_unresolved)
        at = normalize_timestamp(_text(milestone_raw.get("at"), 200)) or ''
        dated_sources = [source for source in sources if _valid_iso_date(at, source.get("quote", ""))]
        if sources and not dated_sources:
            milestone_unresolved.append("\u8282\u70b9\u65e5\u671f\u7f3a\u5c11\u4e0e\u5f15\u7528\u7247\u6bb5\u4e00\u81f4\u7684\u6709\u6548\u65e5\u671f")
        sources = dated_sources
        if not sources:
            milestone_unresolved.append("\u8282\u70b9\u7f3a\u5c11\u53ef\u5b9a\u4f4d\u7684\u516c\u544a\u6216\u9644\u4ef6\u539f\u6587")
        unresolved.extend(milestone_unresolved)
        if sources:
            label = _text(milestone_raw.get("label"), 500)
            if not label or not any(_claim_supported(label, source) for source in sources):
                label = {'document_access': '文件获取', 'submission': '投标 / 响应文件递交截止',
                         'opening': '开标', 'registration': '报名', 'question': '提问 / 澄清截止'}.get(kind, '采购时间节点')
            milestones.append({"id": milestone_id, "kind": kind, "label": label,
                               "at": at, "sources": sources})
    conflicts = [_text(value, 4_000) for value in raw.get("conflicts", [])[:100]
                 ] if isinstance(raw.get("conflicts"), list) else []
    unresolved.extend(_text(value, 4_000) for value in raw.get("unresolved", [])[:100]
                      if _text(value)) if isinstance(raw.get("unresolved"), list) else None
    # Distinguish acquisition and submission deadlines even when a model tried
    # to collapse them into one label. Keeping both is safer than inventing a
    # second date; an unresolved note tells the UI why one is absent.
    milestone_kinds = {milestone["kind"] for milestone in milestones}
    if "document_access" in milestone_kinds and "submission" not in milestone_kinds:
        unresolved.append("已识别文件获取节点，但未发现可定位的提交截止原文")
    if "submission" in milestone_kinds and "document_access" not in milestone_kinds:
        # This is valid for notices that do not publish a purchase window.
        pass
    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), list) else []
    normalized_coverage = []
    expected = {(str(item.get("source_kind")), _int(item.get("record_id")), _text(item.get("document_id"), 200) or None)
                for item in material.get("coverage", []) if isinstance(item, Mapping)}
    for item in coverage[:500]:
        if not isinstance(item, Mapping):
            continue
        key = (str(item.get("source_kind") or ""), _int(item.get("record_id")), _text(item.get("document_id"), 200) or None)
        if key in expected:
            normalized_coverage.append({key_name: item.get(key_name) for key_name in (
                "source_kind", "record_id", "document_id", "segments", "characters", "status"
            ) if item.get(key_name) is not None})
    # Coverage is completed by the host from the exact segments sent in this
    # batch. Model supplied counts/status are advisory and are never allowed
    # to claim an unread source or an unprocessed tail.
    for source_coverage in material.get("coverage", []) if isinstance(material.get("coverage"), list) else []:
        if not isinstance(source_coverage, Mapping):
            continue
        source_status = _text(source_coverage.get("status"), 40)
        if source_status not in {"provided", "parsed"}:
            unresolved.append(
                f"\u9644\u4ef6\u8d44\u6599\u672a\u5b8c\u6574\u63d0\u4f9b\uff0c\u5f53\u524d\u9605\u8bfb\u65e0\u6cd5\u8986\u76d6\u5168\u90e8\u5185\u5bb9\uff08\u72b6\u6001\uff1a{source_status or 'unknown'}\uff09"
            )
    # User corrections are never silently discarded. A model may explicitly
    # cite the corrected text, otherwise the item remains unresolved.
    searchable = json.dumps({"summary": summary, "sections": sections, "milestones": milestones}, ensure_ascii=False)
    # A correction is an audited human assertion.  Matching only its
    # requirement id would allow the old text to survive silently; require
    # the corrected text itself to be represented in the normalized result.
    for correction in material.get("corrections", []) if isinstance(material.get("corrections"), list) else []:
        if not isinstance(correction, Mapping):
            continue
        correction_text = _text(correction.get("text"), 4_000)
        requirement_id = _text(correction.get("requirement_id"), 200)
        adopted = False
        if correction_text:
            for section in sections:
                for requirement in section.get("requirements", []) if isinstance(section, Mapping) else []:
                    if not isinstance(requirement, Mapping):
                        continue
                    if requirement_id and _text(requirement.get("id"), 200) != requirement_id:
                        continue
                    if correction_text in f"{requirement.get('text', '')} {requirement.get('explanation', '')}":
                        adopted = True
                        break
                if adopted:
                    break
        if correction_text and not adopted:
            unresolved.append(
                f"\u4eba\u5de5\u7ea0\u6b63\u672a\u88ab\u5f53\u524d\u9605\u8bfb\u7ed3\u679c\u91c7\u7eb3\uff0c\u8bf7\u590d\u6838\u7ea0\u6b63\u5185\u5bb9\uff1a{correction_text}"
            )

    unresolved = list(dict.fromkeys(value for value in unresolved if value))
    normalized_status = status if status in READING_STATUSES else "partial"
    if normalized_status == "complete" and unresolved:
        normalized_status = "partial"
    return {"version": READING_VERSION, "material_version": _text(material.get("material_version"), 200),
            "status": normalized_status, "stale": False, "summary": summary, "demand_type": demand_type,
            "stage": stage, "buyer": buyer, "region": region, "category": category,
            "sections": sections, "milestones": milestones, "coverage": normalized_coverage,
            "conflicts": list(dict.fromkeys(conflicts)), "unresolved": unresolved}


def empty_reading(material_version_value: str | None, *, status: str = "pending", error: str = "") -> dict[str, Any]:
    normalized = status if status in READING_STATUSES else "pending"
    return {"version": READING_VERSION, "material_version": material_version_value, "status": normalized,
            "stale": False, "summary": "", "demand_type": "unknown", "stage": "", "buyer": "",
            "region": "", "category": [], "sections": [], "milestones": [], "coverage": [], "conflicts": [],
            "unresolved": [_text(error, 240)] if error else []}


__all__ = [
    "DEMAND_TYPES", "MILESTONE_KINDS", "READING_STATUSES", "READING_VERSION", "build_material",
    "READING_STAGES", "build_reading_messages", "empty_reading", "material_version",
    "normalize_reading", "segment_text",
]
