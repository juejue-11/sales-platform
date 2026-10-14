from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..core.utils import parse_timestamp


GATE_VALUES = {"candidate", "review", "excluded", "expired", "closed", "signal", "duplicate"}
AI_DECISIONS = {"recommend", "consider", "verify", "not_recommended"}


def business_sort_key(record: dict[str, Any]) -> tuple[float, int, float, int]:
    """Order records by source business time before fetch time.

    A late re-fetch of an old notice must not replace a newer correction or
    award in the project view. Lifecycle notices get a small tie-break priority
    when their published dates are equal or absent.
    """
    published = parse_timestamp(record.get("published_at"))
    fetched = parse_timestamp(record.get("fetched_at"))
    published_value = published.timestamp() if published else -1.0
    fetched_value = fetched.timestamp() if fetched else -1.0
    kind = str(record.get("kind") or "").lower()
    lifecycle_priority = {"correction": 5, "award": 4, "tender": 3, "inquiry": 3, "news": 1}.get(kind, 2)
    return published_value, lifecycle_priority, fetched_value, int(record.get("id") or 0)


def _contains_any(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword in text]


def evaluate_record(record: dict[str, Any], settings: dict[str, Any], *, now: datetime | None = None) -> tuple[str, list[str]]:
    """Conservative, explainable gate. Missing facts never become exclusions."""
    business = settings.get("business", {}) if isinstance(settings, dict) else {}
    title = str(record.get("title") or "")
    body = str(record.get("body_text") or "")
    text = f"{title}\n{body}"
    kind = str(record.get("kind") or "").lower()
    reasons: list[str] = []

    if record.get("duplicate_of") is not None:
        return "duplicate", [f"确认重复记录，原记录 {record['duplicate_of']}"]

    if kind == "news":
        reasons.append("新闻或行业动态仅作为待核实线索")
        if record.get("parse_status") not in (None, "", "ok"):
            reasons.append(f"解析状态：{record.get('parse_status')}")
        return "signal", reasons

    if kind == "award":
        reasons.append("成交/中标公告保留为项目结果")
        return "closed", reasons

    parse_status = str(record.get("parse_status") or "")
    if parse_status and parse_status not in {"ok", "ai_fallback"}:
        reasons.append(f"解析状态需核实：{parse_status}")

    possible_duplicate = record.get("possible_duplicate_of")
    if possible_duplicate is not None:
        reasons.append(f"存在疑似重复提示（原记录 {possible_duplicate}），未自动合并")

    exclude_keywords = [str(x).strip() for x in business.get("exclude_keywords", []) if str(x).strip()]
    excluded = _contains_any(text, exclude_keywords)
    if excluded:
        reasons.append("命中明确排除条件：" + "、".join(excluded))
        return "excluded", reasons

    deadline = parse_timestamp(record.get("deadline_at"), deadline=True)
    if deadline and now and deadline < now:
        reasons.append("参与截止时间已过，且当前记录未说明延期")
        # A correction can change the deadline, but an already expired correction
        # remains visible as expired until a later source record updates it.
        return "expired", reasons

    if not record.get("title") or not record.get("body_text"):
        reasons.append("标题或正文缺失，保留并待核实")
    if not record.get("equipment"):
        reasons.append("设备字段为空，需结合正文核实")
    if not record.get("deadline_at"):
        reasons.append("未提供截止时间，不据此淘汰")
    if possible_duplicate is not None or reasons:
        return "review", reasons

    # A configured target list is used only as an explanation. Lack of a match
    # cannot prove that the company is incapable of serving the opportunity.
    targets = [str(x).strip() for x in business.get("target_keywords", []) if str(x).strip()]
    if targets and not _contains_any(text, targets):
        return "review", ["未命中配置的目标关键词，仍需人工核对产品适配"]
    return "candidate", []


def gate_for_project(records: list[dict[str, Any]], settings: dict[str, Any], *, now: datetime | None = None,
                     override: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    if not records:
        return "review", ["项目暂无采集记录"]
    # Prefer the newest record for deadline and kind, while preserving duplicate
    # and source quality warnings from all records.
    ordered = sorted(records, key=business_sort_key, reverse=True)
    primary_gate, reasons = evaluate_record(ordered[0], settings, now=now)
    all_reasons = list(reasons)
    for record in ordered[1:]:
        gate, extra = evaluate_record(record, settings, now=now)
        if gate == "duplicate" and primary_gate != "duplicate":
            all_reasons.extend(extra)
        elif gate == "review":
            for reason in extra:
                if reason not in all_reasons:
                    all_reasons.append(reason)
    if override and override.get("override_action") == "restore" and primary_gate == "excluded":
        primary_gate = "review"
        all_reasons = ["人工恢复规则排除"] + [x for x in all_reasons if not x.startswith("命中明确排除条件")]
    if primary_gate not in GATE_VALUES:
        primary_gate = "review"
    return primary_gate, all_reasons


def decision_label(decision: str | None) -> str | None:
    return decision if decision in AI_DECISIONS else None
