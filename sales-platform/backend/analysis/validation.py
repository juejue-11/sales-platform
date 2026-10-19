from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote as url_quote

from ..core.citations import resolve_citation
from ..core.utils import json_text, source_quote, source_quote_hint
from .agent_report import ReportError, derive_legacy_fields, iter_report_evidence, normalize_report
from .analysis_policy import result_analysis_meta
from .errors import AIError
from .prompts import CONTEXT_TRUNCATION_NOTICE, bounded_context_body

def extract_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("模型输出格式错误：响应缺少报告内容", code="MODEL_FORMAT_ERROR") from exc
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise AIError("模型输出格式错误：报告内容为空", code="MODEL_FORMAT_ERROR")
    return content.strip()


def _validate_research_evidence(item: dict[str, Any],
                                sources: dict[tuple[str, str], dict[str, Any]],
                                project_id: Any) -> dict[str, Any]:
    """Resolve only read, verified source fragments from this job's registry."""
    if "record_id" in item or "document_id" in item:
        raise AIError("research evidence cannot mix source identifiers")
    source_ref, version = item.get("source_ref"), item.get("source_version")
    if not isinstance(source_ref, str) or not isinstance(version, str):
        raise AIError("invalid research evidence identifier")
    source = sources.get((source_ref, version))
    if source is None or (source.get("project_id") is not None and source["project_id"] != project_id):
        raise AIError("research evidence source is not trusted")
    if (source.get("relation_status") != "verified" or
            source.get("relation") not in {"same_procurement", "related_project", "buyer_background"}):
        raise AIError(f"research evidence relation is not verified: {source_ref}; "
                      "删除该来源的引用及仅依赖它的断言，保留正文支持的事实和明确标注的推断")
    quote, page = item.get("quote"), item.get("page")
    if isinstance(page, bool) or (page is not None and (not isinstance(page, int) or page < 1)):
        raise AIError("invalid research evidence page")
    fragments = source.get("page_texts", [])
    if "fragment_id" in item:
        windows = fragments or [{"text": str(source.get("text") or "")}]
        try:
            resolved = resolve_citation(item["fragment_id"], windows, page=page)
        except ValueError as exc:
            raise AIError(str(exc)) from exc
        quote, page = resolved["quote"], resolved["page"]
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 2000:
        raise AIError("invalid research evidence quote")
    matched_quote, matched_page = None, None
    for fragment in fragments if isinstance(fragments, list) else []:
        if not isinstance(fragment, dict) or (page is not None and fragment.get("page") != page):
            continue
        matched_quote = source_quote(quote, str(fragment.get("text") or ""))
        if matched_quote is not None:
            matched_page = fragment.get("page")
            break
    if matched_quote is None and page is None and not fragments:
        matched_quote = source_quote(quote, str(source.get("text") or ""))
    if matched_quote is None:
        raise AIError("research evidence quote was not read in this job")
    value = {
        "source_ref": source_ref, "source_version": version, "quote": matched_quote,
        "title": str(source.get("title") or ""), "source_url": str(source.get("url") or ""),
        "fetched_at": source.get("fetched_at"), "relation": source["relation"],
        "snapshot_url": f"/api/projects/{project_id}/research/sources/{source_ref}/versions/{version}",
    }
    if isinstance(matched_page, int) and not isinstance(matched_page, bool) and matched_page > 0:
        value["page"] = matched_page
    return value


