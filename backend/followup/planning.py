"""Pure, deterministic generation of a follow-up task proposal.

``generate_plan`` consumes the reading module's plain JSON explanation.  It
never imports the service, opens a database, calls a model, or invents an
external submission.  In particular, a news or potential-project lead can be
displayed to a user but cannot yield a confirmed tender task list.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .errors import FollowupValidationError

BASES = ("required", "conditional", "suggested")
TASK_STATUSES = ("todo", "doing", "done", "skipped")
MATERIAL_STATUSES = ("not_prepared", "preparing", "provided", "pending_check", "needs_supplement")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any, prefix: str, length: int = 16) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()[:length]}"


def _iso_date(value: Any, *, field: str = "due_at") -> str | None:
    """Validate and normalize a date or ISO-8601 datetime.

    Python's ``fromisoformat`` is intentionally used after a narrow shape
    check.  This rejects natural-language dates, impossible calendar dates,
    and timestamps with an unrecognized suffix.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            return parsed.isoformat(timespec="seconds")
        return parsed.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    raw = _text(value)
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise FollowupValidationError(f"{field} is not a valid date") from exc
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?",
        raw,
    ):
        raise FollowupValidationError(f"{field} must be ISO date or datetime")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FollowupValidationError(f"{field} is not a valid date") from exc
    return parsed.isoformat(timespec="seconds")


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "是"}
    return bool(value)


def _is_material_section(section: str) -> bool:
    text = _text(section).lower()
    return any(token in text for token in (
        "material", "qualification", "credential", "participation", "fee", "document",
        "材料", "资格", "资质", "费用", "怎样参加", "参与", "办理",
    ))


def _has_source(item: Mapping[str, Any]) -> bool:
    sources = item.get("sources")
    return bool(sources) or bool(item.get("source_ref")) or isinstance(item.get("source"), Mapping)


def _is_concrete_action(item: Mapping[str, Any], *, section: str) -> bool:
    text = " ".join(_text(item.get(key)).lower() for key in ("title", "name", "text", "explanation", "label", "kind"))
    return _text(section).lower() == "milestones" or any(token in text for token in (
        "register", "registration", "document_access", "obtain", "get file", "submit", "submission",
        "certificate", "question", "opening", "报名", "注册", "获取文件", "购买文件", "提交", "证书",
        "提问", "开标", "缴费", "保证金",
    ))


def _is_material_requirement(item: Mapping[str, Any], *, section: str) -> bool:
    """Whether one source requirement represents a user-provided material."""

    if any(key in item for key in ("materials", "required_materials", "documents", "attachments", "material")):
        return True
    section_text = _text(section).lower()
    text = " ".join(_text(item.get(key)).lower() for key in ("title", "name", "text", "explanation"))
    if any(token in section_text for token in ("material", "qualification", "credential", "资质", "资格", "材料")):
        # A qualification section may also describe a platform action.  Keep
        # those as tasks and only retain actual proof/document requirements as
        # materials.
        if any(token in text for token in ("注册", "报名", "获取文件", "购买文件", "提问", "缴费", "保证金", "开标")) \
                and not any(token in text for token in ("证书", "证明", "营业执照", "授权", "资质", "资格", "材料", "文件清单")):
            return False
        return True
    if any(token in section_text for token in ("participation", "怎样参加", "参与", "费用", "fee")):
        return any(token in text for token in (
            "证书", "证明", "营业执照", "授权", "资质", "资格", "材料", "文件", "清单", "报告", "凭证", "回执",
        )) and not any(token in text for token in ("获取文件", "购买文件"))
    return False


