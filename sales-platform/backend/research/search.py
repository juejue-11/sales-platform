"""Configured source discovery for a research session."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from .config import ResearchError, DEFAULT_BUDGETS, publication_in_range
from .network import NetworkTransport, host_allowed, safe_url, validate_network_url
from .parsing import html_content, kind, path_get, source_ref, substitute
from .storage import ResearchStorage


Event = Callable[..., None]


class SourceSearcher:
    """Turn configured list/search adapters into project source candidates."""

    def __init__(self, *, storage: ResearchStorage, transport: NetworkTransport,
                 config: dict[str, Any], budgets: dict[str, Any],
                 project_id: int, job_id: int, records: list[dict[str, Any]],
                 event: Event, mark_limited: Callable[[str], None]):
        self.storage = storage
        self.transport = transport
        self.config = config
        self.budgets = budgets
        self.project_id = int(project_id)
        self.job_id = int(job_id)
        self.records = records
        self.event = event
        self.mark_limited = mark_limited

    def candidate_from_item(self, cfg: dict[str, Any], item: Any, endpoint: str) -> dict[str, Any] | None:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            return None
        raw_url = (item.get("url") or item.get("link") or item.get("href") or item.get("canonical_url")
                   or item.get("linkurl") or item.get("linkUrl"))
        if not isinstance(raw_url, str) or not raw_url.strip():
            return None
        try:
            url = safe_url(raw_url, base=endpoint)
        except ResearchError:
            return None
        config_id = str(cfg.get("id") or cfg.get("source_id") or "")
        title = str(item.get("title") or item.get("title2") or item.get("name") or item.get("subject") or "").strip()
        published_at = (item.get("published_at") or item.get("publishedAt") or item.get("date")
                        or item.get("webdate") or item.get("infodate"))
        if published_at is not None:
            published_at = str(published_at).strip() or None
        ref = source_ref(config_id, url, item.get("source_ref") or item.get("id"))
        existing = self.storage.db.execute(
            "SELECT source_id,canonical_url FROM research_sources WHERE source_ref=?", (ref,)
        ).fetchone()
        if existing is not None and (existing["source_id"] != config_id or existing["canonical_url"] != url):
            ref = source_ref(config_id, url)
        return {
            "source_ref": ref,
            "source_id": config_id,
            "url": url,
            "title": title,
            "published_at": published_at,
            "kind": kind(url, str(item.get("mime_type") or item.get("content_type") or "")),
            "summary": str(item.get("summary") or item.get("snippet") or "").strip(),
        }

    async def query_config(self, cfg: dict[str, Any], args: dict[str, Any]) -> tuple[
            str, list[dict[str, Any]], str | None]:
        search = cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg
        if isinstance(cfg.get("request"), dict):
            search = {**cfg, **cfg["request"], **(cfg.get("search") or {})}
        scan = None
        for key in ("list_scan", "list"):
            if isinstance(cfg.get(key), dict):
                scan = cfg[key]
                break
        if scan is not None and not any(search.get(key) for key in ("url", "endpoint", "search_url", "list_url")):
            search = {**search, **scan, "adapter": scan.get("adapter") or "list_scan"}
        adapter = str(search.get("adapter") or search.get("type") or search.get("method") or "get_list").lower()
        endpoint_value = search.get("url") or search.get("endpoint") or search.get("search_url") or search.get("list_url")
        if endpoint_value is None:
            endpoint_value = cfg.get("search_url") or cfg.get("list_url")
        if not isinstance(endpoint_value, str) or not endpoint_value.strip():
            return "no_configuration", [], "source_search_not_configured"
        try:
            endpoint = safe_url(endpoint_value, base=cfg.get("base_url") if isinstance(cfg.get("base_url"), str) else None)
        except ResearchError as exc:
            return "blocked", [], exc.code
        variables = {
            "query": args["query"], "purpose": args["purpose"], "limit": args["limit"], "offset": args["offset"],
            "page_number": args["offset"] // args["limit"] + 1,
            "published_after": args.get("published_after") or "",
            "published_before": args.get("published_before") or "",
        }
        method = "POST" if "post" in adapter or adapter in {"post_json_list", "json"} else "GET"
        body = search.get("body") or search.get("json") or search.get("payload")
        body = substitute(body, variables) if body is not None else None
        query_field = str(search.get("query_field") or "query")
        if method == "POST":
            if not isinstance(body, dict):
                body = {}
            body.setdefault(query_field, args["query"])
            body.setdefault(str(search.get("limit_field") or "limit"), args["limit"])
            body.setdefault(str(search.get("offset_field") or "offset"), args["offset"])
            if args.get("published_after"):
                body.setdefault(str(search.get("published_after_field") or "published_after"), args["published_after"])
            if args.get("published_before"):
                body.setdefault(str(search.get("published_before_field") or "published_before"), args["published_before"])
            params = None
        else:
            params = {}
            if isinstance(search.get("params"), dict):
                params.update(substitute(search["params"], variables))
            params.setdefault(query_field, args["query"])
            params.setdefault(str(search.get("limit_field") or "limit"), args["limit"])
            params.setdefault(str(search.get("offset_field") or "offset"), args["offset"])
            if args.get("published_after"):
                params.setdefault(str(search.get("published_after_field") or "published_after"), args["published_after"])
            if args.get("published_before"):
                params.setdefault(str(search.get("published_before_field") or "published_before"), args["published_before"])
            body = None
        try:
            raw, content_type, final_url, http_status = await self.transport.request(
                method, endpoint, cfg, json_body=body, params=params,
            )
        except ResearchError as exc:
            if exc.code == "budget_exhausted":
                self.mark_limited(exc.code)
                return "limited", [], exc.code
            return ("blocked" if exc.code in {"non_public_source", "attachment_outside_source", "invalid_source_url",
                                                "invalid_source_port", "redirect_limit"} else "failed"), [], exc.code
        if http_status in {401, 403}:
            return "blocked", [], "auth_required"
        if http_status != 200:
            return "failed", [], f"http_{http_status}"
        looks_html = "html" in content_type or raw.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
        if looks_html:
            _page_text, _pages, links = html_content(raw, content_type)
            candidates: list[dict[str, Any]] = []
            allowed_hosts = self.transport.allowed_hosts(cfg, endpoint)
            query_lower = args["query"].casefold()
            blocked_candidate = False
            for link in links[:200]:
                candidate = self.candidate_from_item(
                    cfg, {"url": link.get("url"), "title": link.get("name")}, final_url,
                )
                if candidate is None:
                    continue
                haystack = (candidate.get("title") or "").casefold() + " " + candidate["url"].casefold()
                if query_lower not in haystack:
                    continue
                try:
                    await validate_network_url(candidate["url"], allowed_hosts,
                                               await self.transport.client_for_validation())
                except ResearchError:
                    blocked_candidate = True
                    continue
                candidates.append(candidate)
                if len(candidates) >= args["limit"]:
                    break
            if candidates:
                return "success", candidates, None
            return ("blocked", [], "candidate_url_blocked") if blocked_candidate else ("no_results", [], None)
        try:
            import json

            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return "parse_failed", [], "invalid_json"
        items = path_get(payload, search.get("items_path") or search.get("result_path"))
        if items is None:
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                for key in ("items", "data", "results", "list", "rows", "records"):
                    if isinstance(payload.get(key), list):
                        items = payload[key]
                        break
        if not isinstance(items, list):
            return "parse_failed", [], "result_list_missing"
        candidates = []
        blocked_candidate = False
        allowed_hosts = self.transport.allowed_hosts(cfg, endpoint)
        for item in items[:args["limit"]]:
            candidate = self.candidate_from_item(cfg, item, final_url)
            if candidate is None:
                continue
            try:
                await validate_network_url(candidate["url"], allowed_hosts,
                                           await self.transport.client_for_validation())
            except ResearchError:
                blocked_candidate = True
                continue
            if not publication_in_range(candidate.get("published_at"), args):
                continue
            candidates.append(candidate)
        if not candidates:
            return ("blocked", [], "candidate_url_blocked") if blocked_candidate else ("no_results", [], None)
        return "success", candidates, None

    async def search(self, args: dict[str, Any]) -> dict[str, Any]:
        args = dict(args)
        args["limit"] = min(args["limit"], int(self.budgets.get("max_results", DEFAULT_BUDGETS["max_results"])))
        configured = [cfg for cfg in self.config.get("sources", [])
                      if cfg.get("enabled", True) is not False and
                      (not args["source_ids"] or str(cfg.get("id") or cfg.get("source_id")) in args["source_ids"])]
        if not configured:
            self.event("search_project_sources", args["purpose"], "no_configuration", query=args["query"],
                       message="no configured source matched source_ids")
            return {"status": "no_configuration", "sources": [],
                    "coverage": {"configured": 0, "queried": 0, "list_scan": False}}
        sources: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        statuses: list[str] = []
        queried = 0
        queried_configs: list[dict[str, Any]] = []
        max_results = int(self.budgets.get("max_results", DEFAULT_BUDGETS["max_results"]))
        for cfg in configured:
            if len(sources) >= max_results:
                break
            query_args = dict(args)
            query_args["limit"] = min(args["limit"], max_results - len(sources))
            queried += 1
            queried_configs.append(cfg)
            status, candidates, error = await self.query_config(cfg, query_args)
            statuses.append(status)
            for candidate in candidates:
                self.storage.upsert_source(source_ref=candidate["source_ref"], source_id=candidate["source_id"],
                                            url=candidate["url"], title=candidate["title"],
                                            published_at=candidate.get("published_at"), kind=candidate["kind"],
                                            status="candidate", job_id=self.job_id, project_id=self.project_id)
                source = {key: candidate[key] for key in ("source_ref", "title", "url", "published_at", "kind")}
                source.update({"source_version": None, "fetched_at": None, "status": "candidate",
                               "relation": "unconfirmed", "relation_status": "unconfirmed", "pages": None,
                               "parent_source_ref": None})
                if candidate.get("summary"):
                    source["summary"] = candidate["summary"]
                sources.append(source)
            if error:
                errors.append({"source_id": str(cfg.get("id") or cfg.get("source_id") or ""),
                               "status": status, "error": error})
        if "limited" in statuses:
            overall = "limited"
        elif sources:
            overall = "success"
        elif "blocked" in statuses:
            overall = "blocked"
        elif "parse_failed" in statuses:
            overall = "parse_failed"
        elif "failed" in statuses:
            overall = "failed"
        elif statuses and all(status == "no_configuration" for status in statuses):
            overall = "no_configuration"
        else:
            overall = "no_results"
        limited_scan = any(
            str((cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("adapter") or "").lower()
            in {"list_scan", "html_list", "bounded_list"}
            or isinstance(cfg.get("list_scan"), dict) or isinstance(cfg.get("list"), dict)
            or ((cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("list_url") is not None
                and not ((cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("url")
                         or (cfg.get("search") if isinstance(cfg.get("search"), dict) else cfg).get("endpoint")))
            for cfg in configured
        )
        origins = sorted({host for record in self.records
                          for url in (record.get("original_url"), record.get("canonical_url"))
                          if isinstance(url, str) and (host := urlsplit(url).hostname)})
        uncovered = [host for host in origins if not any(
            (allowed := self.transport.allowed_hosts(cfg)) and host_allowed(host, allowed)
            for cfg in queried_configs
        )]
        source_ids = [str(cfg.get("id") or cfg.get("source_id") or "") for cfg in queried_configs]
        names = [str(cfg.get("name") or cfg.get("id") or cfg.get("source_id") or "") for cfg in queried_configs]
        coverage_note = f"本次检索仅限：{'、'.join(names)}。"
        if limited_scan:
            coverage_note += "包含有限列表扫描，不能代表站内完整检索。"
        if uncovered:
            coverage_note += f"未覆盖本项目公告平台：{'、'.join(uncovered)}。"
        if errors:
            coverage_note += "部分来源受限或失败：" + "; ".join(item["error"] for item in errors)
        self.event("search_project_sources", args["purpose"], overall,
                   [item["source_ref"] for item in sources], query=args["query"], message=coverage_note)
        return {"status": overall, "sources": sources,
                "coverage": {"configured": len(configured), "queried": queried,
                              "offset": args["offset"], "limit": args["limit"], "statuses": statuses,
                              "list_scan": limited_scan, "source_ids": source_ids,
                              "uncovered_origins": uncovered},
                "errors": errors}


__all__ = ["SourceSearcher"]
