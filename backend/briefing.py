"""Deterministic excerpts for the procurement home page.

The source export is the authority for these fields.  This module deliberately
does not call the AI provider: a briefing is a small, bounded view of the
latest source record and must remain useful when AI is disabled or stale.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


BRIEFING_KEYS = (
    "record_id",
    "scope_excerpt",
    "delivery_excerpt",
    "qualification_excerpt",
    "contact_excerpt",
    "original_url",
)

_SCOPE_LABELS = (
    "采购范围",
    "招标范围",
    "采购内容",
    "采购标的",
    "货物清单",
    "项目需求",
    "项目概况",
)
_DELIVERY_LABELS = (
    "交货期",
    "交付期限",
    "交货时间",
    "交付时间",
    "供货期",
    "交付期",
    "交货地点",
    "交付地点",
    "供货地点",
)
_QUALIFICATION_LABELS = (
    "投标人资格要求",
    "供应商资格要求",
    "投标人资格",
    "供应商资格",
    "资格要求",
    "资格条件",
    "资质要求",
    "投标资格",
    "资格审查",
)
_CONTACT_LABELS = (
    "采购人联系方式",
    "招标人联系方式",
    "项目联系人",
    "采购联系人",
    "联系人信息",
    "联系方式",
    "联系信息",
    "联系人",
)

_GROUPS = {
    "scope": _SCOPE_LABELS,
    "delivery": _DELIVERY_LABELS,
    "qualification": _QUALIFICATION_LABELS,
    "contact": _CONTACT_LABELS,
}
_ALL_LABELS = tuple(sorted({label for labels in _GROUPS.values() for label in labels}, key=len, reverse=True))
_SENTENCE_RE = re.compile(r"[^。；;！？!?]*[。；;！？!?]|[^。；;！？!?]+$")
# Require whitespace before an Arabic heading.  Without it, a phone number
# ending in ``...19658.`` would be mistaken for section ``8.``.
_TRAILING_HEADING_RE = re.compile(r"(?:[一二三四五六七八九十百]+[、.．]|\s+\d+(?:\.\d+)?[、.．])\s*$")
_CJK_CHARS = r"\u3400-\u9fff\uf900-\ufaff"
_CJK_RE = rf"[{_CJK_CHARS}]"
_HEADING_SPACE_RE = re.compile(r"\s+(?=\d+(?:\.\d+)?[、.．](?:\s|[A-Za-z\u3400-\u9fff\uf900-\ufaff]))")
_JOIN_CJK_NUMBER_RE = re.compile(rf"(?<={_CJK_RE})\s+(?=[{_CJK_CHARS}0-9])")
_JOIN_NUMBER_CJK_RE = re.compile(rf"(?<=\d)\s+(?=[{_CJK_CHARS}0-9])")
_PUNCT_SPACE_RE = re.compile(r"\s+([，。；：、！？,.!?;:）】》])")
_OPEN_SPACE_RE = re.compile(r"([（(【《])\s+")

_BOILERPLATE = ("公告开始", "公告如下", "现将", "报名时间", "文件出售", "文件获取", "平台登录", "登录平台")
_PLATFORM_CONTACT = ("平台客服", "客服", "技术支持", "系统客服", "服务热线", "平台服务")
_PLATFORM_SPLIT_RE = re.compile(r"(?=(?:平台客服|系统客服|技术支持|服务热线|平台服务|客服))")
_NON_PROCUREMENT_CONTACT = ("异议", "举报", "投诉", "监督")
_CONTACT_CUES = ("联系人", "联系电话", "电话", "手机", "邮箱", "传真", "地址", "采购人", "招标人", "项目负责人")
_PROCUREMENT_CUES = ("采购", "招标", "设备", "货物", "物资", "标的", "清单", "压裂泵", "泵")
_QUALIFICATION_CUES = ("资格", "资质", "业绩", "证书", "许可", "矿安", "防爆", "安全生产")
_NEGATIVE_FILE_CUES = ("报名", "文件", "购买", "下载", "登录", "平台", "保证金")
_STRONG_FILE_CUES = ("文件出售", "文件获取", "文件售价", "购买方式", "标书费", "报名时间", "登录平台", "平台登录")
_EXTRA_BOUNDARIES = {
    "contact": ("异议", "举报", "投诉", "监督", "CA办理方式", "招标文件出售", "电子招标投标注意事项", "注："),
}


def empty_briefing(record_id: int | None = None, original_url: str | None = None) -> dict[str, Any]:
    """Return a stable shape for records without usable source text."""

    return {
        "record_id": record_id,
        "scope_excerpt": "",
        "delivery_excerpt": "",
        "qualification_excerpt": "",
        "contact_excerpt": "",
        "original_url": original_url,
    }


def normalize_source_text(value: Any) -> str:
    """Collapse collector whitespace without splitting Chinese words or units.

    Collector HTML extraction often emits one Chinese token or digit per line.
    CJK/CJK, CJK/number, and number/unit spaces are presentation artefacts and
    are removed.  Spaces between Latin words remain intact.
    """

    text = str(value or "").replace("\u00a0", " ")
    # Keep the separator before a numbered heading.  Otherwise a phone ending
    # in ``...1965`` followed by the next section ``8.`` becomes the false
    # number ``...19658`` when all collector newlines are collapsed.
    heading_guard = "\ue000"
    text = _HEADING_SPACE_RE.sub(heading_guard, text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _JOIN_CJK_NUMBER_RE.sub("", text)
    text = _JOIN_NUMBER_CJK_RE.sub("", text)
    text = _PUNCT_SPACE_RE.sub(r"\1", text)
    text = _OPEN_SPACE_RE.sub(r"\1", text)
    return text.replace(heading_guard, " ").strip()


def _clip(text: str, limit: int) -> str:
    """Bound an excerpt while preserving only source characters."""

    text = text.strip()
    if len(text) <= limit:
        return text
    # Prefer a complete source sentence when one is available before the hard
    # limit.  No ellipsis is appended: that would no longer be an excerpt.
    prefix = text[:limit]
    boundary = max(prefix.rfind(mark) for mark in ("。", "；", ";", "！", "？", "!", "?"))
    if boundary >= max(20, limit - 90):
        return prefix[: boundary + 1].rstrip()
    return prefix.rstrip()


def _marker_positions(text: str, labels: Iterable[str]) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for label in labels:
        start = 0
        while True:
            position = text.find(label, start)
            if position < 0:
                break
            positions.append((position, label))
            start = position + len(label)
    return sorted(positions, key=lambda value: (value[0], -len(value[1])))


def _valid_marker(text: str, group: str, position: int, label: str) -> bool:
    """Reject common inline references that look like section headings."""

    suffix = text[position + len(label):].lstrip()
    if group == "scope" and label == "采购内容" and suffix.startswith(("页", "文件")):
        # Qualification evidence often says “合同首页、采购内容页及签字盖章
        # 页”.  That is not the notice's procurement content.
        return False
    if group == "contact":
        prefix = text[max(0, position - 4):position]
        if prefix.endswith(_NON_PROCUREMENT_CONTACT):
            return False
    return True


def _section_candidates(text: str, group: str) -> list[tuple[str, str]]:
    """Return ``(label, section)`` candidates for one field.

    Other field labels act as section boundaries.  Labels within the same
    group are intentionally not boundaries because ``交货期`` and
    ``交货地点`` commonly belong to one delivery paragraph.
    """

    labels = _GROUPS[group]
    starts = [(position, label) for position, label in _marker_positions(text, labels)
              if _valid_marker(text, group, position, label)]
    if not starts:
        return []
    other_labels = tuple(label for name, values in _GROUPS.items() if name != group for label in values)
    boundaries = _marker_positions(text, other_labels)
    boundaries.extend(_marker_positions(text, _EXTRA_BOUNDARIES.get(group, ())))
    result: list[tuple[str, str]] = []
    for start, label in starts:
        next_positions = [position for position, _ in boundaries if position > start]
        end = min(next_positions) if next_positions else len(text)
        section = _TRAILING_HEADING_RE.sub("", text[start:end]).strip(" \t:：-—")
        if section:
            result.append((label, section))
    return result


def _sentence_candidates(text: str) -> list[str]:
    return [part.strip() for match in _SENTENCE_RE.finditer(text) if (part := match.group(0).strip())]


def _has_quantity(text: str) -> bool:
    number = r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万]+)"
    unit = r"(?:套|台|件|个|吨|公斤|千克|米|批|项|台套|辆|部|组|只|块|箱|次|年|月|日|工作日)"
    return bool(re.search(rf"(?:{number}\s*{unit}|{unit}\s*{number})", text))


def _scope_score(label: str, text: str) -> int:
    score = {
        "采购范围": 65,
        "招标范围": 62,
        "采购内容": 60,
        "采购标的": 58,
        "货物清单": 56,
        "项目需求": 48,
        "项目概况": 30,
    }.get(label, 0)
    if _has_quantity(text):
        score += 42
    score += min(24, sum(6 for cue in _PROCUREMENT_CUES if cue in text))
    if any(token in text for token in _STRONG_FILE_CUES):
        score -= 70
    elif any(token in text for token in ("投标文件", "保证金")):
        # Procurement sections commonly mention bid files or a deposit in a
        # caveat.  That alone must not outweigh a concrete equipment table.
        score -= 8
    if any(token in text for token in _BOILERPLATE):
        score -= 22
    return score


def _select_scope(text: str, *, is_news: bool) -> str:
    sections = _section_candidates(text, "scope")
    if sections:
        label, section = max(sections, key=lambda item: _scope_score(*item))
        if _scope_score(label, section) >= (18 if is_news else 35):
            return _clip(section, 450)

    sentences = _sentence_candidates(text)
    scored: list[tuple[int, str]] = []
    for sentence in sentences:
        score = 0
        if _has_quantity(sentence):
            score += 35
        score += min(30, sum(6 for cue in _PROCUREMENT_CUES if cue in sentence))
        if any(token in sentence for token in _NEGATIVE_FILE_CUES):
            score -= 50
        if any(token in sentence for token in _BOILERPLATE):
            score -= 25
        if len(sentence) < 8:
            score -= 10
        scored.append((score, sentence))
    if scored:
        score, sentence = max(scored, key=lambda item: item[0])
        if score >= (4 if is_news else 12):
            return _clip(sentence, 450)
    if is_news:
        # A news item may have no procurement labels.  Keep a substantive
        # sentence, while skipping a bare announcement heading/opening.
        for sentence in sentences:
            if len(sentence) >= 12 and not any(token in sentence for token in _BOILERPLATE):
                return _clip(sentence, 450)
    return ""


def _delivery_score(label: str, text: str) -> int:
    score = 42 if label in {"交货期", "交付期限", "交货时间", "交付时间", "供货期", "交付期"} else 35
    if re.search(r"(?:\d+|[一二三四五六七八九十百千万]+)\s*(?:日|天|月|年|小时|工作日)", text):
        score += 38
    if any(token in text for token in ("地点", "地址", "矿区", "现场", "送达", "交付至")):
        score += 24
    if any(token in text for token in _NEGATIVE_FILE_CUES):
        score -= 45
    return score


def _select_delivery(text: str) -> str:
    sections = _section_candidates(text, "delivery")
    if sections:
        label, section = max(sections, key=lambda item: _delivery_score(*item))
        score = _delivery_score(label, section)
        if score >= 55 and ("期" in label or "时间" in label or "地点" in label or "期限" in label):
            return _clip(section, 240)
    candidates = []
    for sentence in _sentence_candidates(text):
        if any(token in sentence for token in ("交货", "交付", "供货")):
            score = _delivery_score("交货期", sentence)
            candidates.append((score, sentence))
    if candidates:
        score, sentence = max(candidates, key=lambda item: item[0])
        if score >= 55:
            return _clip(sentence, 240)
    return ""


def _qualification_score(label: str, text: str) -> int:
    score = 58 if label in {"投标人资格要求", "供应商资格要求", "投标人资格", "供应商资格"} else 45
    score += min(35, sum(7 for cue in _QUALIFICATION_CUES if cue in text))
    if "业绩" in text:
        score += 12
    if any(token in text for token in _NEGATIVE_FILE_CUES):
        score -= 45
    return score


def _select_qualification(text: str) -> str:
    sections = _section_candidates(text, "qualification")
    if sections:
        label, section = max(sections, key=lambda item: _qualification_score(*item))
        if _qualification_score(label, section) >= 52:
            return _clip(section, 350)
    candidates = []
    for sentence in _sentence_candidates(text):
        if any(cue in sentence for cue in _QUALIFICATION_CUES):
            score = _qualification_score("资格要求", sentence)
            candidates.append((score, sentence))
    if candidates:
        score, sentence = max(candidates, key=lambda item: item[0])
        if score >= 55:
            return _clip(sentence, 350)
    return ""


def _contact_parts(text: str) -> list[str]:
    parts: list[str] = []
    for sentence in _sentence_candidates(text):
        # Some HTML extractors omit punctuation between contact rows.  Split
        # before platform support labels so a real buyer contact survives.
        parts.extend(part.strip() for part in _PLATFORM_SPLIT_RE.split(sentence) if part.strip())
    # A contact block often uses a colon without a terminal full stop.  The
    # sentence splitter still returns the complete trailing block.
    selected: list[str] = []
    for part in parts:
        if not any(cue in part for cue in _CONTACT_CUES):
            continue
        if any(token in part for token in _PLATFORM_CONTACT + _NON_PROCUREMENT_CONTACT):
            continue
        selected.append(part)
    return selected


def _contact_score(label: str, text: str) -> int:
    score = 65 if label in {"采购人联系方式", "招标人联系方式", "采购联系人", "项目联系人"} else 42
    score += min(36, sum(6 for cue in _CONTACT_CUES if cue in text))
    if any(token in text for token in _PLATFORM_CONTACT):
        score -= 75
    if any(token in text for token in _NON_PROCUREMENT_CONTACT):
        score -= 100
    return score


def _select_contact(text: str) -> str:
    sections = _section_candidates(text, "contact")
    candidates: list[tuple[int, str]] = []
    for label, section in sections:
        parts = _contact_parts(section)
        if parts:
            # Keep source wording and punctuation while dropping platform-only
            # support lines.  The separator is only used between source parts.
            candidate = "；".join(parts)
            candidates.append((_contact_score(label, candidate), candidate))
    if candidates:
        score, candidate = max(candidates, key=lambda item: item[0])
        if score >= 42:
            return _clip(candidate, 240)
    # A notice may have no “联系方式” heading but still provide a procurement
    # contact inline.  Platform-only service numbers remain excluded.
    parts = _contact_parts(text)
    if parts:
        candidate = "；".join(parts)
        if any(token in candidate for token in ("采购人", "招标人", "联系人", "联系电话")):
            return _clip(candidate, 240)
    return ""


def build_briefing(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build bounded source excerpts for one latest record."""

    if not isinstance(record, Mapping):
        return empty_briefing()
    raw_record_id = record.get("id") if record.get("id") is not None else record.get("record_id")
    try:
        record_id = int(raw_record_id) if raw_record_id is not None else None
    except (TypeError, ValueError):
        record_id = None
    original_url = str(record.get("original_url") or record.get("canonical_url") or "").strip() or None
    result = empty_briefing(record_id, original_url)
    text = normalize_source_text(record.get("body_text"))
    if not text:
        return result
    is_news = str(record.get("kind") or "").strip().lower() == "news"
    result["scope_excerpt"] = _select_scope(text, is_news=is_news)
    if not is_news:
        result["delivery_excerpt"] = _select_delivery(text)
        result["qualification_excerpt"] = _select_qualification(text)
    result["contact_excerpt"] = _select_contact(text)
    return result


# Public aliases keep the small module convenient for focused unit tests and
# for callers that use “extract” terminology.
extract_briefing = build_briefing
extract_project_briefing = build_briefing


__all__ = [
    "BRIEFING_KEYS",
    "build_briefing",
    "empty_briefing",
    "extract_briefing",
    "extract_project_briefing",
    "normalize_source_text",
]
