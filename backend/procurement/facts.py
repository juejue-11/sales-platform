"""Conservative procurement facts extracted from an announcement body.

The collector's ``body_text`` is a source document in its own right.  This
module only turns facts already stated in the bounded procurement and delivery
excerpts into structured values.  It never uses a title as a fact source and
does not infer technical specifications from an equipment name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from ..analysis.briefing import build_briefing, normalize_source_text


FACT_FIELDS = ("scope", "delivery_period", "delivery_location", "qualification", "contact")
FACT_STATUSES = {"extracted", "uncertain", "conflict"}


class AnnouncementItem(TypedDict):
    name: str
    specification: str
    material: str
    quantity: str
    unit: str
    delivery_period: str
    delivery_location: str
    code: str
    standard: str
    note: str
    source_document_id: str
    record_id: int
    page: int
    quote: str
    source_kind: Literal["announcement"]


_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]+)"
# Longer goods units come first so ``台套`` is not split into a shorter
# unit. ``个`` is retained for ordinary goods but a following
# ``标段/包`` is explicitly rejected below.
_QUANTITY_RE = re.compile(
    rf"(?P<number>{_NUMBER})\s*(?P<unit>台套|千克|公斤|吨|套|台|件|个|米|批|项|辆|部|组|只|块|箱)",
    re.IGNORECASE,
)
_TIME_SUFFIX_RE = re.compile(r"^(?:日历天|工作日|天|日|个月|月|年|次)")
_CLAUSE_SPLIT_RE = re.compile(r"[。；;！？!?\n]+")
_NAME_SPLIT_RE = re.compile(r"[，,；;。:：、]+")
_NUMBER_ONLY_NAME = {"项目", "采购项目", "标段", "货物", "物资", "设备", "内容", "范围",
                     "项目概况", "项目名称", "招标范围", "招标内容"}
_NAME_BLOCKLIST = ("标段", "本项目", "投标", "供应商", "招标人", "采购人", "联系人", "电话", "地址", "平台",
                   "招标文件", "投标文件", "资格", "业绩", "审计", "标书费", "金额", "售价", "预算",
                   "保证金", "报价", "价格", "费用", "资金", "交货期", "交付期限", "交货时间", "交付时间",
                   "供货期", "交付期", "质保期", "保修期", "合同签订", "施工", "本次设计", "总承包", "本次采购共")
_QUALIFICATION_BLOCKLIST = ("投标人", "资格要求", "资格条件", "资质要求", "业绩要求", "证书",
                            "审计报告", "标书费", "文件售价", "预算金额", "保证金", "报价", "价格", "费用",
                            "招标人", "采购人", "联系人", "联系电话", "地址", "供应商")
_PERIOD_LABEL_RE = r"(?:交货期|交付期限|交货时间|交付时间|供货期|交付期)"
_LOCATION_LABEL_RE = r"(?:交货地点|交付地点|供货地点)"
_SCOPE_LABEL_RE = r"(?:采购范围|招标范围|采购内容|采购标的|货物清单)"
_QUALIFICATION_LABEL_RE = r"(?:投标人资格要求|供应商资格要求|投标人资格|供应商资格|资格要求|资格条件|资质要求|投标资格|资格审查)"
_CONTACT_LABEL_RE = r"(?:采购人联系方式|招标人联系方式|项目联系人|采购联系人|联系人信息|联系方式|联系信息|联系人)"
_LABEL_STOP_RE = rf"(?=(?:{_PERIOD_LABEL_RE}|{_LOCATION_LABEL_RE})\s*[:：]?|\s+\d+(?:\.\d+)?[、.]|$)"
_RESTRICTION_START_RE = re.compile(r"(?:招标文件|采购文件|附件)\s*(?:获取|下载|领取)|(?:文件|附件)\s*(?:获取|下载)")
_RECORD_ID_LIMIT = 10_000_000_000
_QUOTE_IGNORED = set(" \t\r\n:：,，;；、（）()")
_DELIVERY_POINTERS = ("详见需求清单", "详见采购清单", "详见招标文件", "详见采购文件", "见需求清单")
_SCOPE_POINTERS = ("详见附件", "见附件", "详见清单", "见清单", "另见")
_DELIVERY_POLLUTION = (
    "未按", "买方有权", "赔偿", "终止合同", "罚款", "罚扣", "扣款", "违约",
    "备注", "表头", "目录", "附件说明", "货物质量标准", "主要技术性能指标",
    "技术质量要求", "技术要求", "合同模板", "图纸", "附件3",
)
_SCOPE_POLLUTION = (
    "保修", "质保", "防锈", "保证期", "货物质量标准", "主要技术性能指标",
    "技术质量要求", "技术要求", "合同模板", "交付货物之日起", "买方有权",
    "附件目录", "附件说明", "目录如下", "备注", "表头",
)
_PERIOD_DATE_RE = re.compile(
    r"(?:\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
_PERIOD_DURATION_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]+)\s*"
    r"(?:个日历天|日历天|个工作日|工作日|天|日|月|年|小时)"
)
_FACT_LABEL_START_RE = re.compile(
    rf"^(?:\d+(?:\.\d+)*[、.．]?\s*)?(?:{_SCOPE_LABEL_RE}|{_PERIOD_LABEL_RE}|"
    rf"{_LOCATION_LABEL_RE}|{_QUALIFICATION_LABEL_RE}|{_CONTACT_LABEL_RE})\s*[:：]?"
)


def _chinese_number(value: str) -> str:
    """Return a stable quantity string while preserving decimal Arabic values."""

    value = value.strip()
    if not value or value.isdigit() or re.fullmatch(r"\d+(?:\.\d+)?", value):
        return value
    singles = {"零": 0, "〇": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in singles:
        return str(singles[value])
    # Small Chinese quantities are common in notices.  Keep more complex
    # forms in source wording when a lossless conversion would be ambiguous.
    if value == "十":
        return "10"
    if len(value) == 2 and value[0] == "十" and value[1] in singles:
        return str(10 + singles[value[1]])
    if len(value) == 2 and value[1] == "十" and value[0] in singles:
        return str(singles[value[0]] * 10)
    return value


def _raw_quote(raw: str, fragment: str) -> str:
    """Find ``fragment`` in ``raw`` while ignoring collector whitespace.

    The returned value is the original substring, including line breaks.  A
    generated normalized phrase is never returned as evidence.
    """

    raw = str(raw or "")
    fragment = str(fragment or "")
    target = "".join(char for char in fragment if not char.isspace() and char not in _QUOTE_IGNORED)
    if not raw or not target:
        return ""
    compact_chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(raw):
        if char.isspace() or char in _QUOTE_IGNORED:
            continue
        compact_chars.append(char)
        positions.append(index)
    compact = "".join(compact_chars)
    offset = compact.find(target)
    if offset < 0:
        return ""
    start = positions[offset]
    end = positions[offset + len(target) - 1] + 1
    return raw[start:end]


def _normalize_fact_text(raw: Any) -> str:
    """Normalize collector whitespace while retaining field line boundaries."""

    source = str(raw or "")
    if not source:
        return ""
    marker = "\ue001"
    marked = re.sub(r"[\r\n]+", marker, source)
    normalized = normalize_source_text(marked)
    normalized = re.sub(rf"\s*{marker}\s*", marker, normalized)
    parts = normalized.split(marker)
    if len(parts) == 1:
        return normalized
    result = [parts[0]]
    for part in parts[1:]:
        # Keep a separator before an independent labelled row.  Newlines that
        # split one Chinese token/label are removed so ordinary collector OCR
        # continues to normalize as before.
        result.append(" " if _FACT_LABEL_START_RE.match(part) else "")
        result.append(part)
    return "".join(result).strip()


def _clean_item_name(value: str) -> str:
    value = re.sub(r"^\s*(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿]+)[、.]\s*", "", value)
    value = value.strip(" \t\r\n:：-—，,；;。、")
    # Scope excerpts may contain a long lead-in before the final clause.  The
    # final comma/colon segment is the only one eligible as an item name.
    parts = [part.strip() for part in _NAME_SPLIT_RE.split(value) if part.strip()]
    if parts:
        value = parts[-1]
        if value in {"数量", "采购数量", "数量为", "合计"} and len(parts) > 1:
            value = parts[-2]
    value = re.sub(r"^(?:本项目(?:物资)?采购范围|物资采购范围|采购范围|采购内容|采购标的|招标范围|招标内容)\s*", "", value)
    value = re.sub(r"^(?:本项目|本次|此次|项目|物资|需|拟)?(?:采购|购置)\s*(?:共)?", "", value)
    value = re.sub(r"\s*(?:采购)?数量(?:为)?\s*$", "", value)
    value = value.strip(" \t\r\n:：-—，,；;。、").rstrip("（(").rstrip()
    return value[:200]


def _scope_item_candidates(scope: str) -> list[tuple[int, AnnouncementItem, str]]:
    candidates: list[tuple[int, AnnouncementItem, str]] = []
    for clause in _CLAUSE_SPLIT_RE.split(scope):
        clause = clause.strip()
        if not clause:
            continue
        # ``build_briefing`` deliberately has a useful sentence fallback for
        # notices without headings.  A qualification sentence can also carry
        # equipment-looking quantities (for example “投标人具有2套设备”);
        # it is never an item source.
        if any(blocked in clause for blocked in _QUALIFICATION_BLOCKLIST):
            continue
        for match in _QUANTITY_RE.finditer(clause):
            following = clause[match.end():].lstrip()
            # A goods unit "个" is valid, but "30个日历天" is a delivery duration.
            if _TIME_SUFFIX_RE.match(following):
                continue
            # A section count such as “1个标段（包）” is not a material line.
            if re.match(r"(?:标段|包)\b|[（(](?:标段|包)", following):
                continue
            number_raw = str(match.group("number"))
            unit = str(match.group("unit"))
            name = _clean_item_name(clause[:match.start()])
            if not name or len(name) < 2 or name in _NUMBER_ONLY_NAME:
                continue
            # A previous goods quantity in the name means this match is a
            # nested delivery batch or sub-count, not another named line.
            if name.endswith("第") or _QUANTITY_RE.search(name):
                continue
            if any(blocked in name for blocked in _NAME_BLOCKLIST):
                continue
            # A valid scope clause normally contains a procurement cue.  The
            # item itself may be a generic material (for example “钢板”), so
            # the cue is checked on the clause rather than only on the name.
            if not any(cue in clause for cue in ("采购", "招标", "物资", "货物", "设备", "清单", "标的")):
                continue
            score = 20 + min(20, len(name))
            if any(cue in name for cue in ("泵", "机", "钢", "板", "阀", "管", "电", "仪", "车")):
                score += 8
            if any(cue in clause for cue in ("采购范围", "采购内容", "物资采购")):
                score += 12
            item: AnnouncementItem = {
                "name": name,
                "specification": "",
                "material": "",
                "quantity": _chinese_number(number_raw),
                "unit": unit,
                "delivery_period": "",
                "delivery_location": "",
                "code": "",
                "standard": "",
                "note": "",
                "source_document_id": "",
                "record_id": 0,
                "page": 0,
                "quote": "",
                "source_kind": "announcement",
            }
            candidates.append((score, item, f"{name}{number_raw}{unit}"))
    return candidates


def _extract_label_value(text: str, labels: str) -> str:
    match = re.search(rf"{labels}\s*[:：]?\s*(.+?)(?:。|；|;|！|？|!|\?|{_LABEL_STOP_RE})", text)
    if match is None:
        match = re.search(rf"{labels}\s*[:：]?\s*(.+)$", text)
    if match is None:
        return ""
    value = str(match.group(1)).strip(" \t\r\n:：-—，,；;。")
    # Heading numbers can remain when the source omitted punctuation before
    # the next row.  They are not part of a delivery value.
    value = re.sub(r"\s+\d+(?:\.\d+)?[、.]\s*$", "", value).strip()
    if not value or value in {"/", "—", "-", "待定", "另行通知"}:
        return ""
    return value[:4000]


def _restriction_quote(raw: str) -> str:
    normalized = normalize_source_text(raw)
    if not normalized:
        return ""
    starts = list(_RESTRICTION_START_RE.finditer(normalized))
    # A body can omit the “获取” heading and still explicitly require login,
    # purchase, or review before download.  Search only a bounded body window
    # around those access verbs; a platform name alone is never evidence.
    for start_match in starts:
        start = start_match.start()
        window = normalized[start:start + 700]
        if "下载" in window and (("登录" in window or "登陆" in window) or "购买" in window or "审核" in window):
            # Include the object after “下载” when the source says
            # “下载招标文件/采购文件/附件”, while still keeping the evidence
            # bounded before account or unrelated notice text.
            end = min(len(normalized), start + max(80, min(700, window.find("下载") + 12)))
            quote = _raw_quote(raw, normalized[start:end])
            if quote:
                return quote[:1200]
    for cue in ("登录", "登陆", "购买"):
        start = normalized.find(cue)
        if start < 0:
            continue
        window = normalized[max(0, start - 120):start + 420]
        if "下载" not in window or not any(token in window for token in ("招标文件", "采购文件", "附件")):
            continue
        quote = _raw_quote(raw, window)
        if quote:
            return quote[:1200]
    return ""


def _record_id(value: Any) -> int:
    """Return a bounded record id suitable for a public evidence object."""

    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= _RECORD_ID_LIMIT else 0


def evidence_source(
    *,
    source_kind: Literal["announcement", "attachment"],
    record_id: Any,
    quote: Any,
    document_id: Any = None,
    page: Any = None,
    sha256: Any = None,
) -> dict[str, Any]:
    """Build one source object while omitting unavailable optional metadata.

    In particular, a parser's legacy ``page=1`` default is not a real page
    for a DOCX/text-only document.  Callers pass ``page`` only when a page
    came from ``page_texts`` (or from an old parser row with a known page).
    """

    source: dict[str, Any] = {
        "source_kind": source_kind,
        "record_id": _record_id(record_id),
        "quote": str(quote or ""),
    }
    document = str(document_id or "").strip()
    if document:
        source["document_id"] = document
    try:
        page_number = int(page) if page is not None else 0
    except (TypeError, ValueError):
        page_number = 0
    if page_number > 0:
        source["page"] = page_number
    digest = str(sha256 or "").strip()
    if digest:
        source["sha256"] = digest
    return source


def _heading_label(text: str, start: int) -> bool:
    """Return whether a label starts a source heading rather than prose."""

    raw_prefix = str(text[:start])
    # A collector may remove the punctuation at the end of a line.  Preserve
    # an ordinary line/heading separator when the labelled value is otherwise
    # explicit; inline legal references generally have no whitespace before
    # the label and remain rejected.
    if raw_prefix and raw_prefix[-1].isspace():
        context = raw_prefix[-60:]
        if not any(token in context for token in (
            "未按", "合同模板", "合同条款", "合同约定", "合同执行", "通用条款",
            "附件目录", "附件说明", "目录如下", "备注", "技术要求",
        )):
            return True
    prefix = raw_prefix.rstrip()
    if not prefix:
        return True
    previous = prefix[-1]
    if previous in "。；;！？!?，,、:：\n\r":
        # A contract/template reference can contain a nested label that looks
        # like a heading after punctuation.  Keep those out of the new facts;
        # parser-row values are still retained separately as uncertain.
        # Keep the current sentence as context.  A clean field after a
        # completed contract paragraph remains valid, while
        # ``合同模板：章节；交货期：`` is still rejected as a nested clause.
        sentence_start = max(prefix.rfind("。"), prefix.rfind("！"), prefix.rfind("!"),
                             prefix.rfind("？"), prefix.rfind("?")) + 1
        context = prefix[sentence_start:]
        if any(token in context for token in (
            "合同模板", "合同条款", "合同约定", "合同执行", "通用条款",
            "附件目录", "附件说明", "目录如下", "备注", "技术要求",
        )):
            return False
        if previous in "；;" and any(token in prefix[-40:] for token in (
            "合同模板", "合同条款", "合同约定", "合同执行", "通用条款",
            "附件目录", "附件说明", "目录如下", "备注", "技术要求",
        )):
            return False
        return True
    # Numbered document headings are commonly emitted as ``2.交货期``.
    return bool(re.search(
        r"(?:^|[。；;！？!?])\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十百]+)[、.．]?\s*$",
        prefix,
    ))


def _label_segments(text: str, labels: str) -> list[tuple[str, str]]:
    """Extract source segments whose labels are real field headings.

    ``normalize_source_text`` removes collector line breaks, so the boundary
    check accepts sentence punctuation and numbered headings.  A label inside
    prose (for example ``未按交货期交货``) has neither a heading boundary nor a
    colon and is rejected.
    """

    if not text:
        return []
    label_pattern = re.compile(labels)
    all_delivery = re.compile(
        rf"(?:{_PERIOD_LABEL_RE}|{_LOCATION_LABEL_RE}|{_SCOPE_LABEL_RE}|"
        rf"{_QUALIFICATION_LABEL_RE}|{_CONTACT_LABEL_RE})"
    )
    matches = list(label_pattern.finditer(text))
    result: list[tuple[str, str]] = []
    for match in matches:
        if not _heading_label(text, match.start()):
            continue
        after = text[match.end():]
        colon = re.match(r"\s*[:：]\s*", after)
        if colon:
            value_start = match.end() + colon.end()
        else:
            whitespace = re.match(r"\s+", after)
            if not whitespace:
                continue
            value_start = match.end() + whitespace.end()
        if value_start >= len(text):
            continue

        end = len(text)
        sentence = re.search(r"[。；;！？!?]", text[value_start:])
        if sentence:
            end = min(end, value_start + sentence.start())
        # A source table may put two labelled fields on one line without
        # punctuation.  Stop at the next explicit label with its own colon.
        for next_match in all_delivery.finditer(text, value_start):
            next_after = text[next_match.end():]
            if re.match(r"\s*[:：]", next_after) and next_match.start() < end:
                end = next_match.start()
                break
        value = text[value_start:end].strip(" \t\r\n:：-—，,；;。、")
        if not value or value in {"/", "—", "-", "待定", "另行通知"}:
            continue
        result.append((str(match.group(0) or ""), value[:4000]))
    return result


def _delivery_segments(text: str, labels: str) -> list[tuple[str, str]]:
    """Return explicitly headed delivery values from normalized source text."""

    return _label_segments(text, labels)


def _scope_segments(text: str) -> list[tuple[str, str]]:
    """Return explicit procurement-scope headings from source text."""

    if not text:
        return []
    label_pattern = re.compile(_SCOPE_LABEL_RE)
    all_labels = re.compile(
        rf"(?:{_SCOPE_LABEL_RE}|{_PERIOD_LABEL_RE}|{_LOCATION_LABEL_RE}|"
        rf"{_QUALIFICATION_LABEL_RE}|{_CONTACT_LABEL_RE})"
    )
    result: list[tuple[str, str]] = []
    for match in label_pattern.finditer(text):
        if not _heading_label(text, match.start()):
            continue
        after = text[match.end():]
        colon = re.match(r"\s*[:：]\s*", after)
        if colon:
            value_start = match.end() + colon.end()
        else:
            whitespace = re.match(r"\s+", after)
            if not whitespace:
                continue
            value_start = match.end() + whitespace.end()
        if value_start >= len(text):
            continue
        end = len(text)
        sentence = re.search(r"[。；;！？!?]", text[value_start:])
        if sentence:
            end = min(end, value_start + sentence.start())
        for next_match in all_labels.finditer(text, value_start):
            next_after = text[next_match.end():]
            if re.match(r"\s*[:：]", next_after) and next_match.start() < end:
                end = next_match.start()
                break
        value = text[value_start:end].strip(" \t\r\n:：-—，,；;。、")
        if value:
            result.append((str(match.group(0) or ""), value[:4000]))
    return result


def _polluted_delivery_value(value: str) -> bool:
    """Identify values that look like a copied heading or unrelated block.

    A suspicious parser value remains visible in its original field.  This
    predicate only controls its evidence status; it never rewrites the value
    from another source.
    """

    text = str(value or "").strip()
    if not text:
        return False
    return len(text) > 4000 or any(token in text for token in _DELIVERY_POLLUTION) or bool(
        re.search(r"\d+\.\d+", text) and any(token in text for token in ("货物", "技术", "质量", "合同", "备注"))
    )


def _delivery_value_status(field: str, value: str) -> str:
    """Classify a headed delivery value without repairing its wording."""

    text = str(value or "").strip()
    if any(pointer in text for pointer in _DELIVERY_POINTERS):
        return "skip"
    if field == "delivery_period":
        has_time = bool(_PERIOD_DATE_RE.search(text) or _PERIOD_DURATION_RE.search(text))
        if not has_time:
            # General contract clauses are useful uncertainty signals only
            # when explicitly headed; they must never compete with a concrete
            # date/duration as a scalar conflict.
            return "uncertain" if any(token in text for token in _DELIVERY_POLLUTION) else "skip"
    return "uncertain" if _polluted_delivery_value(text) else "extracted"


def _scope_value_allowed(value: str) -> bool:
    text = str(value or "").strip()
    if not text or any(token in text for token in _SCOPE_POLLUTION):
        return False
    if any(pointer in text for pointer in (*_DELIVERY_POINTERS, *_SCOPE_POINTERS)):
        return False
    # A labelled scope should identify a procurement object or quantity.  This
    # rejects material-table headers and generic contract paragraphs.
    return bool(
        _QUANTITY_RE.search(text)
        or any(token in text for token in ("设备", "货物", "物资", "标的", "清单", "采购", "招标"))
    )


def _scope_fallback_allowed(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        text
        and not any(token in text for token in _SCOPE_POLLUTION)
        and not any(pointer in text for pointer in (*_DELIVERY_POINTERS, *_SCOPE_POINTERS))
        and (_QUANTITY_RE.search(text) or any(token in text for token in ("设备", "货物", "物资", "标的", "清单")))
        and any(token in text for token in ("采购", "招标", "设备", "货物", "物资", "标的", "清单"))
    )


def _source_quote(raw: str, excerpt: str) -> str:
    """Recover an exact raw prefix when a bounded excerpt was clipped."""

    quote = _raw_quote(raw, excerpt)
    if quote:
        return quote
    # Some callers/tests provide an excerpt clipped with a typographic
    # ellipsis.  Drop only that marker and map the remaining prefix back to
    # the source; the returned value is still entirely source text.
    prefix = re.sub(r"(?:\.\.\.|…)+\s*$", "", str(excerpt or "")).rstrip()
    return _raw_quote(raw, prefix) if prefix and prefix != excerpt else ""


def extract_text_evidence(
    text: Any,
    *,
    record_id: Any,
    source_kind: Literal["announcement", "attachment"],
    kind: str = "tender",
    document_id: Any = None,
    page: Any = None,
    sha256: Any = None,
) -> list[dict[str, Any]]:
    """Extract bounded, source-backed facts from one text fragment.

    ``build_briefing`` supplies the existing qualification/contact/scope
    selection rules.  Delivery values use the existing explicit labels but
    retain every labelled value so later aggregation can flag conflicts.
    Every returned candidate has an exact quote from ``text``; if a selected
    excerpt cannot be mapped back to the source, it is discarded rather than
    turned into fabricated evidence.
    """

    raw = str(text or "")
    normalized = _normalize_fact_text(raw)
    if not normalized or str(kind or "").strip().lower() == "news":
        return []
    record = {"id": _record_id(record_id), "kind": str(kind or "tender"), "body_text": raw}
    briefing = build_briefing(record)
    source_values: list[tuple[str, str, str]] = []

    explicit_scope = _scope_segments(normalized)
    if explicit_scope:
        for label, value in explicit_scope:
            if _scope_value_allowed(value):
                source_values.append(("scope", f"{label}：{value}", ""))
    elif source_kind == "announcement":
        # Body notices sometimes state a procurement sentence without a
        # heading.  Attachments are intentionally stricter: an arbitrary page
        # sentence (for example a warranty clause) is not a scope fact.
        scope_excerpt = normalize_source_text(briefing.get("scope_excerpt") or "")
        if _scope_fallback_allowed(scope_excerpt):
            source_values.append(("scope", scope_excerpt, ""))

    for label, value in _delivery_segments(normalized, _PERIOD_LABEL_RE):
        source_values.append(("delivery_period", value, label))
    for label, value in _delivery_segments(normalized, _LOCATION_LABEL_RE):
        source_values.append(("delivery_location", value, label))

    qualification = normalize_source_text(briefing.get("qualification_excerpt") or "")
    if qualification and any(token in qualification for token in (
        "资格", "资质", "业绩", "证书", "许可", "防爆", "安全生产",
    )) and not any(token in qualification for token in ("附件目录", "附件说明", "目录如下")):
        source_values.append(("qualification", qualification, ""))
    contact = normalize_source_text(briefing.get("contact_excerpt") or "")
    if contact and any(token in contact for token in (
        "联系人", "联系电话", "电话", "手机", "邮箱", "采购人", "招标人", "项目负责人",
    )) and not any(token in contact for token in ("附件目录", "附件说明", "目录如下")):
        source_values.append(("contact", contact, ""))

    result: list[dict[str, Any]] = []
    for field, value, label in source_values:
        if not value:
            continue
        # Include the label for delivery so the evidence points to the
        # labelled source line, while fact.value remains the extracted value.
        excerpt = f"{label}：{value}" if label else value
        quote = _source_quote(raw, excerpt)
        if not quote:
            # Normalization can change punctuation or collector line breaks.
            # No quote means no evidence, even if the normalized value looks
            # useful.
            continue
        note = ""
        status = "extracted"
        if field in {"delivery_period", "delivery_location"}:
            status = _delivery_value_status(field, value)
            if status == "skip":
                continue
            if status == "uncertain":
                note = "交付字段来源摘录疑似混入目录或其他段落，保留原值待核对。"
        result.append({
            "field": field,
            "value": value[:4000],
            "status": status,
            "note": note,
            "source": evidence_source(
                source_kind=source_kind,
                record_id=record_id,
                document_id=document_id,
                page=page,
                quote=quote,
                sha256=sha256,
            ),
        })
    return result


def extract_announcement_facts(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract body-backed items, delivery facts, and access evidence.

    The returned values are deliberately plain dictionaries so they can be
    included in the existing JSON API without introducing a persistence model.
    """

    if not isinstance(record, Mapping):
        return {"items": [], "delivery_period": "", "delivery_location": "", "scope_quote": "",
                "restriction_quote": ""}
    raw = str(record.get("body_text") or "")
    if not raw or str(record.get("kind") or "").strip().lower() == "news":
        return {"items": [], "delivery_period": "", "delivery_location": "", "scope_quote": "",
                "restriction_quote": ""}
    briefing = build_briefing(record)
    scope = normalize_source_text(briefing.get("scope_excerpt") or "")
    delivery = normalize_source_text(briefing.get("delivery_excerpt") or "")
    normalized_body = normalize_source_text(raw)
    try:
        record_id = int(record.get("id") if record.get("id") is not None else record.get("record_id"))
    except (TypeError, ValueError):
        record_id = 0
    record_id = record_id if 0 <= record_id <= _RECORD_ID_LIMIT else 0

    items: list[AnnouncementItem] = []
    seen: set[tuple[str, str, str]] = set()
    candidates = sorted(_scope_item_candidates(scope), key=lambda value: value[0], reverse=True)
    for _, item, fragment in candidates:
        identity = (item["name"], item["quantity"], item["unit"])
        if identity in seen:
            continue
        seen.add(identity)
        item["record_id"] = record_id
        item["quote"] = _raw_quote(raw, fragment)
        if not item["quote"]:
            # A source quote is mandatory for an announcement item.  If
            # collector normalization changed punctuation beyond recovery,
            # skip the fact instead of fabricating evidence.
            continue
        if "详见招标文件" in scope or "详见采购文件" in scope:
            item["note"] = "详细规格待核对招标文件。"
        items.append(item)

    # ``build_briefing`` ranks one bounded delivery excerpt.  A later file
    # access paragraph can make the location score higher and hide the period;
    # label-specific extraction from the full body safely recovers both values
    # without scanning qualification numbers or prices as delivery facts.
    period = _extract_label_value(normalized_body, _PERIOD_LABEL_RE)
    location = _extract_label_value(normalized_body, _LOCATION_LABEL_RE)
    if not period:
        period = _extract_label_value(delivery, _PERIOD_LABEL_RE)
    if not location:
        location = _extract_label_value(delivery, _LOCATION_LABEL_RE)
    # A single evidenced line can use this announcement's delivery terms.
    # Multiple lines may have different terms, so keep those values at the
    # announcement level rather than assigning the first label to every line.
    if len(items) == 1:
        items[0]["delivery_period"] = period
        items[0]["delivery_location"] = location
    scope_quote = items[0]["quote"] if items else ""
    return {
        "items": items,
        "delivery_period": period,
        "delivery_location": location,
        "scope_quote": scope_quote,
        "restriction_quote": _restriction_quote(raw),
    }


__all__ = [
    "AnnouncementItem",
    "FACT_FIELDS",
    "FACT_STATUSES",
    "evidence_source",
    "extract_announcement_facts",
    "extract_text_evidence",
]