def _source_basis(item: Mapping[str, Any], *, section: str) -> str:
    """Map source provenance and workflow conditions to a plan category.

    A normalized reading contains many factual requirements (buyer, scope,
    delivery).  Those facts inform a suggested check; only an explicit
    classification or a source-backed concrete action/material is required.
    """

    raw: Any = item.get("basis")
    explicit = raw is not None
    if raw is None:
        raw = item.get("source_type", item.get("source_kind"))
        explicit = raw is not None
    if raw is None and isinstance(item.get("source"), str):
        raw = item.get("source")
        explicit = True
    if raw is None:
        raw = item.get("status")
        explicit = raw is not None
    if isinstance(raw, Mapping):
        raw = raw.get("basis", raw.get("type", raw.get("kind", raw.get("classification"))))
        explicit = raw is not None
    text = " ".join(_text(value).lower() for value in (
        raw, item.get("text"), item.get("explanation"), item.get("label"), item.get("kind"),
    ))
    if _bool(item.get("suggested")) or _bool(item.get("ai_suggested")) or any(
        token in text for token in ("suggest", "recommend", "建议", "可选")
    ):
        return "suggested"
    if _bool(item.get("conditional")) or _bool(item.get("when")) or any(
        token in text for token in ("conditional", "condition", "条件", "视", "如适用", "如满足", "若", "如果")
    ):
        return "conditional"
    if explicit and any(token in text for token in ("required", "mandatory", "must", "required", "必", "规定")):
        return "required"
    if _bool(item.get("required")) or _bool(item.get("mandatory")):
        return "required"
    if _has_source(item) and (_is_concrete_action(item, section=section) or _is_material_section(section)):
        return "required"
    return "suggested"


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [value]
    if isinstance(value, Mapping):
        # Some readers group requirements by section.  Keep a single mapping
        # with a usable title as one item, otherwise flatten its values.
        if any(key in value for key in ("title", "name", "text", "requirement", "action", "id")):
            return [value]
        flattened: list[Any] = []
        for child in value.values():
            flattened.extend(_as_items(child))
        return flattened
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return list(value)
    raise FollowupValidationError("requirements/milestones must be arrays or objects")


def _item_title(item: Any, *, fallback: str) -> str:
    if isinstance(item, Mapping):
        for key in ("title", "name", "text", "requirement", "action", "description", "label"):
            value = _text(item.get(key))
            if value:
                return value
    value = _text(item)
    if value:
        return value
    raise FollowupValidationError(f"{fallback} item must have a title")


def _item_value(item: Any, *keys: str) -> Any:
    if not isinstance(item, Mapping):
        return None
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _materials_for(item: Any) -> list[Any]:
    if not isinstance(item, Mapping):
        return []
    value = _item_value(item, "materials", "required_materials", "documents", "attachments", "material")
    return _as_items(value)


def _material_name(item: Any) -> str:
    if isinstance(item, Mapping):
        return _item_title(item, fallback="material")
    value = _text(item)
    if not value:
        raise FollowupValidationError("material name must not be blank")
    return value


def _procedure(explanation: Mapping[str, Any]) -> str:
    value = explanation.get("procedure", explanation.get("procurement_process"))
    if value is None:
        value = explanation.get("purchase_method", explanation.get("method", explanation.get("kind")))
    text = _text(value).lower()
    if any(token in text for token in ("询价", "inquiry", "rfq", "quote", "quotation")):
        return "inquiry"
    if any(token in text for token in ("公开招标", "邀请招标", "招标", "tender", "bid", "rfp")):
        return "tender"
    if any(token in text for token in ("比选", "竞争性谈判", "竞争性磋商", "negotiation", "proposal")):
        return "competitive"
    milestones = explanation.get("milestones")
    if isinstance(milestones, Sequence) and not isinstance(milestones, (str, bytes, bytearray)):
        kinds = {_text(item.get("kind")).lower() for item in milestones if isinstance(item, Mapping)}
        if "submission" in kinds or "opening" in kinds:
            return "tender"
        if "question" in kinds and "submission" not in kinds:
            return "inquiry"
    return text or "unspecified"


def _is_lead(explanation: Mapping[str, Any]) -> bool:
    if any(_bool(explanation.get(key)) for key in ("is_potential_lead", "potential_lead", "news_lead", "is_news")):
        return True
    kind = _text(explanation.get("demand_type", explanation.get("kind", explanation.get("type", explanation.get("status"))))).lower()
    return any(token in kind for token in ("news", "lead", "potential", "线索", "意向", "规划"))


def _source_ref(item: Any) -> str:
    if not isinstance(item, Mapping):
        return ""
    source = item.get("source")
    if source is None:
        sources = item.get("sources")
        if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes, bytearray)):
            source = sources[0] if sources else None
    if isinstance(source, Mapping):
        source_ref = source.get("source_ref", source.get("id"))
        if source_ref is not None:
            source = source_ref
        else:
            source_kind = _text(source.get("source_kind", item.get("source_kind", "")))
            record_id = _text(source.get("record_id", item.get("record_id", "")))
            document_id = _text(source.get("document_id", item.get("document_id", "")))
            source = ":".join(part for part in (source_kind, record_id, document_id) if part)
    return _text(item.get("source_ref", item.get("source_id", source)))


