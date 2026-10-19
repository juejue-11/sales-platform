"""Validation and compatibility helpers for the agent's flexible report.

The report is intentionally a small structural protocol.  The model chooses
the sections and their order, while this module owns the parts that must stay
stable: bounded strings, unique identifiers, evidence normalization, and the
legacy fields used by older API clients.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from typing import Any


REPORT_VERSION = 1
REPORT_MAX_BYTES = 256_000
REPORT_MAX_SECTIONS = 32
REPORT_MAX_BLOCKS = 64
REPORT_MAX_ITEMS = 128
REPORT_MAX_ROWS = 128
REPORT_MAX_COLUMNS = 32
REPORT_MAX_OPPORTUNITIES = 64
REPORT_MAX_GAPS = 64
REPORT_MAX_ID_LENGTH = 128
REPORT_MAX_TEXT_LENGTH = 20_000
REPORT_MAX_CELL_LENGTH = 4_000
REPORT_MAX_EVIDENCE_PER_NODE = 32

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OPPORTUNITY_KINDS = {"explicit_demand", "potential_opportunity"}
_GAP_STATUSES = {"not_stated", "not_read", "read_failed", "not_applicable"}
_GAP_LABELS = {"not_stated": "原文未说明", "not_read": "尚未读取",
               "read_failed": "读取失败", "not_applicable": "不适用"}
_FORBIDDEN_EXPLICIT_RELATIONS = {"buyer_background", "related_project"}


class ReportError(ValueError):
    """Raised when a model report cannot be accepted by the protocol."""

    def __init__(self, message: str, *, format_error: bool = True) -> None:
        super().__init__(message)
        self.format_error = format_error


EvidenceNormalizer = Callable[[dict[str, Any]], dict[str, Any]]


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError(f"invalid report {name}")
    return value


def _text(value: Any, name: str, *, required: bool = True, limit: int = REPORT_MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str):
        raise ReportError(f"invalid report {name}")
    value = value.strip()
    if required and not value:
        raise ReportError(f"invalid report {name}")
    if len(value) > limit:
        raise ReportError(f"report {name} is too long")
    return value


def _id(value: Any, name: str, ids: set[str]) -> str:
    value = _text(value, name, limit=REPORT_MAX_ID_LENGTH)
    if not _ID_RE.fullmatch(value):
        raise ReportError(f"invalid report {name}")
    if value in ids:
        raise ReportError(f"duplicate report id: {value}")
    ids.add(value)
    return value


def _strings(value: Any, name: str, *, limit: int = REPORT_MAX_ITEMS) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ReportError(f"invalid report {name}")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_text(item, f"{name}[{index}]"))
    return result


def _evidence(value: Any, name: str, normalize: EvidenceNormalizer) -> list[dict[str, Any]]:
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > REPORT_MAX_EVIDENCE_PER_NODE:
        raise ReportError(f"invalid report {name}")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ReportError(f"invalid report {name}[{index}]")
        try:
            normalized = normalize(item)
        except Exception as exc:
            # Keep the public validation error stable while preserving the
            # useful evidence rejection reason from the backend normalizer.
            raise ReportError(str(exc), format_error=False) from exc
        if not isinstance(normalized, dict):
            raise ReportError(f"invalid report {name}[{index}]")
        result.append(normalized)
    return result


def _paragraph(value: Any, name: str, normalize: EvidenceNormalizer) -> dict[str, Any]:
    item = _object(value, name)
    block_type = item.get("type", "paragraph")
    if block_type != "paragraph":
        raise ReportError(f"invalid report {name}.type")
    return {
        "type": "paragraph",
        "text": _text(item.get("text"), f"{name}.text"),
        "evidence": _evidence(item.get("evidence", []), f"{name}.evidence", normalize),
    }


def _block(value: Any, name: str, normalize: EvidenceNormalizer) -> dict[str, Any]:
    item = _object(value, name)
    block_type = item.get("type")
    if block_type == "paragraph":
        return _paragraph(item, name, normalize)
    if block_type == "list":
        raw_items = item.get("items")
        if not isinstance(raw_items, list) or not raw_items or len(raw_items) > REPORT_MAX_ITEMS:
            raise ReportError(f"invalid report {name}.items")
        clean_items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_items):
            item_name = f"{name}.items[{index}]"
            list_item = _object(raw_item, item_name)
            clean_items.append({
                "text": _text(list_item.get("text"), f"{item_name}.text"),
                "evidence": _evidence(list_item.get("evidence", []),
                                        f"{item_name}.evidence", normalize),
            })
        return {"type": "list", "items": clean_items}
    if block_type == "table":
        columns = _strings(item.get("columns"), f"{name}.columns", limit=REPORT_MAX_COLUMNS)
        if not columns:
            raise ReportError(f"invalid report {name}.columns")
        raw_rows = item.get("rows", [])
        if not isinstance(raw_rows, list) or len(raw_rows) > REPORT_MAX_ROWS:
            raise ReportError(f"invalid report {name}.rows")
        rows: list[dict[str, Any]] = []
        for index, raw_row in enumerate(raw_rows):
            row_name = f"{name}.rows[{index}]"
            row = _object(raw_row, row_name)
            cells = row.get("cells")
            if not isinstance(cells, list) or len(cells) != len(columns):
                raise ReportError(f"invalid report {row_name}.cells")
            # An empty cell is a real, reviewable "not stated" value.  The
            # row still needs a cell for every declared column, but the model
            # must not invent text merely to fill the table shape.
            clean_cells = [_text(cell, f"{row_name}.cells[{cell_index}]", required=False,
                                 limit=REPORT_MAX_CELL_LENGTH)
                           for cell_index, cell in enumerate(cells)]
            rows.append({"cells": clean_cells,
                         "evidence": _evidence(row.get("evidence", []),
                                                f"{row_name}.evidence", normalize)})
        return {"type": "table", "columns": columns, "rows": rows}
    raise ReportError(f"invalid report {name}.type")


def _section(value: Any, index: int, ids: set[str], normalize: EvidenceNormalizer) -> dict[str, Any]:
    item = _object(value, f"sections[{index}]")
    section_id = _id(item.get("id"), f"sections[{index}].id", ids)
    title = _text(item.get("title"), f"sections[{index}].title")
    blocks = item.get("blocks")
    if not isinstance(blocks, list):
        raise ReportError(f"invalid report sections[{index}].blocks: expected an array, got {type(blocks).__name__}")
    if len(blocks) > REPORT_MAX_BLOCKS:
        raise ReportError(f"report sections[{index}].blocks exceeds {REPORT_MAX_BLOCKS} items")
    return {"id": section_id, "title": title,
            "blocks": [_block(block, f"sections[{index}].blocks[{block_index}]", normalize)
                       for block_index, block in enumerate(blocks)]}


def _opportunity(value: Any, index: int, ids: set[str], normalize: EvidenceNormalizer) -> dict[str, Any]:
    item = _object(value, f"opportunities[{index}]")
    opportunity_id = _id(item.get("id"), f"opportunities[{index}].id", ids)
    kind = item.get("kind")
    if kind not in _OPPORTUNITY_KINDS:
        raise ReportError(f"invalid report opportunities[{index}].kind")
    evidence = _evidence(item.get("evidence", []), f"opportunities[{index}].evidence", normalize)
    if kind == "potential_opportunity":
        if not evidence:
            raise ReportError(f"potential opportunity requires evidence: opportunities[{index}].evidence (id={opportunity_id})",
                              format_error=False)
        if not _text(item.get("basis"), f"opportunities[{index}].basis"):
            raise ReportError("potential opportunity requires basis", format_error=False)
        uncertainties = _strings(item.get("uncertainties"), f"opportunities[{index}].uncertainties")
        next_actions = _strings(item.get("next_actions"), f"opportunities[{index}].next_actions")
        if not uncertainties or not next_actions:
            raise ReportError(f"potential opportunity requires uncertainties and next_actions: opportunities[{index}] (id={opportunity_id})",
                              format_error=False)
    else:
        uncertainties = _strings(item.get("uncertainties", []), f"opportunities[{index}].uncertainties")
        next_actions = _strings(item.get("next_actions", []), f"opportunities[{index}].next_actions")
    if kind == "explicit_demand" and not evidence:
        raise ReportError(f"explicit demand requires evidence: opportunities[{index}].evidence (id={opportunity_id})",
                          format_error=False)
    if kind == "explicit_demand":
        for evidence_item in evidence:
            if evidence_item.get("relation") in _FORBIDDEN_EXPLICIT_RELATIONS:
                raise ReportError("explicit demand cannot use background or related-project evidence", format_error=False)
    return {
        "id": opportunity_id,
        "kind": kind,
        "title": _text(item.get("title"), f"opportunities[{index}].title"),
        "description": _text(item.get("description"), f"opportunities[{index}].description"),
        "basis": _text(item.get("basis"), f"opportunities[{index}].basis"),
        "uncertainties": uncertainties,
        "next_actions": next_actions,
        "evidence": evidence,
    }


def _gap(value: Any, index: int, ids: set[str]) -> dict[str, Any]:
    item = _object(value, f"gaps[{index}]")
    status = item.get("status")
    if status not in _GAP_STATUSES:
        raise ReportError(f"invalid report gaps[{index}].status")
    worth = item.get("worth_researching")
    if not isinstance(worth, bool):
        raise ReportError(f"invalid report gaps[{index}].worth_researching")
    return {
        "id": _id(item.get("id"), f"gaps[{index}].id", ids),
        "description": _text(item.get("description"), f"gaps[{index}].description"),
        "status": status,
        "impact": _text(item.get("impact"), f"gaps[{index}].impact"),
        "next_action": _text(item.get("next_action"), f"gaps[{index}].next_action", required=False),
        "worth_researching": worth,
    }


def normalize_report(value: Any, *, normalize_evidence: EvidenceNormalizer,
                     max_bytes: int = REPORT_MAX_BYTES) -> dict[str, Any]:
    """Validate and canonicalize a version 1 report.

    ``normalize_evidence`` is supplied by :mod:`backend.analysis.validation`; this keeps source
    and document trust rules in one place while this module owns the report
    protocol itself.
    """
    report = _object(value, "report")
    if type(report.get("version")) is not int or report.get("version") != REPORT_VERSION:
        raise ReportError(f"unsupported report version: report.version must be integer {REPORT_VERSION}; "
                          f"got {type(report.get('version')).__name__}")
    sections = report.get("sections")
    opportunities = report.get("opportunities", [])
    gaps = report.get("gaps", [])
    if not isinstance(sections, list) or len(sections) > REPORT_MAX_SECTIONS:
        raise ReportError("invalid report sections")
    if not isinstance(opportunities, list) or len(opportunities) > REPORT_MAX_OPPORTUNITIES:
        raise ReportError("invalid report opportunities")
    if not isinstance(gaps, list) or len(gaps) > REPORT_MAX_GAPS:
        raise ReportError("invalid report gaps")
    ids: set[str] = set()
    normalized = {
        "version": REPORT_VERSION,
        "sections": [_section(item, index, ids, normalize_evidence)
                     for index, item in enumerate(sections)],
        "opportunities": [_opportunity(item, index, ids, normalize_evidence)
                          for index, item in enumerate(opportunities)],
        "gaps": [_gap(item, index, ids) for index, item in enumerate(gaps)],
    }
    # Enforce a serialized size cap after canonicalization.  This catches
    # deeply nested but individually legal values before they reach SQLite or
    # the API response.
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ReportError("report is too large")
    return normalized


def iter_report_evidence(report: Any) -> Iterator[dict[str, Any]]:
    """Yield every canonical evidence object in a normalized report."""
    if not isinstance(report, dict):
        return
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "paragraph":
                yield from _iter_evidence(block.get("evidence"))
            elif block.get("type") == "list":
                for item in block.get("items", []):
                    if isinstance(item, dict):
                        yield from _iter_evidence(item.get("evidence"))
            elif block.get("type") == "table":
                for row in block.get("rows", []):
                    if isinstance(row, dict):
                        yield from _iter_evidence(row.get("evidence"))
    for opportunity in report.get("opportunities", []):
        if isinstance(opportunity, dict):
            yield from _iter_evidence(opportunity.get("evidence"))


def _iter_evidence(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def derive_legacy_fields(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Project a dynamic report into the historical reasons/unknowns/actions."""
    reasons: list[dict[str, Any]] = []
    unknowns: list[str] = []
    next_actions: list[str] = []

    def add_reason(text: str, evidence: list[dict[str, Any]]) -> None:
        if evidence:
            reasons.append({"text": text, "evidence": evidence})

    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks", []):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "paragraph":
                add_reason(str(block.get("text") or ""), list(block.get("evidence") or []))
            elif block_type == "list":
                for item in block.get("items", []):
                    if isinstance(item, dict):
                        add_reason(str(item.get("text") or ""), list(item.get("evidence") or []))
            elif block_type == "table":
                columns = [str(column) for column in block.get("columns", [])]
                for row in block.get("rows", []):
                    if not isinstance(row, dict):
                        continue
                    cells = [str(cell) for cell in row.get("cells", [])]
                    text = "；".join(f"{column}：{cell}" for column, cell in zip(columns, cells))
                    add_reason(text, list(row.get("evidence") or []))
    for opportunity in report.get("opportunities", []):
        if not isinstance(opportunity, dict):
            continue
        evidence = list(opportunity.get("evidence") or [])
        add_reason(f"{opportunity.get('title')}: {opportunity.get('description')}", evidence)
        unknowns.extend(str(item) for item in opportunity.get("uncertainties", []) if isinstance(item, str))
        next_actions.extend(str(item) for item in opportunity.get("next_actions", []) if isinstance(item, str))
    for gap in report.get("gaps", []):
        if not isinstance(gap, dict):
            continue
        status = str(gap.get("status") or "")
        description = str(gap.get("description") or "")
        if description and status != "not_applicable":
            label = _GAP_LABELS.get(status, status)
            unknowns.append(f"{description}（{label}）" if label else description)
        action = str(gap.get("next_action") or "")
        if action:
            next_actions.append(action)
    return reasons, _unique_strings(unknowns), _unique_strings(next_actions)


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = [
    "REPORT_VERSION",
    "ReportError",
    "derive_legacy_fields",
    "iter_report_evidence",
    "normalize_report",
]
