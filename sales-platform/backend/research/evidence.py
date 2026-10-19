"""Evidence tracking and relation identity policy for research sessions."""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..core.citations import citation_fragments
from .config import VERIFIED_RELATIONS
from .storage import ResearchStorage


class EvidenceLedger:
    """Track exactly which immutable fragments a session served."""

    def __init__(self, storage: ResearchStorage, *, job_id: int):
        self.storage = storage
        self.job_id = int(job_id)
        self.served: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.used_refs: set[tuple[str, str]] = set()

    def register(self, source_ref: str, version: str, fragments: Iterable[dict[str, Any]], *, scope: str) -> None:
        key = (str(source_ref), str(version))
        self.used_refs.add(key)
        served = self.served.setdefault(key, [])
        for fragment in fragments:
            served.append({"page": fragment.get("page"), "text": fragment.get("text") or "", "scope": scope})

    def served_quote(self, source_ref: str, version: str, quote: str) -> bool:
        if not quote:
            return False
        return any(quote in str(fragment.get("text") or "")
                   for fragment in self.served.get((source_ref, version), []))

    def evidence_sources(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for source_ref, source_version in sorted(self.used_refs):
            fragments = [fragment for fragment in self.served.get((source_ref, source_version), [])
                         if str(fragment.get("text") or "")]
            if not fragments:
                continue
            found = self.storage.project_source_row(source_ref)
            version = self.storage.source_version_row(source_ref, source_version)
            if found is None or version is None:
                continue
            source, _latest, _current_relation = found
            relation = self.storage.relation(source_ref, source_version)
            unique: list[dict[str, Any]] = []
            seen: set[tuple[Any, str]] = set()
            for fragment in fragments:
                key = (fragment.get("page"), str(fragment.get("text") or ""))
                if key not in seen:
                    seen.add(key)
                    unique.append({"page": fragment.get("page"), "text": fragment.get("text") or ""})
            values.append({
                "source_ref": source_ref,
                "source_version": source_version,
                "title": version.get("title") or source.get("title") or "",
                "url": version.get("url") or source.get("canonical_url") or "",
                "fetched_at": version.get("fetched_at"),
                "published_at": version.get("published_at") or source.get("published_at"),
                "relation": relation.get("relation") or "unconfirmed",
                "relation_status": relation.get("relation_status") or "unconfirmed",
                "page_texts": unique,
                "text": "\n".join(item["text"] for item in unique),
                "scope": relation.get("scope") or version.get("scope") or "page",
                "parent_source_ref": version.get("parent_source_ref") or source.get("parent_source_ref"),
            })
        return values

    def dependencies(self, source_refs: Iterable[str] | None = None) -> list[dict[str, Any]]:
        requested = ({source_refs} if isinstance(source_refs, str)
                     else {str(item) for item in source_refs} if source_refs is not None else None)
        values: list[dict[str, Any]] = []
        for source_ref, source_version in sorted(self.used_refs):
            if requested is not None and source_ref not in requested:
                continue
            found = self.storage.project_source_row(source_ref)
            if found is None:
                continue
            source, _latest, _current_relation = found
            relation = self.storage.relation(source_ref, source_version)
            if relation.get("relation_status") != "verified":
                continue
            if self.storage.source_version_row(source_ref, source_version) is None:
                continue
            values.append({
                "source_ref": source_ref,
                "source_version": source_version,
                "relation": relation.get("relation"),
                "verified": True,
                "relation_status": "verified",
                "scope": relation.get("scope") or "page",
                "job_id": self.job_id,
            })
        return values

    @staticmethod
    def evidence_fragments(fragments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**citation, "page": fragment.get("page")}
            for fragment in fragments
            for citation in citation_fragments(str(fragment.get("text") or ""))
        ]