def _build_item(item: Any, *, section: str, index: int, kind: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    title = _item_title(item, fallback=section)
    basis = _source_basis(item if isinstance(item, Mapping) else {}, section=section)
    source_ref = _source_ref(item)
    due_value = _item_value(item, "due_at", "deadline", "date", "at", "completed_by")
    due_at = _iso_date(due_value) if due_value not in (None, "") else None
    key = _item_value(item, "id", "key", "code") if isinstance(item, Mapping) else None
    stable_key = _text(key) or f"{section}:{index}:{title}"
    task_id = _digest({"key": stable_key, "title": title, "basis": basis}, "task", 12)
    prerequisites = _item_value(item, "prerequisites", "depends_on", "dependencies")
    prerequisites = _as_items(prerequisites) if prerequisites is not None else []
    prerequisites = [_text(value) for value in prerequisites if _text(value)]
    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "basis": basis,
        "owner": "",
        "due_at": due_at,
        "prerequisites": prerequisites,
        "materials": [],
        "status": "todo",
        "completion_note": "",
        "source_ref": source_ref,
        "source_section": section,
    }
    materials: list[dict[str, Any]] = []
    for material_index, raw_material in enumerate(_materials_for(item)):
        material_name = _material_name(raw_material)
        material_basis = _source_basis(raw_material if isinstance(raw_material, Mapping) else {}, section=section)
        material_key = _item_value(raw_material, "id", "key", "code") if isinstance(raw_material, Mapping) else None
        stable_material_key = _text(material_key) or f"{task_id}:{material_index}:{material_name}"
        material_id = _digest({"key": stable_material_key, "name": material_name}, "material", 12)
        material = {
            "id": material_id,
            "task_id": task_id,
            "name": material_name,
            "basis": material_basis,
            "status": "not_prepared",
            "ai_check_status": "not_checked",
            "ai_check_note": "",
            "provided_at": None,
            "note": "",
            "source_ref": _source_ref(raw_material),
        }
        materials.append(material)
        task["materials"].append(material_id)
    return task, materials