def validate_judgment(payload: Any, project: dict[str, Any], model: str,
                      fingerprint: str, related_records: list[dict[str, Any]] | None = None,
                      forbidden_values: list[str] | tuple[str, ...] = (),
                      document_sources: list[dict[str, Any]] | None = None,
                      company_material: dict[str, Any] | None = None,
                      research_sources: list[dict[str, Any]] | None = None,
                      require_report: bool = False) -> dict[str, Any]:
    """Validate model output before it can enter the judgments table."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raw_length = len(payload)
            position = max(0, min(int(exc.pos), raw_length))
            metadata = {
                "parser": "json",
                "position": position,
                "line": max(1, min(int(exc.lineno), 2_000_000_000)),
                "column": max(1, min(int(exc.colno), 2_000_000_000)),
                "eof": position >= raw_length,
            }
            raise AIError(
                "模型输出格式错误：报告内容不是有效 JSON",
                code="MODEL_FORMAT_ERROR",
                metadata=metadata,
            ) from exc
    if not isinstance(payload, dict):
        raise AIError("模型输出格式错误：报告内容必须是 JSON 对象", code="MODEL_FORMAT_ERROR")
    if require_report and "report" not in payload:
        raise AIError("new analysis requires a versioned report")
    try:
        serialized_payload = json_text(payload)
    except (TypeError, ValueError) as exc:
        raise AIError("AI response contains unsupported values") from exc
    if any(isinstance(value, str) and len(value) >= 3 and value in serialized_payload
           for value in forbidden_values):
        raise AIError("AI response contains restricted credential")
    decision = payload.get("decision")
    if decision not in {"recommend", "consider", "verify", "not_recommended"}:
        raise AIError("invalid AI decision")
    def required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str):
            raise AIError(f"invalid AI field: {name}")
        value = value.strip()
        if not value:
            raise AIError(f"invalid AI field: {name}")
        return value
    summary = required_text("summary")
    # Version 1 reports are the source of the flexible demand narrative.  Keep
    # demand_summary for old clients, but let a new report omit it and use the
    # concise summary as its compatibility projection.
    if "report" in payload and (payload.get("demand_summary") is None
                                or payload.get("demand_summary") == ""):
        demand_summary = summary
    else:
        demand_summary = required_text("demand_summary")
    unknowns = payload.get("unknowns", [])
    next_actions = payload.get("next_actions", [])
    if not isinstance(unknowns, list) or not all(isinstance(x, str) for x in unknowns):
        raise AIError("invalid unknowns")
    if not isinstance(next_actions, list) or not all(isinstance(x, str) for x in next_actions):
        raise AIError("invalid next_actions")
    record_map: dict[int, dict[str, Any]] = {}
    for record in [*project.get("records", []), *(related_records or [])]:
        if not isinstance(record, dict) or record.get("id") is None:
            continue
        try:
            record_map[int(record["id"])] = record
        except (TypeError, ValueError):
            continue
    document_map: dict[tuple[int, str], dict[str, Any]] = {}
    for document in document_sources or []:
        if not isinstance(document, dict):
            continue
        try:
            source_record_id = int(document.get("record_id"))
        except (TypeError, ValueError):
            continue
        document_id = document.get("id")
        if isinstance(document_id, str) and document_id:
            document_map[(source_record_id, document_id)] = document
    research_map = {
        (source["source_ref"], source["source_version"]): source
        for source in research_sources or []
        if isinstance(source, dict) and isinstance(source.get("source_ref"), str)
        and isinstance(source.get("source_version"), str)
    }
    primary_record_ids = {int(record["id"]) for record in project.get("records", [])
                          if isinstance(record, dict) and record.get("id") is not None}
    def normalize_evidence(item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise AIError("invalid evidence item")
        if "source_ref" in item or "source_version" in item:
            return _validate_research_evidence(item, research_map, project.get("id"))
        try:
            record_id = int(item.get("record_id"))
        except (TypeError, ValueError) as exc:
            raise AIError("invalid evidence record_id") from exc
        record = record_map.get(record_id)
        quote = item.get("quote")
        document_id = item.get("document_id")
        page = item.get("page")
        if document_id is not None:
            if not isinstance(document_id, str) or not document_id.strip():
                raise AIError("invalid evidence document_id")
            document = document_map.get((record_id, document_id.strip()))
            if document is None:
                raise AIError(f"evidence document is not trusted (document_id={document_id})")
            page_match: int | None = None
            if isinstance(page, bool) or (page is not None and not isinstance(page, int)):
                raise AIError("invalid evidence page")
            # A Pi run starts with no trusted attachment text.  When the
            # document bridge supplies read_fragments, only those fragments
            # count as read for this job; the durable full text/page_texts
            # snapshot must never silently widen the citation surface.
            fragment_mode = "read_fragments" in document
            fragments = document.get("read_fragments") if fragment_mode else document.get("page_texts", [])
            if not isinstance(fragments, list):
                fragments = []
            if "fragment_id" in item:
                windows = fragments if fragment_mode or fragments else [{"text": str(document.get("text") or "")}]
                try:
                    resolved = resolve_citation(item["fragment_id"], windows, page=page)
                except ValueError as exc:
                    raise AIError(str(exc)) from exc
                quote, page = resolved["quote"], resolved["page"]
            if fragment_mode:
                matched_quote = None
                for fragment in fragments:
                    if not isinstance(fragment, dict):
                        continue
                    fragment_page = fragment.get("page")
                    if page is not None and fragment_page != page:
                        continue
                    candidate_quote = source_quote(quote, str(fragment.get("text") or "")) if isinstance(quote, str) else None
                    if candidate_quote is not None:
                        matched_quote = candidate_quote
                        if isinstance(fragment_page, int) and not isinstance(fragment_page, bool) and fragment_page > 0:
                            page_match = fragment_page
                        break
            elif isinstance(page, int):
                page_text = None
                for page_value in fragments:
                    if isinstance(page_value, dict) and page_value.get("page") == page:
                        page_text = str(page_value.get("text") or "")
                        break
                if page_text is None:
                    raise AIError("evidence page is not trusted")
                matched_quote = source_quote(quote, page_text) if isinstance(quote, str) else None
                page_match = page
            else:
                # Page is optional in the model contract.  Derive the first
                # matching page from the server-read page texts so clients can
                # render a stable citation without trusting model metadata.
                matched_quote = None
                for page_value in fragments:
                    if not isinstance(page_value, dict):
                        continue
                    try:
                        candidate_page = max(1, int(page_value.get("page") or 1))
                    except (TypeError, ValueError):
                        continue
                    candidate_quote = source_quote(quote, str(page_value.get("text") or "")) if isinstance(quote, str) else None
                    if candidate_quote is not None:
                        matched_quote = candidate_quote
                        page_match = candidate_page
                        break
                if matched_quote is None:
                    matched_quote = source_quote(quote, str(document.get("text") or "")) if isinstance(quote, str) else None
        else:
            # The SDK only receives the bounded context body.  Do not accept a
            # citation copied from the hidden tail of a large record (or from
            # the truncation marker itself).
            visible_body = bounded_context_body(record.get("body_text")) if record else ""
            if CONTEXT_TRUNCATION_NOTICE in visible_body:
                visible_body = visible_body.split(CONTEXT_TRUNCATION_NOTICE, 1)[0]
            if "fragment_id" in item:
                try:
                    quote = resolve_citation(item["fragment_id"], [{"text": visible_body}])["quote"]
                except ValueError as exc:
                    raise AIError(str(exc)) from exc
            matched_quote = source_quote(quote, visible_body) if record and isinstance(quote, str) else None
            page_match = None
        if matched_quote is None:
            hint = source_quote_hint(quote, visible_body) if document_id is None and record else None
            raise AIError(f"evidence quote does not match source record (record_id={record_id}, "
                          f"quote={json_text(quote)[:320]})" +
                          (f"；可复制的连续原文={json_text(hint)}" if hint else ""))
        clean_item = {"record_id": record_id, "quote": matched_quote}
        if record_id not in primary_record_ids:
            # Linked announcements can support project-level inference, but
            # their demand is not automatically the current purchase scope.
            clean_item["relation"] = "related_project"
        if document_id is not None:
            clean_item["document_id"] = str(document_id).strip()
            # These values are copied from the trusted server snapshot.  Any
            # same-named fields supplied by the model are ignored.
            clean_item["document_name"] = str(document.get("name") or "")
            clean_item["source_url"] = str(document.get("url") or "")
            clean_item["document_sha256"] = str(document.get("sha256") or "")
            clean_item["snapshot_url"] = (
                f"/api/projects/{project.get('id')}/documents/"
                f"{url_quote(str(document_id).strip(), safe='')}"
                f"/snapshot?sha256={url_quote(str(document.get('sha256') or ''), safe='')}"
            )
            if page_match is not None:
                clean_item["page"] = page_match
        return clean_item

    def normalize_reasons(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise AIError("invalid reasons")
        normalized: list[dict[str, Any]] = []
        for reason in value:
            if not isinstance(reason, dict) or not isinstance(reason.get("text"), str) or not reason.get("text", "").strip():
                raise AIError("invalid reason")
            evidence = reason.get("evidence", [])
            if not isinstance(evidence, list):
                raise AIError("invalid evidence")
            normalized.append({"text": reason["text"].strip(),
                               "evidence": [normalize_evidence(item) for item in evidence]})
        return normalized

    normalized_report = None
    if "report" in payload:
        # Return all distinct quotation errors together so the one correction
        # attempt can fix them, instead of discovering a second typo too late.
        evidence_errors: list[str] = []
        for item in iter_report_evidence(payload.get("report")):
            try:
                normalize_evidence(item)
            except AIError as exc:
                message = str(exc)
                if message not in evidence_errors:
                    evidence_errors.append(message)
        if evidence_errors:
            raise AIError("无效报告引用：" + "; ".join(evidence_errors[:8]))
        try:
            normalized_report = normalize_report(payload.get("report"), normalize_evidence=normalize_evidence)
        except ReportError as exc:
            raise AIError(str(exc), code="MODEL_FORMAT_ERROR" if exc.format_error else None) from exc
    derived_reasons: list[dict[str, Any]] = []
    derived_unknowns: list[str] = []
    derived_actions: list[str] = []
    if normalized_report is not None:
        derived_reasons, derived_unknowns, derived_actions = derive_legacy_fields(normalized_report)
    normalized_reasons = normalize_reasons(payload.get("reasons", []))
    if normalized_report is not None:
        if not normalized_reasons:
            normalized_reasons = derived_reasons
        else:
            existing = {(item["text"], json_text(item["evidence"])) for item in normalized_reasons}
            normalized_reasons.extend(item for item in derived_reasons
                                      if (item["text"], json_text(item["evidence"])) not in existing)
        unknowns = list(unknowns) + [item for item in derived_unknowns if item not in unknowns]
        next_actions = list(next_actions) + [item for item in derived_actions if item not in next_actions]
    if decision in {"recommend", "consider"}:
        if not normalized_reasons or not any(item["evidence"] for item in normalized_reasons):
            raise AIError("positive AI judgment requires source evidence")
    options = payload.get("options", [])
    if not isinstance(options, list) or len(options) > 3:
        raise AIError("options must contain 0 to 3 items")
    normalized_options = []
    option_ids = set()
    for option in options:
        if not isinstance(option, dict):
            raise AIError("invalid option")
        option_id = option.get("id")
        if not isinstance(option_id, str) or not option_id.strip() or option_id in option_ids:
            raise AIError("invalid option id")
        option_ids.add(option_id)
        fields = {}
        for name in ("title", "description", "suitable_for", "next_action"):
            if not isinstance(option.get(name), str):
                raise AIError(f"invalid option field: {name}")
            fields[name] = option[name].strip()
        for name in ("tradeoffs", "unknowns"):
            value = option.get(name, [])
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise AIError(f"invalid option field: {name}")
            fields[name] = [x.strip() for x in value]
        fields["id"] = option_id.strip()
        normalized_options.append(fields)
    # Keep the historical options guard for legacy payloads.  A version 1
    # report classifies an opportunity by its evidence and relation; the
    # collector kind alone must not force a fixed report shape.
    if normalized_report is None and project.get("kind") == "news" and options:
        # A news lead may have verification actions but no product delivery plan.
        for option in normalized_options:
            material = f"{option['title']} {option['description']}"
            if any(word in material for word in ("供货方案", "报价方案", "采购方案", "交付方案")):
                raise AIError("news judgment contains a procurement option")
    company_material = company_material or {}
    try:
        analysis_meta = result_analysis_meta(payload.get("analysis_meta"), company_material)
    except ValueError as exc:
        raise AIError(str(exc)) from exc
    result = {
        "created_at": None,
        "model": model,
        "input_fingerprint": fingerprint,
        "stale": False,
        "decision": decision,
        "summary": summary,
        "demand_summary": demand_summary,
        "reasons": normalized_reasons,
        "unknowns": [x.strip() for x in unknowns],
        "next_actions": [x.strip() for x in next_actions],
        "options": normalized_options,
        "analysis_meta": analysis_meta,
        "report": normalized_report,
    }
    # Fragment references expand to server-owned text after input validation.
    # Apply the credential boundary to those derived quotes as well.
    serialized_result = json_text(result)
    if any(isinstance(value, str) and len(value) >= 3 and value in serialized_result
           for value in forbidden_values):
        raise AIError("AI response contains restricted credential")
    return result