class RelationPolicy:
    """Apply project identity checks before a source can become evidence."""

    def __init__(self, records: Iterable[dict[str, Any]] | None):
        self.records = records if isinstance(records, list) else list(records or [])

    def _identity_values(self, keys: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for record in self.records:
            for key in keys:
                value = str(record.get(key) or "").strip()
                if value:
                    found.add(value.casefold())
        return found

    @staticmethod
    def _mismatch(found_values: list[str], known_values: set[str]) -> bool:
        if not found_values or not known_values:
            return False
        return any(value.casefold().strip() not in known_values for value in found_values)

    def identity_conflict(self, text: str) -> bool:
        # Keep the canonical labels escaped so matching does not depend on the
        # terminal encoding used to edit this module.
        project_found = re.findall(
            r"(?:\u9879\u76ee|\u91c7\u8d2d|\u63a1\u8cfc|\u62db\u6807|\u516c\u544a|notice)"
            r"[^\n:\uff1a]{0,12}(?:\u7f16\u53f7|\u7de8\u865f|number|No\.?|\u53f7)"
            r"\s*[:\uff1a#]?\s*([A-Za-z0-9][A-Za-z0-9_./-]{2,})",
            text, flags=re.I,
        )
        if self._mismatch(project_found, self._identity_values(
                ("notice_number", "project_code", "tender_number", "procurement_number"))):
            return True
        lot_found = re.findall(
            r"(?:\u6807\u6bb5|\u6a19\u6bb5|\u5305\u53f7|\u5305\u865f|\u5305\u4ef6|lot|section)"
            r"(?:\s*(?:number|no\.?|\u53f7|\u7de8\u865f|\u7f16\u53f7))?"
            r"\s*[:\uff1a#-]?\s*([A-Za-z0-9][A-Za-z0-9_./-]{0,64})",
            text, flags=re.I,
        )
        if self._mismatch(lot_found, self._identity_values(
                ("lot_number", "package_number", "tender_section", "section_number", "lot"))):
            return True
        buyer_found = re.findall(
            r"(?:\u91c7\u8d2d\u4eba|\u63a1\u8cfc\u4eba|\u62db\u6807\u4eba|\u91c7\u8d2d\u65b9|\u63a1\u8cfc\u65b9|"
            r"buyer|purchaser|procuring\s+entity)\s*[:\uff1a#]?\s*([^\r\n,\uff1a;；]{2,80})",
            text, flags=re.I,
        )
        buyer_values = {value.casefold().strip(" \t:\uff1a#-") for value in buyer_found}
        known_buyers = self._identity_values(("buyer", "buyer_name", "purchaser", "procuring_entity", "tenderer"))
        if buyer_values and known_buyers and not any(
            actual == expected or actual in expected or expected in actual
            for actual in buyer_values for expected in known_buyers
        ):
            return True
        return False

    def same_procurement_identity(self, text: str) -> bool:
        folded = text.casefold()
        for record in self.records:
            strong = [record.get(key) for key in
                       ("notice_number", "project_code", "tender_number", "procurement_number")]
            strong = [str(value).strip() for value in strong if str(value or "").strip()]
            if strong:
                if any(value.casefold() in folded for value in strong):
                    return True
                continue
            title = str(record.get("project_name") or record.get("title") or "").strip()
            buyer = next((str(record.get(key) or "").strip()
                          for key in ("buyer", "buyer_name", "purchaser", "procuring_entity")
                          if str(record.get(key) or "").strip()), "")
            if title and buyer and title.casefold() in folded and buyer.casefold() in folded:
                return True
        return False

    def related_project_identity(self, text: str) -> bool:
        folded = text.casefold()
        for record in self.records:
            strong = [str(record.get(key) or "").strip() for key in
                       ("notice_number", "project_code", "tender_number", "procurement_number")]
            if any(value and value.casefold() in folded for value in strong):
                return True
            title = str(record.get("project_name") or record.get("title") or "").strip()
            buyer = next((str(record.get(key) or "").strip()
                          for key in ("buyer", "buyer_name", "purchaser", "procuring_entity")
                          if str(record.get(key) or "").strip()), "")
            if title and buyer and title.casefold() in folded and buyer.casefold() in folded:
                return True
        return False

    def known_buyer(self, text: str) -> bool:
        folded = text.casefold()
        return any(
            str(record.get(key) or "").strip() and str(record.get(key) or "").strip().casefold() in folded
            for record in self.records
            for key in ("buyer", "buyer_name", "purchaser", "procuring_entity", "tenderer")
        )

    def verifies(self, relation: str, quote: str) -> tuple[str, str, str | None]:
        hard_conflict = self.identity_conflict(quote)
        identity_missing = (
            (relation == "same_procurement" and not self.same_procurement_identity(quote))
            or (relation == "related_project" and not self.related_project_identity(quote))
            or (relation == "buyer_background" and not self.known_buyer(quote))
        )
        if relation in VERIFIED_RELATIONS and not hard_conflict and not identity_missing:
            return "verified", "success", None
        if relation in {"unrelated", "conflict"} or hard_conflict:
            return "rejected", "rejected", "identity_conflict" if hard_conflict else None
        return "unconfirmed", "unconfirmed", "identity_not_established" if identity_missing else None


__all__ = ["EvidenceLedger", "RelationPolicy"]
