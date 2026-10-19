"""Reading, parsing, and bounded content access for discovered sources."""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from .config import ResearchError, json_value
from .evidence import EvidenceLedger
from .network import NetworkTransport, host_allowed, safe_url
from .parsing import kind, page_evidence_text, parse_fetched, source_ref, source_shape
from .storage import ResearchStorage


Event = Callable[..., None]
BudgetCheck = Callable[..., None]


class SourceReader:
    """Fetch immutable source versions and expose bounded evidence fragments."""

    def __init__(self, *, storage: ResearchStorage, transport: NetworkTransport,
                 ledger: EvidenceLedger, config: dict[str, Any],
                 project_id: int, job_id: int, event: Event,
                 budget_check: BudgetCheck, consume_chars: Callable[[int], None],
                 mark_limited: Callable[[str], None]):
        self.storage = storage
        self.transport = transport
        self.ledger = ledger
        self.config = config
        self.project_id = int(project_id)
        self.job_id = int(job_id)
        self.event = event
        self.budget_check = budget_check
        self.consume_chars = consume_chars
        self.mark_limited = mark_limited

    def source_config(self, source_id: str) -> dict[str, Any] | None:
        return next((item for item in self.config.get("sources", [])
                     if str(item.get("id") or item.get("source_id") or "") == source_id), None)

    def source_version_row(self, source_ref_value: str, version: str | None = None) -> dict[str, Any] | None:
        return self.storage.source_version_row(source_ref_value, version)

    def serve_version(self, source: dict[str, Any], version: dict[str, Any], args: dict[str, Any], *,
                      register: bool = True) -> dict[str, Any]:
        pages = json_value(version.get("page_texts_json"), [])
        if not pages and version.get("body_text"):
            pages = [{"page": None, "text": str(version.get("body_text") or "")}]
        page = args.get("page") or 0
        if page:
            selected = [item for item in pages if item.get("page") is not None and int(item.get("page")) == int(page)]
        else:
            selected = pages[:1]
        fragments: list[dict[str, Any]] = []
        for item in selected:
            text = str(item.get("text") or "")
            if version.get("kind") == "page":
                text = page_evidence_text(text)
            offset = args.get("offset", 0)
            limit = args.get("limit", 4000)
            fragments.append({"page": item.get("page"), "text": text[offset:offset + limit]})
        text = "\n".join(fragment["text"] for fragment in fragments)
        if register:
            self.budget_check(calls=0, chars=len(text))
            self.consume_chars(len(text))
        relation = self.storage.relation(source["source_ref"], str(version.get("version") or ""))
        result = source_shape(source, version, relation)
        result.update({"text": text, "page_texts": fragments,
                       "evidence_fragments": self.ledger.evidence_fragments(fragments),
                       "scope": relation.get("scope") or version.get("scope") or "page"})
        if not text and result.get("status") == "read":
            result["status"] = "no_content"
            result["error"] = "empty_fragment"
        if register and version.get("version"):
            self.ledger.register(str(source["source_ref"]), str(version["version"]), fragments,
                                 scope=result.get("scope") or "page")
        return result

    def save_version(self, source: dict[str, Any], *, raw: bytes, url: str,
                     content_type: str, title: str, published_at: str | None,
                     parent_source_ref: str | None = None) -> dict[str, Any]:
        status, parsed_kind, pages, text, error, links = parse_fetched(
            raw, content_type, url, title=title, record_id=self.project_id,
            parent_source_ref=parent_source_ref,
        )
        version_row = self.storage.persist_version(
            source_ref=source["source_ref"], raw=raw, title=title or source.get("title") or "",
            url=url, published_at=published_at or source.get("published_at"), kind=parsed_kind,
            status=status, body_text=text, page_texts=pages,
            scope="attachment" if parent_source_ref else "page", parent_source_ref=parent_source_ref,
            error=error, job_id=self.job_id, project_id=self.project_id,
        )
        if links:
            cfg = self.source_config(str(source.get("config_id") or source.get("source_id") or "")) or {}
            allowed_hosts = self.transport.allowed_hosts(cfg, url)
            for link in links[:100]:
                try:
                    child_url = safe_url(link.get("url") or "", base=url)
                    if child_url in {url, source.get("canonical_url")}:
                        continue
                    name = str(link.get("name") or "").strip()
                    if name in {"��ҳ", "��¼", "�ҵ�", "�б�ɹ�", "�б깫��", "�ɹ�����", "�н�公示", "�н����"}:
                        continue
                    is_document = kind(child_url) == "document" or bool(re.search(
                        r"download|attachment|downfile|attach|filedownload", urlsplit(child_url).path, re.I,
                    ))
                    is_related_notice = len(name) > 6 and bool(re.search(
                        r"公告|公示|澄清|变更|补充通知|技术(?:要求|规格|参数|文件|规范)|"
                        r"交付(?:要求|时间|地点)|(?:物料|采购|设备)清单", name,
                    ))
                    if not is_document and not is_related_notice:
                        continue
                    child_host = urlsplit(child_url).hostname or ""
                    if not host_allowed(child_host, allowed_hosts):
                        continue
                    try:
                        if not ipaddress.ip_address(child_host).is_global:
                            continue
                    except ValueError:
                        pass
                except ResearchError:
                    continue
                child_ref = source_ref(str(source.get("config_id") or source.get("source_id") or ""), child_url)
                child_kind = kind(child_url)
                self.storage.upsert_source(
                    source_ref=child_ref,
                    source_id=str(source.get("source_id") or source.get("config_id") or ""),
                    url=child_url, title=link.get("name") or "", published_at=None,
                    kind=child_kind, status="candidate", parent_source_ref=source["source_ref"],
                    scope="attachment" if child_kind == "document" else "page", job_id=self.job_id,
                    project_id=self.project_id,
                )
        return version_row

    def save_failed_version(self, source: dict[str, Any], *, raw: bytes, url: str,
                            status: str, error: str, content_type: str = "") -> dict[str, Any]:
        return self.storage.persist_failed_version(
            source_ref=source["source_ref"], raw=raw, url=url, title=source.get("title") or "",
            published_at=source.get("published_at"), kind=kind(url, content_type), status=status,
            error=error, parent_source_ref=source.get("parent_source_ref"), job_id=self.job_id,
            project_id=self.project_id,
        )

    async def read(self, args: dict[str, Any]) -> dict[str, Any]:
        found = self.storage.project_source_row(args["source_ref"])
        if found is None:
            self.event("read_research_source", args["purpose"], "blocked", [args["source_ref"]],
                       message="source_not_in_project")
            return {"status": "blocked", "error": "source_not_in_project", "source_ref": args["source_ref"]}
        source, latest, _relation = found
        if latest is not None and not args["refresh"]:
            value = self.serve_version(source, latest, args)
            status = value.get("status") or "failed"
            event_status = "success" if status == "read" and value.get("text") else status
            self.event("read_research_source", args["purpose"], event_status,
                       [args["source_ref"]], message="cache_hit")
            return value
        config_id = str(source.get("config_id") or source.get("source_id") or "")
        cfg = self.source_config(config_id) or {
            "allowed_hosts": [urlsplit(source["canonical_url"]).hostname or ""]
        }
        try:
            raw, content_type, final_url, http_status = await self.transport.request(
                "GET", source["canonical_url"], cfg,
            )
        except ResearchError as exc:
            status = "budget_exhausted" if exc.code == "budget_exhausted" else (
                "blocked" if exc.code in {"non_public_source", "attachment_outside_source", "invalid_source_url",
                                           "invalid_source_port", "redirect_limit"} else "failed"
            )
            self.event("read_research_source", args["purpose"], status,
                       [args["source_ref"]], message=exc.code)
            if exc.code == "budget_exhausted":
                self.mark_limited(exc.code)
            return {"status": status, "error": exc.code, "source_ref": args["source_ref"]}
        if http_status in {401, 403}:
            version = self.save_failed_version(source, raw=raw, url=final_url,
                                               status="auth_required", error="auth_required",
                                               content_type=content_type)
            value = self.serve_version(source, version, args)
            self.event("read_research_source", args["purpose"], "auth_required", [args["source_ref"]])
            return value
        if http_status != 200:
            version = self.save_failed_version(source, raw=raw, url=final_url,
                                               status="failed", error=f"http_{http_status}",
                                               content_type=content_type)
            value = self.serve_version(source, version, args)
            self.event("read_research_source", args["purpose"], "failed", [args["source_ref"]],
                       message=f"http_{http_status}")
            return value
        version = self.save_version(source, raw=raw, url=final_url, content_type=content_type,
                                    title=source.get("title") or "", published_at=source.get("published_at"),
                                    parent_source_ref=source.get("parent_source_ref"))
        value = self.serve_version(source, version, args)
        event_status = "success" if value.get("status") == "read" and value.get("text") else value.get("status", "failed")
        self.event("read_research_source", args["purpose"], event_status, [args["source_ref"]],
                   message="refreshed" if args["refresh"] else None)
        return value

    async def content(self, args: dict[str, Any]) -> dict[str, Any]:
        found = self.storage.project_source_row(args["source_ref"])
        if found is None:
            self.event("search_research_content", args["purpose"], "blocked", [args["source_ref"]],
                       query=args["query"], message="source_not_in_project")
            return {"status": "blocked", "error": "source_not_in_project", "source_ref": args["source_ref"]}
        source, _latest, relation = found
        version = self.source_version_row(args["source_ref"], args["source_version"])
        if version is None:
            self.event("search_research_content", args["purpose"], "failed", [args["source_ref"]],
                       query=args["query"], message="source_version_not_found")
            return {"status": "failed", "error": "source_version_not_found", "source_ref": args["source_ref"]}
        full_text = str(version.get("body_text") or "")
        if version.get("kind") == "page":
            full_text = page_evidence_text(full_text)
        query = args["query"].casefold()
        matches: list[dict[str, Any]] = []
        pages = json_value(version.get("page_texts_json"), [])
        for item in pages:
            page_text = str(item.get("text") or "")
            if version.get("kind") == "page":
                page_text = page_evidence_text(page_text)
            haystack = page_text.casefold()
            start = 0
            while len(matches) < args["limit"]:
                index = haystack.find(query, start)
                if index < 0:
                    break
                begin = max(0, index - 280)
                end = min(len(page_text), index + len(query) + 280)
                matches.append({"page": item.get("page"), "text": page_text[begin:end],
                                "quote": page_text[index:index + len(args["query"])], "offset": begin})
                start = index + max(1, len(query))
            if len(matches) >= args["limit"]:
                break
        if not matches and full_text and query in full_text.casefold():
            index = full_text.casefold().find(query)
            matches.append({"page": None, "text": full_text[max(0, index - 280):index + len(args["query"]) + 280],
                            "quote": full_text[index:index + len(args["query"])],
                            "offset": max(0, index - 280)})
        status = "no_results" if not matches else "success"
        if matches:
            fragments = [{"page": match.get("page"), "text": match.get("text") or ""} for match in matches]
            chars = sum(len(fragment["text"]) for fragment in fragments)
            self.budget_check(calls=0, chars=chars)
            self.consume_chars(chars)
            scope = relation.get("scope") or version.get("scope") or "fragment"
            self.ledger.register(args["source_ref"], args["source_version"], fragments, scope=scope)
        self.event("search_research_content", args["purpose"], status, [args["source_ref"]],
                   query=args["query"])
        result = source_shape(source, version, relation)
        result.update({"status": status,
                       "matches": matches,
                       "text": "\n".join(item["text"] for item in matches),
                       "evidence_fragments": self.ledger.evidence_fragments(
                           [{"page": match.get("page"), "text": match.get("text") or ""} for match in matches]
                       ),
                       "page_texts": [{"page": item.get("page"), "text": item.get("text") or ""} for item in matches],
                       "scope": relation.get("scope") or version.get("scope") or "fragment"})
        return result


__all__ = ["SourceReader"]
