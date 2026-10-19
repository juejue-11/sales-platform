"""Composition root for one authorized, bounded research run."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import httpx

from .config import (
    DEFAULT_BUDGETS,
    RESEARCH_POLICY_VERSION,
    ResearchError,
    load_config,
    now,
    sources_path as resolve_sources_path,
    validate_research_arguments,
)
from .evidence import EvidenceLedger, RelationPolicy
from .network import NetworkTransport
from .parsing import source_shape
from .reading import SourceReader
from .relations import RelationAssessor
from .search import SourceSearcher
from .storage import ResearchStorage


class ResearchSession:
    """One authorized, bounded research run for one project and job."""

    def __init__(self, db: Any, *, project_id: int, job_id: int, records: list[dict[str, Any]],
                 db_lock: Any = None, sources_path: str | Path | None = None,
                 client: httpx.AsyncClient | None = None):
        self.db = db
        self.project_id = int(project_id)
        self.job_id = int(job_id)
        self.records = [records] if isinstance(records, dict) else list(records or [])
        self.db_lock = db_lock
        self.sources_path = resolve_sources_path(sources_path)
        self.config = load_config(self.sources_path)
        self.storage = ResearchStorage(db, project_id=self.project_id, job_id=self.job_id, db_lock=db_lock)
        self.budgets = {**DEFAULT_BUDGETS, **(self.config.get("budgets") or {})}
        self.transport = NetworkTransport(client, self.budgets)
        self.ledger = EvidenceLedger(self.storage, job_id=self.job_id)
        self.policy = RelationPolicy(self.records)
        self._events: list[dict[str, Any]] = []
        self._unresolved: list[str] = []
        self._chars = 0
        self._tool_calls = 0
        self._status = "not_started"
        self._stop_reason: str | None = None
        self._authorization_error = self.storage.authorization_error()
        self.searcher = SourceSearcher(
            storage=self.storage, transport=self.transport, config=self.config, budgets=self.budgets,
            project_id=self.project_id, job_id=self.job_id, records=self.records,
            event=self._event, mark_limited=self._mark_limited,
        )
        self.reader = SourceReader(
            storage=self.storage, transport=self.transport, ledger=self.ledger,
            config=self.config, project_id=self.project_id, job_id=self.job_id,
            event=self._event, budget_check=self._budget_check, consume_chars=self._consume_chars,
            mark_limited=self._mark_limited,
        )
        self.assessor = RelationAssessor(
            storage=self.storage, ledger=self.ledger, policy=self.policy,
            project_id=self.project_id, job_id=self.job_id, event=self._event,
        )

    async def close(self) -> None:
        await self.transport.close()

    def __enter__(self) -> "ResearchSession":
        return self

    def __exit__(self, *_: Any) -> None:
        # The async owner should call close(); there is no synchronous event-loop
        # safe close to perform here.
        return None

    def _mark_limited(self, reason: str) -> None:
        self._status = "limited"
        self._stop_reason = reason

    def _consume_chars(self, count: int) -> None:
        self._chars += count

    def _budget_check(self, *, calls: int = 1, bytes_count: int = 0, chars: int = 0) -> None:
        if self.transport.calls + calls > int(self.budgets.get("max_calls", DEFAULT_BUDGETS["max_calls"])):
            raise ResearchError("budget_exhausted")
        if self.transport.download_bytes + bytes_count > int(self.budgets.get("max_download_bytes", DEFAULT_BUDGETS["max_download_bytes"])):
            raise ResearchError("budget_exhausted")
        if self._chars + chars > int(self.budgets.get("max_chars", DEFAULT_BUDGETS["max_chars"])):
            raise ResearchError("budget_exhausted")

    def _event(self, tool: str, purpose: str, status: str, sources: list[str] | None = None,
               *, query: str | None = None, message: str | None = None) -> None:
        event: dict[str, Any] = {"at": now(), "tool": tool, "purpose": purpose, "status": status,
                                 "sources": list(sources or [])}
        if query is not None:
            event["query"] = query
        if message:
            event["message"] = message
        self._events.append(event)

    def _investigation_sources(self) -> list[dict[str, Any]]:
        keys: list[tuple[str, str | None]] = []
        seen: set[tuple[str, str | None]] = set()
        for source_ref, source_version in sorted(self.ledger.used_refs):
            key = (source_ref, source_version)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        discovered = self.db.execute(
            """SELECT source_ref FROM research_project_sources
                WHERE project_id=? AND discovered_job_id=?
                ORDER BY updated_at DESC, source_ref LIMIT 50""",
            (self.project_id, self.job_id),
        ).fetchall()
        for row in discovered:
            if any(source_ref == str(row["source_ref"]) for source_ref, _ in keys):
                continue
            key = (str(row["source_ref"]), None)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        values: list[dict[str, Any]] = []
        for source_ref, used_version in keys[:50]:
            found = self.storage.project_source_row(source_ref)
            if found is None:
                continue
            source, latest, current_relation = found
            if used_version is not None:
                version = self.storage.source_version_row(source_ref, used_version)
                relation = self.storage.relation(source_ref, used_version)
            else:
                version = latest
                relation = current_relation
            values.append(source_shape(source, version, relation))
        return values

    def investigation(self, status: str | None = None, stop_reason: str | None = None,
                       unresolved: list[str] | None = None) -> dict[str, Any]:
        if status is not None:
            if status not in {"not_started", "running", "completed", "limited", "failed", "not_performed"}:
                raise ValueError("invalid_investigation_status")
            self._status = status
        if stop_reason is not None:
            self._stop_reason = str(stop_reason)
        if unresolved is not None:
            self._unresolved = [str(item) for item in unresolved]
        return {
            "policy_version": RESEARCH_POLICY_VERSION,
            "status": self._status,
            "stop_reason": self._stop_reason,
            "events": [dict(event) for event in self._events],
            "sources": self._investigation_sources() if self._authorization_error is None else [],
            "unresolved": list(self._unresolved),
        }

    def evidence_sources(self) -> list[dict[str, Any]]:
        return self.ledger.evidence_sources()

    def dependencies(self, source_refs: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return self.ledger.dependencies(source_refs)

    async def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one validated research tool and return a safe result object."""
        if self._authorization_error is not None:
            self._status = "failed"
            self._stop_reason = self._authorization_error
            return {"status": "blocked", "error": self._authorization_error,
                    "policy_version": RESEARCH_POLICY_VERSION}
        try:
            normalized = validate_research_arguments(tool, args)
        except (TypeError, ValueError) as exc:
            self._status = "failed"
            self._event(tool if isinstance(tool, str) else "unknown", "", "invalid_arguments", message=str(exc))
            return {"status": "invalid_arguments", "error": str(exc),
                    "policy_version": RESEARCH_POLICY_VERSION}
        self._tool_calls += 1
        if self._tool_calls > int(self.budgets.get("max_calls", DEFAULT_BUDGETS["max_calls"])):
            self._mark_limited("budget_exhausted")
            self._event(tool, normalized.get("purpose", ""), "budget_exhausted",
                       [normalized.get("source_ref")] if normalized.get("source_ref") else None)
            return {"status": "budget_exhausted", "error": "budget_exhausted",
                    "policy_version": RESEARCH_POLICY_VERSION}
        self._status = "running"
        # Callers may refresh the project records between tool calls; relation
        # checks must use that current snapshot.
        self.policy.records = self.records
        try:
            if tool == "search_project_sources":
                return await self.searcher.search(normalized)
            if tool == "read_research_source":
                return await self.reader.read(normalized)
            if tool == "assess_source_relation":
                return await self.assessor.assess(normalized)
            return await self.reader.content(normalized)
        except ResearchError as exc:
            if exc.code == "budget_exhausted":
                self._mark_limited(exc.code)
            else:
                self._status = "failed"
            self._event(tool, normalized.get("purpose", ""),
                       "budget_exhausted" if exc.code == "budget_exhausted" else "failed",
                       [normalized.get("source_ref")] if normalized.get("source_ref") else None,
                       query=normalized.get("query"), message=exc.code)
            return {"status": "budget_exhausted" if exc.code == "budget_exhausted" else "failed",
                    "error": exc.code, "policy_version": RESEARCH_POLICY_VERSION}
        except Exception:
            self._status = "failed"
            self._event(tool, normalized.get("purpose", ""), "failed",
                       [normalized.get("source_ref")] if normalized.get("source_ref") else None,
                       query=normalized.get("query"), message="research_internal_error")
            return {"status": "failed", "error": "research_internal_error",
                    "policy_version": RESEARCH_POLICY_VERSION}


__all__ = ["ResearchSession"]
