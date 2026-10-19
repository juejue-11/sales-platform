"""Bounded analysis snapshots and dependency fingerprints."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import copy
import sqlite3
from ..analysis.ai import MAX_CONTEXT_BODY_CHARS, bounded_context_body, input_fingerprint
from ..analysis.analysis_policy import analysis_context
from ..core.citations import citation_fragments
from ..research.project_research import RESEARCH_POLICY_VERSION, configuration_fingerprint, list_sources
from ..documents.document_access import build_procurement_context
from ..core.utils import json_text, now_iso, sha256_text
from ..procurement import procurement_fingerprint
from .state import ApplicationState
from .errors import NotFoundError

if TYPE_CHECKING:
    from .reading_store import ProcurementReadings
    from .records import ProjectRecords


class AnalysisContext:
    def __init__(self, *, state: ApplicationState, reading_store: ProcurementReadings, records: ProjectRecords) -> None:
        self.state = state
        self.reading_store = reading_store
        self.records = records

    @staticmethod
    def analysis_record(record: dict[str, Any]) -> dict[str, Any]:
        # Keep the context self-contained while avoiding collector payloads and
        # local archive paths.  ``record_id`` is explicit because it is the
        # citation identity used by the Pi agent.
        full_body = str(record.get("body_text") or "")
        attachment_metadata = []
        for attachment in record.get("attachments", []) if isinstance(record.get("attachments"), list) else []:
            if not isinstance(attachment, dict):
                continue
            attachment_metadata.append({key: attachment.get(key) for key in (
                "name", "url", "status", "sha256", "mime_type", "byte_size", "pages",
            )})
        value = {
            "id": record.get("id"),
            "record_id": record.get("id"),
            "source_id": record.get("source_id"),
            "kind": record.get("kind"),
            "title": record.get("title"),
            "project_name": record.get("project_name"),
            "published_at": record.get("published_at"),
            "buyer": record.get("buyer"),
            "notice_number": record.get("notice_number"),
            "deadline_at": record.get("deadline_at"),
            "original_url": record.get("original_url"),
            "canonical_url": record.get("canonical_url"),
            "parse_status": record.get("parse_status"),
            "equipment": record.get("equipment", []),
            "facts": record.get("facts", []),
            "inferences": record.get("inferences", []),
            "body_text": bounded_context_body(full_body),
            "evidence_fragments": citation_fragments(full_body[:MAX_CONTEXT_BODY_CHARS]),
            # Keep the complete source identity even when the protocol body is
            # truncated, so changes in an unseen tail invalidate the input.
            "body_text_sha256": sha256_text(full_body),
            "attachments": attachment_metadata,
            "attachments_sha256": sha256_text(json_text(attachment_metadata)),
            "related_record_id": record.get("related_record_id"),
        }
        return value

    @staticmethod
    def merge_job_document_reads(current: list[dict[str, Any]], returned: Any,
                                   target_record_ids: set[int]) -> list[dict[str, Any]]:
        """Accumulate actual read windows without treating unread text as evidence."""
        registry = {(document["record_id"], document["id"]): copy.deepcopy(document) for document in current}
        for document in returned if isinstance(returned, list) else []:
            if not isinstance(document, dict) or document.get("record_id") not in target_record_ids:
                continue
            key = (document["record_id"], document.get("id"))
            saved = registry.get(key)
            if saved is None or saved.get("sha256") != document.get("sha256"):
                saved = {**copy.deepcopy(document), "read_fragments": []}
                registry[key] = saved
            fragments = document.get("page_texts", [])
            for fragment in fragments if isinstance(fragments, list) else []:
                if isinstance(fragment, dict) and fragment.get("text"):
                    value = {"page": fragment.get("page"), "text": str(fragment["text"])}
                    if value not in saved["read_fragments"]:
                        saved["read_fragments"].append(value)
            # A document-level or DOCX window has no page number; retain its
            # continuous text as a separate window, never concatenate reads.
            if document.get("text"):
                value = {"page": None, "text": str(document["text"])}
                if value not in saved["read_fragments"]:
                    saved["read_fragments"].append(value)
        return list(registry.values())

    def analysis_project_snapshot(self, project_id: int, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        project_row = self.records.project_row(project_id)
        records = records if records is not None else self.records.records_for_project(project_id)
        latest = records[0] if records else {}
        gate, gate_reasons = self.reading_store.gate(records, project_id)
        procurement = self.records.procurement_for_project(project_id)
        procurement_reading = self.reading_store.procurement_reading_for_project(project_id)
        return {
            "id": int(project_id),
            "title": latest.get("title") or f"项目 {project_id}",
            "buyer": latest.get("buyer"),
            "kind": latest.get("kind") or "tender",
            "source_id": latest.get("source_id") or "",
            "published_at": latest.get("published_at"),
            "deadline_at": latest.get("deadline_at"),
            "record_ids": [int(record["id"]) for record in records if record.get("id") is not None],
            "gate_status": gate,
            "gate_reasons": gate_reasons,
            "records": [self.analysis_record(record) for record in records],
            # The aggregate contains only server-parsed facts and document
            # metadata. Full text remains available through the trusted tool.
            "procurement": build_procurement_context(procurement),
            "procurement_fingerprint": procurement_fingerprint(procurement),
            # The AI judgment consumes the same persisted reading that cards
            # and details expose. A stale reading remains visible as evidence
            # but is explicitly marked for the model and reviewer.
            "procurement_reading": procurement_reading,
            "procurement_reading_fingerprint": sha256_text(json_text(procurement_reading)),
            "updated_at": project_row["updated_at"],
        }

    def analysis_related_context(self, project_id: int,
                                  records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Build bounded context only from explicit record refs and active links."""
        records = records if records is not None else self.records.records_for_project(project_id)
        related: dict[int, set[str]] = {}
        for record in records:
            for key in ("possible_duplicate_of", "duplicate_of", "related_record_id"):
                value = record.get(key)
                if value is None:
                    continue
                related_project = self.records.project_for_record_id(int(value))
                if related_project is not None and related_project != project_id:
                    related.setdefault(related_project, set()).add("record_ref")
        for row in self.state.db.execute(
                "SELECT project_id,target_project_id,relation FROM links "
                "WHERE active=1 AND relation IN ('same_project','duplicate') "
                "AND (project_id=? OR target_project_id=?)", (project_id, project_id)).fetchall():
            other = int(row["target_project_id"] if int(row["project_id"]) == project_id else row["project_id"])
            if other != project_id:
                related.setdefault(other, set()).add(str(row["relation"]))

        output: list[dict[str, Any]] = []
        record_count = 0
        for related_id in sorted(related)[:5]:
            related_records = self.records.records_for_project(related_id)
            if record_count >= 20 or not related_records:
                break
            remaining = 20 - record_count
            selected = related_records[:remaining]
            latest = selected[0]
            output.append({
                "id": related_id,
                "title": latest.get("title") or f"项目 {related_id}",
                "relation": ",".join(sorted(related[related_id])),
                "records": [self.analysis_record(record) for record in selected],
            })
            record_count += len(selected)
        return output

    def analysis_related_validation_records(self, related_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return full source snapshots for exactly the records sent in context.

        The SDK receives bounded bodies with an explicit truncation marker. A
        validator must never treat that marker as source evidence, so it uses
        this private snapshot captured before the provider is awaited.  Record
        IDs are taken only from the internally-built related context and are
        checked against their project membership in SQLite.
        """
        output: list[dict[str, Any]] = []
        for related_project in related_context:
            if not isinstance(related_project, dict):
                continue
            try:
                project_id = int(related_project.get("id"))
            except (TypeError, ValueError):
                continue
            record_ids: list[int] = []
            for record in related_project.get("records", []):
                if not isinstance(record, dict):
                    continue
                try:
                    record_id = int(record.get("id", record.get("record_id")))
                except (TypeError, ValueError):
                    continue
                if record_id not in record_ids:
                    record_ids.append(record_id)
            if not record_ids:
                continue
            placeholders = ",".join("?" for _ in record_ids)
            rows = self.state.db.execute(
                f"SELECT r.* FROM records r JOIN project_records pr ON pr.record_id=r.id "
                f"WHERE pr.project_id=? AND r.id IN ({placeholders})",
                (project_id, *record_ids),
            ).fetchall()
            by_id = {int(row["id"]): self.records.record_dict(row) for row in rows}
            output.extend(by_id[record_id] for record_id in record_ids if record_id in by_id)
        return output

    def related_context_fingerprints(self, project_ids: list[int] | tuple[int, ...] | None = None) -> dict[int, str]:
        """Fingerprint only the bounded related snapshots used by analysis.

        This intentionally excludes the primary project and capability card.
        It lets imports and human links invalidate conclusions that actually
        consumed a related project without expiring older conclusions that
        had no related context or were created before this feature existed.
        """
        if project_ids is None:
            project_ids = [int(row["id"]) for row in self.state.db.execute("SELECT id FROM projects ORDER BY id").fetchall()]
        fingerprints: dict[int, str] = {}
        for project_id in project_ids:
            try:
                related = self.analysis_related_context(int(project_id))
            except (NotFoundError, sqlite3.Error):
                continue
            fingerprints[int(project_id)] = sha256_text(json_text(related))
        return fingerprints

    def mark_related_context_changes(self, before: dict[int, str]) -> list[int]:
        """Mark judgments stale where an import/link changed included context."""
        changed: list[int] = []
        after = self.related_context_fingerprints(list(before))
        for project_id, old_fingerprint in before.items():
            new_fingerprint = after.get(project_id)
            if new_fingerprint is None or new_fingerprint == old_fingerprint:
                continue
            self.state.db.execute("UPDATE judgments SET stale=1 WHERE project_id=?", (project_id,))
            self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
            changed.append(project_id)
        return changed

    def analysis_context(self, project_id: int, capability: dict[str, Any],
                          records: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        records = records if records is not None else self.records.records_for_project(project_id)
        related = self.analysis_related_context(project_id, records)
        return {
            "project": self.analysis_project_snapshot(project_id, records),
            **analysis_context(capability),
            "related_projects": copy.deepcopy(related),
            "research": {"policy_version": RESEARCH_POLICY_VERSION,
                         "sources": list_sources(self.state.db, project_id)},
        }, related

    def analysis_fingerprint(self, project: dict[str, Any], capability: dict[str, Any], config: dict[str, Any],
                              related_context: list[dict[str, Any]] | None = None,
                              research_dependencies: list[dict[str, Any]] | None = None,
                              research_configuration: str | None = None) -> str:
        return sha256_text(json_text({
            "input": input_fingerprint(project, capability, related_context),
            "gate_status": project.get("gate_status"), "gate_reasons": project.get("gate_reasons", []),
            "model": config.get("model"), "base_url": config.get("base_url"),
            "enabled": config.get("enabled"), "engine": config.get("engine"),
            "research_policy": RESEARCH_POLICY_VERSION,
            "research_configuration": research_configuration or configuration_fingerprint(self.state.research_sources_path),
            "research_dependencies": research_dependencies or [],
        }))
