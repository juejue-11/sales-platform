"""Deterministic eligibility rules for equipment procurement notices.

The collector calls this module after a detail page has been fetched.  The
functions deliberately return a small, stable reason code rather than raising
or making a best-effort classification.  Publication dates are interpreted in
the user's local business timezone (Asia/Shanghai).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from .extract import EQUIPMENT


LOCAL_TZ = ZoneInfo("Asia/Shanghai")

REJECTION_LABELS = {
    "published_too_old": "发布日期超出保留时间窗口",
    "published_in_future": "发布日期晚于当前时间",
    "missing_published_at": "缺少发布日期",
    "invalid_published_at": "发布日期无效",
    "non_procurement": "非采购公告",
    "closed_notice": "中标、成交、候选结果等非在售采购公告",
    "non_equipment": "非设备或备件货物采购",
    "submission_expired": "投标、报价或响应文件截止时间已过",
    "title_keyword_miss": "标题未命中来源关键词",
    "detail_keyword_miss": "详情未命中来源关键词",
}


@dataclass(frozen=True)
class _ParsedDate:
    day: date
    instant: datetime | None


_DATE_TOKEN = re.compile(
    r"(?<!\d)20\d{2}\s*(?:年|[-/.])\s*\d{1,2}\s*(?:月|[-/.])\s*\d{1,2}\s*(?:日|号)?"
    r"(?:[T\s]+\d{1,2}\s*(?::|时)\s*\d{1,2}(?:\s*(?::|分)\s*\d{1,2})?\s*(?:秒)?"
    r"(?:\s*(?:Z|[+-]\d{2}:?\d{2}))?)?"
)
_DATE_CORE = re.compile(
    r"20\d{2}\s*(?:年|[-/.])\s*\d{1,2}\s*(?:月|[-/.])\s*\d{1,2}(?:日|号)?"
)


def _local_now(now: datetime) -> datetime:
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.tzinfo is None:
        return now.replace(tzinfo=LOCAL_TZ)
    return now.astimezone(LOCAL_TZ)


def _date_from_match(match: re.Match[str], raw: str) -> _ParsedDate | None:
    parts = re.findall(r"20\d{2}|\d{1,2}", match.group(0))
    if len(parts) < 3:
        return None
    try:
        day = date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None

    # A date without a clock is intentionally a whole local calendar day.
    core = _DATE_CORE.match(match.group(0))
    tail = (match.group(0)[core.end() :] if core else "") + raw[match.end() :]
    clock = re.match(
        r"\s*(?:T|日\s*)?(\d{1,2})\s*(?::|时)\s*(\d{1,2})"
        r"(?:\s*(?::|分)\s*(\d{1,2}))?\s*(Z|[+-]\d{2}:?\d{2})?",
        tail,
    )
    if not clock:
        return _ParsedDate(day, None)
    try:
        tm = time(int(clock.group(1)), int(clock.group(2)), int(clock.group(3) or 0))
    except ValueError:
        return None
    suffix = clock.group(4)
    if suffix == "Z":
        tz = ZoneInfo("UTC")
    elif suffix:
        sign = 1 if suffix[0] == "+" else -1
        offset = suffix[1:].replace(":", "")
        try:
            from datetime import timezone

            tz = timezone(sign * timedelta(hours=int(offset[:2]), minutes=int(offset[2:])))
        except (ValueError, IndexError):
            return None
    else:
        tz = LOCAL_TZ
    return _ParsedDate(day, datetime.combine(day, tm, tzinfo=tz))


def _parse_date(value: object) -> _ParsedDate | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=LOCAL_TZ)
        local = value.astimezone(LOCAL_TZ)
        return _ParsedDate(local.date(), value)
    if isinstance(value, date):
        return _ParsedDate(value, None)
    if not isinstance(value, str):
        return None
    raw = html.unescape(value).strip()
    if not raw:
        return None

    # ISO/RFC values are common in listing adapters.  The explicit regex below
    # also accepts Chinese date punctuation and avoids accepting arbitrary text.
    iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        if re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", raw):
            return _ParsedDate(parsed.date(), None)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        local = parsed.astimezone(LOCAL_TZ)
        return _ParsedDate(local.date(), parsed)
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        local = parsed.astimezone(LOCAL_TZ)
        return _ParsedDate(local.date(), parsed)

    match = _DATE_TOKEN.search(raw)
    return _date_from_match(match, raw) if match else None


def publication_rejection(
    value: object, *, now: datetime, max_age_days: int = 30
) -> str | None:
    """Return a rejection code for a missing, invalid, old, or future date.

    The cutoff is inclusive: with a local date of 16 October and the default
    window, 16 September is retained.  A date-only value published today is
    retained for the entire local day.
    """

    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days <= 0:
        raise ValueError("max_age_days must be a positive integer")
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing_published_at"
    local_now = _local_now(now)
    parsed = _parse_date(value)
    if parsed is None:
        return "invalid_published_at"
    if parsed.day > local_now.date():
        return "published_in_future"
    if parsed.instant is not None and parsed.instant.astimezone(LOCAL_TZ) > local_now:
        return "published_in_future"
    if parsed.day < local_now.date() - timedelta(days=max_age_days):
        return "published_too_old"
    return None


# Only these labels establish a submission deadline.  In particular, generic
# ``截止时间`` and document-purchase/registration deadlines are absent.
_SUBMISSION_LABEL = re.compile(
    r"(?:投标(?:文件)?(?:递交|提交)?截止(?:时间)?|"
    r"报价(?:文件)?(?:递交|提交)?截止(?:时间)?|"
    r"响应(?:投标|报价|[（(]投标[/／]报价[）)])?文件(?:递交|提交)?截止(?:时间)?)"
)
_SUBMISSION_BEFORE = re.compile(
    r"(?:投标|报价|响应)(?:文件)?\s*(?:应于|须于|应在|须在)\s*"
    r"(?P<date>20\d{2}\s*(?:年|[-/.])\s*\d{1,2}\s*(?:月|[-/.])\s*\d{1,2}\s*(?:日|号)?"
    r"(?:[T\s]+\d{1,2}\s*(?::|时)\s*\d{1,2}(?:\s*(?::|分)\s*\d{1,2})?)?)"
    r"\s*(?:之前|以前|前)\s*(?:递交|提交|上传|送达)"
)


def _visible_lines(value: object) -> list[str]:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style|header|footer|nav)\b.*?</\1\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines: list[str] = []
    for part in re.split(r"[\r\n。；;]+", text):
        line = part.strip()
        if line:
            lines.append(line)
    return lines


_BOILERPLATE = re.compile(
    r"^(?:项目背景|项目概况|建设背景|企业简介|公司简介|资格(?:审查|条件)?|"
    r"历史业绩|类似业绩|业绩要求|投标人(?:须知|应|需)|采购人|招标人|"
    r"代理机构|评标|评审|公告发布|联系方式|页眉|页脚|header|footer|"
    r"网站头部|网站导航|顶部导航|通用头部)\s*[：:]?"
)
_BOILERPLATE_ANY = (
    "项目背景", "项目概况", "建设背景", "企业简介", "公司简介", "资格", "历史业绩",
    "类似业绩", "业绩要求", "投标人", "营业执照", "信用", "履约", "评标", "评审",
    "招标人", "采购人", "代理机构",
)
_SCOPE_MARKERS = (
    "采购范围",
    "采购内容",
    "采购物资",
    "设备清单",
    "设备名称",
    "货物名称",
    "货物清单",
    "供货范围",
    "标的物",
    "标的名称",
)


def _scope_lines(body: object) -> list[str]:
    lines = []
    for line in _visible_lines(body):
        if _BOILERPLATE.match(line) or any(mark in line for mark in _BOILERPLATE_ANY):
            continue
        explicit = any(mark in line for mark in _SCOPE_MARKERS)
        action_positions = [line.find(mark) for mark in ("采购", "招标", "询比", "询价", "供货")
                            if line.find(mark) >= 0]
        equipment_positions = [line.find(term) for term in tuple(EQUIPMENT) + _EXTRA_EQUIPMENT
                               if term and line.find(term) >= 0]
        procurement_sentence = bool(action_positions and equipment_positions
                                     and min(action_positions) < min(equipment_positions))
        if explicit or procurement_sentence:
            lines.append(line)
    return lines


def _title_subject(title: object) -> str:
    value = re.sub(r"\s+", "", str(title or ""))
    # Organization prefixes are useful for display but are not the purchased
    # subject.  Repeating the operation handles nested [org]【org】 prefixes.
    while True:
        stripped = re.sub(r"^(?:【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\))", "", value)
        if stripped == value:
            return value
        value = stripped


_EXTRA_EQUIPMENT = (
    "电动机", "电机", "液压支柱", "油泵", "给料机", "减速机", "变压器",
    "阀门", "压滤机", "浮选机", "空压机", "除尘器", "输送设备", "存储设备",
    "生产设备", "机加工设备", "洗选装备", "压裂泵", "调制解调仪", "过滤器",
    "装载机", "柴油机",
)
_HARD_SERVICE_TERMS = (
    "修复服务", "维修服务", "检修服务", "维护服务", "保养服务", "技术服务",
    "监测服务", "监测技术", "租赁", "出租", "劳务", "分包", "设计服务",
    "咨询服务", "运营服务",
)
_SOFT_SERVICE_TERMS = ("施工", "安装服务", "调试服务")
_MATERIAL_TERMS = (
    "钢材", "钢结构", "水泥", "砂石", "木材", "建筑材料", "原材料",
    "施工材料", "材料采购", "材料招标", "制备材料", "化工产品", "化工材料", "填料", "药剂",
)
_PROCUREMENT_TERMS = (
    "采购", "招标", "询比", "询价", "供货", "货物", "物资", "备件",
    "设备清单", "采购范围", "采购内容",
)
_CLOSED_TERMS = (
    "中标公告", "成交公告", "中标结果", "成交结果", "结果公告", "候选人公示",
    "结果公示", "评标结果", "定标结果", "中选公告", "中选结果", "矿业权", "采矿权", "探矿权",
)
_CLOSED_HEADING = re.compile(
    r"^(?:公告类型|公告标题|采购结果|公告状态)\s*[：:]\s*.*(?:中标|成交|结果|矿业权|采矿权|探矿权)"
)


def _contains_equipment(text: str) -> bool:
    terms = tuple(EQUIPMENT) + _EXTRA_EQUIPMENT
    return any(term and term in text for term in terms)


def _explicit_goods_scope(scope: Iterable[str]) -> bool:
    text = "\n".join(scope)
    if not text:
        return False
    if not _contains_equipment(text) and not re.search(r"(?:设备|装备)", text):
        return False
    return any(mark in text for mark in _PROCUREMENT_TERMS)


def _is_closed_notice(title: str, lines: Iterable[str]) -> bool:
    if any(marker in title for marker in _CLOSED_TERMS):
        return True
    return any(_CLOSED_HEADING.match(line) for line in lines)


def _normalize_fragmented_date(text: str) -> str:
    """Join OCR/HTML line fragments that split digits or date punctuation."""

    text = re.sub(r"(?<=\d)\s+(?=\d|年|月|日|号|时|分|秒|:)", "", text)
    text = re.sub(r"(?<=[年月号])\s+(?=\d)", "", text)
    return re.sub(r"(?<=日)\s*(?=\d)", " ", text)


def _is_deadline_reference(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 16) : match.start()]
    after = text[match.end() : match.end() + 32]
    return bool(
        re.search(r"(?:应|须)(?:于|在)\s*$", before)
        or re.match(r"\s*(?:前|之前|以前)(?:递交|提交|上传|送达)", after)
    )


def _label_date(text: str, match: re.Match[str]) -> _ParsedDate | None:
    after = text[match.end() : match.end() + 90]
    date_match = _DATE_TOKEN.search(after)
    if not date_match:
        return None
    prefix = after[: date_match.start()]
    if prefix.strip(" \t\r\n:：,，、()（）[]【】为是于在"):
        return None
    candidate = after[date_match.start() : date_match.end() + 40]
    return _parse_date(candidate)


def _deadline_dates(body: object) -> tuple[list[_ParsedDate], bool]:
    text = _normalize_fragmented_date("\n".join(_visible_lines(body)))
    dates: list[_ParsedDate] = []
    uncertain = False
    for match in _SUBMISSION_LABEL.finditer(text):
        if _is_deadline_reference(text, match):
            continue
        parsed = _label_date(text, match)
        if parsed is None:
            uncertain = True
        else:
            dates.append(parsed)
    for match in _SUBMISSION_BEFORE.finditer(text):
        start = match.start("date")
        parsed = _parse_date(text[start : match.end("date") + 30])
        if parsed is None:
            uncertain = True
        else:
            dates.append(parsed)
    return dates, uncertain


def _expired_submission(parsed: _ParsedDate, local_now: datetime) -> bool:
    if parsed.day < local_now.date():
        return True
    if parsed.day > local_now.date() or parsed.instant is None:
        return False
    return parsed.instant.astimezone(LOCAL_TZ) < local_now


def notice_rejection(
    title: object,
    body: object,
    category: object,
    published_at: object,
    *,
    now: datetime,
    max_age_days: int = 30,
) -> str | None:
    """Return the first deterministic reason a detail notice is ineligible."""

    if category != "procurement":
        return "non_procurement"
    publication = publication_rejection(published_at, now=now, max_age_days=max_age_days)
    if publication:
        return publication

    title_text = _title_subject(title)
    body_lines = _visible_lines(body)
    scope = _scope_lines(body)
    searchable_scope = "\n".join(scope)
    if _is_closed_notice(title_text, body_lines):
        return "closed_notice"

    # A title-level equipment subject is strong evidence.  Generic ``设备`` is
    # accepted only when the title/body explicitly describes goods scope.
    title_has_concrete = _contains_equipment(title_text)
    equipment = title_has_concrete or _explicit_goods_scope(scope)

    # Service and raw-material words count only in the title or an explicit
    # procurement-scope line; background and qualifications are intentionally
    # ignored.  Installation/commissioning remain valid when goods are bought.
    subject_scope = "\n".join((title_text, searchable_scope))
    if any(term in subject_scope for term in _MATERIAL_TERMS):
        return "non_equipment"
    title_has_goods_signal = any(term in title_text for term in _PROCUREMENT_TERMS)
    scope_has_goods_signal = any(
        term in searchable_scope for term in ("供货", "货物", "物资", "备件", "设备清单")
    )
    if any(term in title_text for term in _HARD_SERVICE_TERMS):
        return "non_equipment"
    scope_services = [term for term in _HARD_SERVICE_TERMS if term in searchable_scope]
    if scope_services and (
        any(term != "技术服务" for term in scope_services)
        or not (title_has_goods_signal or scope_has_goods_signal)
    ):
        return "non_equipment"
    if any(term in title_text for term in _SOFT_SERVICE_TERMS):
        if not any(term in title_text for term in _PROCUREMENT_TERMS):
            return "non_equipment"
        # A service label attached to an equipment title is still a service
        # notice unless the title explicitly buys/supplies the equipment.
        if not re.search(r"(?:设备|装备|备件|货物)?(?:采购|招标|询比|询价|供货)", title_text):
            return "non_equipment"
    if any(term in searchable_scope for term in _SOFT_SERVICE_TERMS) and not re.search(
        r"(?:采购|招标|询比|询价|供货|货物|物资)", searchable_scope
    ):
        return "non_equipment"
    if not equipment:
        return "non_equipment"

    deadlines, uncertain = _deadline_dates(body)
    if deadlines and not uncertain and all(
        _expired_submission(item, _local_now(now)) for item in deadlines
    ):
        return "submission_expired"
    return None