def generate_plan(explanation: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic, reviewable plan proposal from a reading result.

    The plan is intentionally not a task list in persistence.  ``adopt_plan``
    must be called explicitly before any generated task or material is saved.
    """

    if not isinstance(explanation, Mapping):
        raise FollowupValidationError("procurement explanation must be an object")
    sections = explanation.get("sections") or []
    if sections is None:
        sections = []
    if not isinstance(sections, (Mapping, Sequence)) or isinstance(sections, (str, bytes, bytearray)):
        raise FollowupValidationError("sections must be an object or array")
    kind = _text(explanation.get("kind", explanation.get("type", explanation.get("demand_type", "procurement")))) or "procurement"
    demand_type = _text(explanation.get("demand_type", "")).lower()
    procedure = _procedure(explanation)
    lead = _is_lead(explanation)
    if demand_type == "lead":
        lead = True
    confirmed = demand_type == "confirmed" or _bool(explanation.get("confirmed", explanation.get("is_confirmed", False)))
    if not demand_type and procedure != "unspecified":
        confirmed = True
    # The reading contract has already separated confirmed procurement from a
    # news/market lead.  A confirmed reading is sufficient even when its
    # procedure is absent; only explicit milestones/requirements are used.
    if demand_type == "unknown":
        confirmed = False
    if lead or not confirmed:
        plan = {
            "id": _digest({"kind": kind, "procedure": procedure, "lead": lead}, "plan"),
            "eligible": False,
            "reason": "潜在线索尚未确认采购程序，不能生成投标办理流程。",
            "kind": kind,
            "procedure": procedure,
            "tasks": [],
            "materials": [],
        }
        return plan

    if isinstance(sections, Mapping):
        raw_requirements = sections.get("requirements", explanation.get("requirements", []))
        raw_milestones = sections.get("milestones", explanation.get("milestones", []))
        raw_suggestions = sections.get("suggestions", sections.get("recommendations", []))
        section_values = [{"id": "requirements", "title": "采购要求", "requirements": raw_requirements}]
    else:
        section_values = [item for item in sections if isinstance(item, Mapping)]
        raw_requirements = explanation.get("requirements", [])
        raw_milestones = explanation.get("milestones", [])
        raw_suggestions = explanation.get("suggestions", explanation.get("recommendations", []))
    tasks: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    # The canonical reading shape is sections[{id,title,requirements}].  A
    # section becomes one check/preparation task and its individual source
    # requirements become materials.  This keeps a long material list from
    # becoming a dozen fake workflow tasks.
    for section_index, section_value in enumerate(section_values):
        section_id = _text(section_value.get("id")) or f"section-{section_index + 1}"
        section_title = _text(section_value.get("title")) or section_id
        requirements = _as_items(section_value.get("requirements", []))
        if not requirements:
            continue
        section_key = f"{section_id} {section_title}"
        requirement_bases = [
            _source_basis(requirement, section=section_key) if isinstance(requirement, Mapping) else "suggested"
            for requirement in requirements
        ]
        section_basis = "required" if "required" in requirement_bases else (
            "conditional" if "conditional" in requirement_bases else "suggested"
        )
        task_id = _digest({"section": section_id, "title": section_title, "basis": section_basis}, "task", 12)
        task = {
            "id": task_id,
            "title": f"核对/准备{section_title}",
            "basis": section_basis,
            "owner": "",
            "due_at": None,
            "prerequisites": [],
            "materials": [],
            "status": "todo",
            "completion_note": "",
            "source_ref": _source_ref(section_value) or next(
                (_source_ref(item) for item in requirements if isinstance(item, Mapping) and _source_ref(item)),
                "",
            ),
            "source_section": section_id,
        }
        for requirement_index, requirement in enumerate(requirements):
            if isinstance(requirement, Mapping):
                requirement_title = _item_title(requirement, fallback="requirement")
                material_basis = _source_basis(requirement, section=section_key)
                requirement_key = _text(requirement.get("id")) or f"{section_id}:{requirement_index}:{requirement_title}"
                material_source = _source_ref(requirement)
            else:
                requirement_title = _item_title(requirement, fallback="requirement")
                material_basis = "required"
                requirement_key = f"{section_id}:{requirement_index}:{requirement_title}"
                material_source = ""
            material_id = _digest({"key": requirement_key, "name": requirement_title}, "material", 12)
            include_material = isinstance(requirement, Mapping) and _is_material_requirement(
                requirement, section=section_key
            )
            if include_material:
                materials.append({
                    "id": material_id,
                    "task_id": task_id,
                    "name": requirement_title,
                    "basis": material_basis,
                    "status": "not_prepared",
                    "ai_check_status": "not_checked",
                    "ai_check_note": "",
                    "provided_at": None,
                    "note": "",
                    "source_ref": material_source,
                })
                task["materials"].append(material_id)
        tasks.append(task)

    # Compatibility input (and the canonical top-level milestones list) still
    # create one task per explicit process node.  No generic tender steps are
    # inserted when the reading did not state them.
    for section, raw_items in (("milestones", raw_milestones), ("suggestions", raw_suggestions)):
        for index, raw_item in enumerate(_as_items(raw_items)):
            task, item_materials = _build_item(raw_item, section=section, index=index, kind=kind)
            tasks.append(task)
            materials.extend(item_materials)
    plan_core = {
        "eligible": True,
        "kind": kind,
        "procedure": procedure,
        "tasks": tasks,
        "materials": materials,
    }
    plan = {"id": _digest(plan_core, "plan"), **plan_core}
    return plan


def plan_fingerprint(plan: Mapping[str, Any]) -> str:
    """Stable fingerprint used to make adoption idempotent."""

    if not isinstance(plan, Mapping):
        raise FollowupValidationError("plan must be an object")
    comparable = dict(plan)
    comparable.pop("id", None)
    comparable.pop("generated_at", None)
    return hashlib.sha256(_canonical(comparable).encode("utf-8")).hexdigest()


__all__ = [
    "BASES",
    "MATERIAL_STATUSES",
    "TASK_STATUSES",
    "generate_plan",
    "plan_fingerprint",
]
