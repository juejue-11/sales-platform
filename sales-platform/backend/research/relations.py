"""Relation assessment over served, immutable research evidence."""
from __future__ import annotations

from typing import Any, Callable

from .evidence import EvidenceLedger, RelationPolicy
from .parsing import source_shape
from .storage import ResearchStorage


Event = Callable[..., None]


class RelationAssessor:
    """Persist a relation only when its exact served quote supports it."""

    def __init__(self, *, storage: ResearchStorage, ledger: EvidenceLedger,
                 policy: RelationPolicy, project_id: int, job_id: int, event: Event):
        self.storage = storage
        self.ledger = ledger
        self.policy = policy
        self.project_id = int(project_id)
        self.job_id = int(job_id)
        self.event = event

    async def assess(self, args: dict[str, Any]) -> dict[str, Any]:
        found = self.storage.project_source_row(args["source_ref"])
        if found is None:
            self.event("assess_source_relation", args["reason"], "blocked", [args["source_ref"]],
                       message="source_not_in_project")
            return {"status": "blocked", "error": "source_not_in_project", "source_ref": args["source_ref"]}
        source, latest_version_row, relation = found
        version = self.storage.source_version_row(args["source_ref"], args["source_version"])
        if version is None:
            self.event("assess_source_relation", args["reason"], "failed", [args["source_ref"]],
                       message="source_version_not_found")
            return {"status": "failed", "error": "source_version_not_found", "source_ref": args["source_ref"]}
        if not self.ledger.served_quote(args["source_ref"], args["source_version"], args["quote"]):
            self.event("assess_source_relation", args["reason"], "rejected", [args["source_ref"]],
                       message="quote_not_in_served_fragment")
            return {"status": "rejected", "error": "quote_not_in_served_fragment",
                    "source_ref": args["source_ref"], "source_version": args["source_version"],
                    "relation": "unconfirmed", "relation_status": "rejected"}
        relation_status, status, message = self.policy.verifies(args["relation"], args["quote"])
        scope = relation.get("scope") or version.get("scope") or "page"
        self.storage.record_relation(
            source_ref=args["source_ref"], source_version=args["source_version"],
            relation=args["relation"], relation_status=relation_status,
            quote=args["quote"], reason=args["reason"], scope=scope,
            job_id=self.job_id, project_id=self.project_id,
        )
        self.event("assess_source_relation", args["reason"], status, [args["source_ref"]], message=message)
        result = source_shape(source, version, {"relation": args["relation"],
                                                "relation_status": relation_status, "scope": scope})
        result.update({"status": status, "quote": args["quote"], "reason": args["reason"],
                       "relation": args["relation"], "relation_status": relation_status})
        return result


__all__ = ["RelationAssessor"]
