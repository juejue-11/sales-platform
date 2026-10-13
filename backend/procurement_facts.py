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

from .briefing import build_briefing, normalize_source_text


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
_LABEL_STOP_RE = rf"(?=(?:{_PERIOD_LABEL_RE}|{_LOCATION_LABEL_RE})\s*[:：]?|\s+\d+(?:\.\d+)?[、.]|$)"
_RESTRICTION_START_RE = re.compile(r"(?:招标文件|采购文件|附件)\s*(?:获取|下载|领取)|(?:文件|附件)\s*(?:获取|下载)")
_RECORD_ID_LIMIT = 10_000_000_000
_QUOTE_IGNORED = set(" \t\r\n:：,，;；、（）()")


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


__all__ = ["AnnouncementItem", "extract_announcement_facts"]
