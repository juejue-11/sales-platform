"""Attachment acquisition, persistence and authorized document snapshots."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

import asyncio
from ..analysis.ai import AIError
from ..documents.document_access import DocumentAccessError, build_document_view, validate_document_access_arguments
from ..core.utils import json_text, now_iso, sha256_text
from ..documents.public_documents import fetch_record_documents, read_archived_documents
from ..procurement import document_from_row, document_public_metadata, normalize_document, procurement_fingerprint
from .state import ApplicationState
from .errors import ServiceError, NotFoundError

if TYPE_CHECKING:
    from .reading_store import ProcurementReadings
    from .records import ProjectRecords
    from .settings import ApplicationSettings


class ProjectDocuments:
    def __init__(
        self, *,
        state: ApplicationState,
        reading_store: ProcurementReadings,
        records: ProjectRecords,
        settings: ApplicationSettings,
        queue_reading: Callable[..., Any],
    ) -> None:
        self.state = state
        self.reading_store = reading_store
        self.records = records
        self.settings = settings
        self.queue_reading = queue_reading

    async def refresh_record_documents(self, project_id: int, records: list[dict[str, Any]], *,
                                        preloaded: dict[int, list[dict[str, Any]]] | None = None,
                                        queue_reading: bool = True) -> dict[str, Any]:
        """Fetch and persist attachment documents without invoking an AI provider."""
        before = self.records.procurement_for_project(project_id)
        target_record_ids = [int(record["id"]) for record in records]
        previous = self.records.document_sources_for_records(target_record_ids)
        fetched: list[dict[str, Any]] = []
        for record in records:
            record_id = int(record["id"])
            request_record = {
                "id": record_id, "record_id": record_id,
                "source_url": record.get("original_url") or record.get("canonical_url") or "",
                "original_url": record.get("original_url") or "",
                "canonical_url": record.get("canonical_url") or "",
                "body_text": record.get("body_text") or "",
                "attachments": record.get("attachments") if isinstance(record.get("attachments"), list) else [],
            }
            try:
                result = (preloaded[record_id] if preloaded is not None and record_id in preloaded
                          else await fetch_record_documents(request_record))
                if not isinstance(result, list):
                    raise ValueError("invalid_document_result")
            except Exception as exc:
                raw_code = str(exc).strip()
                safe_code = raw_code if raw_code in {"auth_required", "source_unreachable", "download_failed",
                                                      "attachment_too_large", "redirect_limit", "invalid_source_url",
                                                      "attachment_outside_source", "non_public_source"} else "fetch_failed"
                result = [{"id": f"fetch-{record_id}", "record_id": record_id,
                           "name": "附件获取失败", "url": request_record["source_url"],
                           "status": "auth_required" if safe_code == "auth_required" else "failed",
                           "error": safe_code}]
            for raw in result:
                document = normalize_document(raw, record_id)
                if document is not None:
                    fetched.append(document)

        with self.state.lock, self.state.db:
            if target_record_ids:
                placeholders = ",".join("?" for _ in target_record_ids)
                # Keep superseded rows for historical citations, but exclude
                # them from the current aggregate after every refresh. An
                # empty result therefore also reflects attachment deletion.
                # User-supplemented files are independent material. A public
                # source refresh must not hide them when the collector's
                # attachment list changes or returns an empty result.
                self.state.db.execute(f"UPDATE procurement_documents SET is_current=0 WHERE record_id IN ({placeholders}) "
                                "AND id NOT LIKE 'supplement-%'", tuple(target_record_ids))
            # An observed read failure is different from an observed removal.
            # Retain the last successful bytes alongside the failed attempt;
            # the aggregate stays partial and shows both timestamps/statuses.
            for saved in previous:
                failed = [value for value in fetched if value["record_id"] == saved["record_id"]
                          and value["status"] != "parsed"
                          and (value["url"] == saved["url"] or value["id"].startswith("fetch-"))]
                replaced = any(value["record_id"] == saved["record_id"] and value["status"] == "parsed"
                               and value["url"] == saved["url"] for value in fetched)
                if saved["status"] == "parsed" and failed and not replaced:
                    self.state.db.execute("UPDATE procurement_documents SET is_current=1 WHERE id=?", (saved["id"],))
            for document in fetched:
                existing = self.state.db.execute("SELECT sha256,status FROM procurement_documents WHERE id=?", (document["id"],)).fetchone()
                if existing and existing["status"] == "parsed" and document["status"] != "parsed":
                    attempt_key = ":".join(document[key] for key in ("id", "status", "error", "sha256"))
                    document["id"] = f"attempt-{sha256_text(attempt_key)[:32]}"
                    document["fetched_at"] = document["fetched_at"] or now_iso()
                if existing and existing["sha256"] and document["sha256"] and existing["sha256"] != document["sha256"]:
                    old_id = document["id"]
                    document["id"] = f"{old_id}-{document['sha256'][:16]}"
                    for item in document["items"]:
                        if item.get("source_document_id") == old_id:
                            item["source_document_id"] = document["id"]
                self.state.db.execute(
                    """INSERT INTO procurement_documents
                    (id,record_id,name,url,status,pages,sha256,fetched_at,mime_type,byte_size,text,page_texts_json,items_json,error,is_current,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET record_id=excluded.record_id,name=excluded.name,url=excluded.url,
                    status=excluded.status,pages=excluded.pages,sha256=excluded.sha256,fetched_at=excluded.fetched_at,
                    mime_type=excluded.mime_type,byte_size=excluded.byte_size,text=excluded.text,
                    page_texts_json=excluded.page_texts_json,items_json=excluded.items_json,error=excluded.error,
                    is_current=1,updated_at=excluded.updated_at""",
                    (document["id"], document["record_id"], document["name"], document["url"], document["status"],
                     document["pages"], document["sha256"], document["fetched_at"], document["mime_type"],
                     document["byte_size"], document["text"], json_text(document["page_texts"]),
                     json_text(document["items"]), document["error"], 1, now_iso()),
                )
            self.state.db.execute(
                "INSERT INTO procurement_refreshes(project_id,attempted_at,status,message) VALUES (?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET attempted_at=excluded.attempted_at,status=excluded.status,message=excluded.message",
                (project_id, now_iso(), "succeeded", "附件已刷新" if fetched else "未发现可解析附件"),
            )
            after = self.records.procurement_for_project(project_id)
            semantic_changed = procurement_fingerprint(before) != procurement_fingerprint(after)
            if semantic_changed:
                self.state.db.execute("UPDATE judgments SET stale=1 WHERE project_id=?", (project_id,))
            self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
            for record in records:
                self.state.db.execute(
                    "INSERT INTO procurement_attachment_fetches(record_id,record_version,status,updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(record_id) DO UPDATE SET record_version=excluded.record_version,status=excluded.status,updated_at=excluded.updated_at",
                    (int(record["id"]), int(record.get("current_version") or 1), "attempted", now_iso()),
                )
        if queue_reading and self.settings.ai_config()["configured"] and self.reading_store.reading_gate_enabled():
            # Attachment acquisition is a material change. Queue a fresh
            # source reading so cards/detail/研判 can converge on one result.
            self.queue_reading(project_id, force=semantic_changed, schedule=True)
        return after

    async def ensure_reading_documents(self, project_id: int) -> None:
        """Acquire public attachments once per record version before reading.

        The fetch marker prevents retrying the same network operation on every
        model retry.  The explicit refresh endpoint remains available for a
        user-requested retry and always calls the existing document pipeline.
        """

        records = self.records.records_for_project(int(project_id))
        candidates = [record for record in records
                      if isinstance(record.get("attachments"), list) and record.get("attachments")]
        if not candidates:
            return
        pending: list[dict[str, Any]] = []
        for record in candidates:
            marker = self.state.db.execute(
                "SELECT record_version FROM procurement_attachment_fetches WHERE record_id=?",
                (int(record["id"]),),
            ).fetchone()
            if marker is None or int(marker["record_version"] or 0) != int(record.get("current_version") or 1):
                pending.append(record)
        if pending:
            await self.refresh_record_documents(int(project_id), pending, queue_reading=False)

    async def refresh_project_documents(self, project_id: int) -> dict[str, Any]:
        project_id = int(self.records.project_row(project_id)["id"])
        records = self.records.records_for_project(project_id)
        try:
            procurement = await self.refresh_record_documents(project_id, records)
            message = "采购附件刷新完成"
        except ServiceError as exc:
            with self.state.lock, self.state.db:
                self.state.db.execute(
                    "INSERT INTO procurement_refreshes(project_id,attempted_at,status,message) VALUES (?,?,?,?) "
                    "ON CONFLICT(project_id) DO UPDATE SET attempted_at=excluded.attempted_at,status=excluded.status,message=excluded.message",
                    (project_id, now_iso(), "failed", str(exc)[:240]),
                )
                self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))
            procurement = self.records.procurement_for_project(project_id)
            message = str(exc)
        return {"procurement": procurement, "message": message}

    def document_snapshot(self, project_id: int, document_id: str, *, sha256: str = "") -> dict[str, Any]:
        """Serve the saved version, including superseded files, within its project."""
        self.records.project_row(project_id)
        row = self.state.db.execute(
            "SELECT d.* FROM procurement_documents d JOIN project_records pr ON pr.record_id=d.record_id "
            "WHERE pr.project_id=? AND d.id=?", (project_id, document_id),
        ).fetchone()
        if row is None or (sha256 and row["sha256"] != sha256):
            raise NotFoundError("附件快照不存在或版本不匹配")
        document = document_from_row(row)
        return {**document_public_metadata(document), "text": document["text"],
                "page_texts": document["page_texts"], "items": document["items"]}

    async def agent_fetch_documents(self, project_id: int, arguments: Any, *,
                                     cache: dict[int, list[dict[str, Any]]] | None = None,
                                     records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            request = validate_document_access_arguments(arguments)
        except DocumentAccessError as exc:
            raise AIError("工具参数无效") from exc
        value = request.record_id
        records = records if records is not None else self.records.records_for_project(project_id)
        allowed = {int(record["id"]): record for record in records}
        if value is None:
            targets = records
        elif int(value) in allowed:
            targets = [allowed[int(value)]]
        else:
            raise AIError("工具无权限")
        cache = cache if cache is not None else {}
        unread = [record for record in targets if int(record["id"]) not in cache]
        for record in unread:
            archived = await asyncio.to_thread(read_archived_documents, record, self.state.archive_root)
            # Reuse only a complete set of verified archive files. Otherwise
            # normal acquisition reports missing/failed files explicitly.
            attachments = record.get("attachments", [])
            if (archived and isinstance(attachments, list) and len(archived) == len(attachments)
                    and all(document["status"] == "parsed" for document in archived)):
                await self.refresh_record_documents(project_id, [record], preloaded={int(record["id"]): archived})
                cache[int(record["id"])] = self.records.document_sources_for_records([int(record["id"])])
            else:
                await self.refresh_record_documents(project_id, [record])
                cache[int(record["id"])] = self.records.document_sources_for_records([int(record["id"])])
        documents = [document for record in targets for document in cache[int(record["id"])]]
        try:
            view = build_document_view(documents, request)
        except DocumentAccessError as exc:
            raise AIError("工具无权限" if exc.code == "record_not_in_project" else "工具参数无效") from exc
        # A compact summary avoids re-injecting thousands of material rows on
        # each page read; full procurement detail remains in the API and DB.
        procurement = self.records.procurement_for_project(project_id)
        compact = {key: procurement.get(key) for key in (
            "status", "scope_summary", "delivery_period", "delivery_location", "attachment_status", "attachment_note",
        )}
        return {**view, "procurement": compact}
