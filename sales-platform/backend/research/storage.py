"""SQLite persistence for source identities, immutable versions, and evidence."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Any, Iterable

from .config import json_text, json_value, now
from .parsing import row, source_shape


class ResearchStorage:
    """Database adapter used by a single project-scoped research session."""

    def __init__(self, db: Any, *, project_id: int | None = None,
                 job_id: int | None = None, db_lock: Any = None):
        self.db = db
        self.project_id = int(project_id) if project_id is not None else None
        self.job_id = int(job_id) if job_id is not None else None
        self.db_lock = db_lock

    @contextmanager
    def guard(self):
        if self.db_lock is not None and hasattr(self.db_lock, "__enter__"):
            with self.db_lock:
                yield
        else:
            yield

    def commit(self) -> None:
        with self.guard():
            self.db.commit()

    def authorization_error(self, project_id: int | None = None, job_id: int | None = None) -> str | None:
        project_id = self.project_id if project_id is None else int(project_id)
        job_id = self.job_id if job_id is None else int(job_id)
        if project_id is None or job_id is None:
            return None
        project = self.db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
        job = self.db.execute("SELECT project_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if project is None:
            return "project_not_found"
        if job is None:
            return "job_not_found"
        if int(job["project_id"]) != project_id:
            return "job_project_mismatch"
        return None

    def project_source_row(self, source_ref: str, project_id: int | None = None) -> tuple[
            dict[str, Any], dict[str, Any] | None, dict[str, Any]] | None:
        project_id = self.project_id if project_id is None else int(project_id)
        row_value = self.db.execute(
            """SELECT s.*, ps.latest_version, ps.status AS project_status, ps.relation,
                      ps.relation_status, ps.scope
                 FROM research_sources s JOIN research_project_sources ps
                   ON ps.source_ref=s.source_ref
                WHERE ps.project_id=? AND s.source_ref=?""",
            (project_id, source_ref),
        ).fetchone()
        if row_value is None:
            return None
        source = dict(row_value)
        version = None
        if source.get("latest_version"):
            version = row(self.db.execute(
                "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                (source_ref, source["latest_version"]),
            ).fetchone())
        relation = {"relation": source.get("relation"), "relation_status": source.get("relation_status"),
                    "scope": source.get("scope")}
        return source, version, relation

    def list_sources(self, project_id: int | None = None) -> list[dict[str, Any]]:
        project_id = self.project_id if project_id is None else int(project_id)
        rows = self.db.execute(
            """SELECT s.*, ps.latest_version, ps.status AS project_status, ps.relation,
                      ps.relation_status, ps.scope
                 FROM research_sources s JOIN research_project_sources ps
                   ON ps.source_ref=s.source_ref
                WHERE ps.project_id=? ORDER BY ps.updated_at DESC, s.source_ref LIMIT 50""",
            (project_id,),
        ).fetchall()
        values: list[dict[str, Any]] = []
        for raw in rows:
            source = dict(raw)
            version = None
            if source.get("latest_version"):
                version = row(self.db.execute(
                    "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                    (source["source_ref"], source["latest_version"]),
                ).fetchone())
            relation = {"relation": source.get("relation"), "relation_status": source.get("relation_status"),
                        "scope": source.get("scope")}
            value = source_shape(source, version, relation)
            if version is None:
                value["status"] = source.get("project_status") or source.get("status") or "candidate"
                value["url"] = source.get("canonical_url") or ""
                value["title"] = source.get("title") or ""
                value["published_at"] = source.get("published_at")
            values.append(value)
        return values

    def read_saved_source(self, source_ref: str, version: str, *, page: int | None = None,
                          offset: int = 0, limit: int = 4000,
                          project_id: int | None = None) -> dict[str, Any]:
        project_id = self.project_id if project_id is None else int(project_id)
        if isinstance(page, bool) or (page is not None and (not isinstance(page, int) or page < 1)):
            raise ValueError("invalid_page")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("invalid_offset")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8000:
            raise ValueError("invalid_limit")
        found = self.project_source_row(str(source_ref), project_id)
        if found is None:
            raise LookupError("source_not_in_project")
        source, _latest, relation = found
        version_row = row(self.db.execute(
            "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
            (str(source_ref), str(version)),
        ).fetchone())
        if version_row is None:
            raise LookupError("source_version_not_found")
        historical = self.db.execute(
            """SELECT relation,relation_status,scope FROM research_source_relations
                WHERE project_id=? AND source_ref=? AND source_version=?
                ORDER BY id DESC LIMIT 1""",
            (project_id, str(source_ref), str(version)),
        ).fetchone()
        if historical is not None:
            relation = dict(historical)
        pages = self._pages(version_row)
        selected: list[dict[str, Any]]
        if page is None:
            selected = pages[:1]
        else:
            selected = [item for item in pages if item.get("page") is not None and int(item.get("page")) == page]
        if not selected:
            raise LookupError("source_page_not_found")
        selected_page = selected[0].get("page")
        full_text = "\n".join(str(item.get("text") or "") for item in selected)
        fragments = [{"page": item.get("page"), "text": str(item.get("text") or "")[offset:offset + limit]}
                     for item in selected]
        text = "\n".join(item["text"] for item in fragments)
        source_result = source_shape(source, version_row, relation)
        source_result.update({"page_texts": fragments,
                              "scope": relation.get("scope") or version_row.get("scope") or "page"})
        return {
            "source": dict(source_result),
            "text": text,
            "page": selected_page,
            "offset": offset,
            "total_chars": len(full_text),
            "has_more": offset + len(text) < len(full_text),
        }

    @staticmethod
    def _pages(version: dict[str, Any]) -> list[dict[str, Any]]:
        pages = json_value(version.get("page_texts_json"), [])
        if not pages and version.get("body_text"):
            pages = [{"page": None, "text": str(version.get("body_text") or "")}]
        return pages if isinstance(pages, list) else []

    def current_dependencies(self, dependencies: Iterable[dict[str, Any]] | None,
                             project_id: int | None = None) -> list[dict[str, Any]]:
        project_id = self.project_id if project_id is None else int(project_id)
        result: list[dict[str, Any]] = []
        dependency_values: Iterable[Any] = [dependencies] if isinstance(dependencies, dict) else (dependencies or [])
        for dependency in dependency_values:
            if not isinstance(dependency, dict):
                continue
            source_ref = str(dependency.get("source_ref") or "")
            source_version = str(dependency.get("source_version") or dependency.get("version") or "")
            if not source_ref or not source_version:
                continue
            found = self.project_source_row(source_ref, project_id)
            if found is None:
                result.append({"source_ref": source_ref, "source_version": source_version,
                               "current_source_version": None, "relation": None,
                               "relation_status": "unconfirmed", "changed": True,
                               "reason": "source_removed_or_unauthorized"})
                continue
            _source, latest, relation = found
            current_version = (latest or {}).get("version")
            expected_relation = dependency.get("relation") or dependency.get("verified_relation")
            current_relation = relation.get("relation")
            current_status = relation.get("relation_status")
            changed = bool(current_version and current_version != source_version)
            relation_changed = bool(expected_relation and current_relation != expected_relation)
            ignored_job = dependency.get("job_id")
            if ignored_job is not None:
                try:
                    ignored_job = int(ignored_job)
                except (TypeError, ValueError):
                    ignored_job = None
            latest_relation = self.db.execute(
                "SELECT * FROM research_source_relations WHERE project_id=? AND source_ref=? "
                "ORDER BY id DESC LIMIT 1", (project_id, source_ref)
            ).fetchone()
            latest_relation_dict = row(latest_relation)
            if ignored_job is not None and latest_relation_dict and latest_relation_dict.get("job_id") == ignored_job:
                if current_version == source_version:
                    changed = False
                if expected_relation and latest_relation_dict.get("relation") == expected_relation:
                    relation_changed = False
            result.append({
                "source_ref": source_ref,
                "source_version": source_version,
                "current_source_version": current_version,
                "relation": current_relation,
                "relation_status": current_status,
                "changed": changed or relation_changed,
                "reason": "source_version_changed" if changed else ("relation_revised" if relation_changed else "unchanged"),
            })
        return result

    def upsert_source(self, *, source_ref: str, source_id: str, url: str, title: str,
                      published_at: str | None, kind: str, status: str = "candidate",
                      parent_source_ref: str | None = None, scope: str | None = None,
                      job_id: int | None = None, project_id: int | None = None) -> None:
        project_id = self.project_id if project_id is None else int(project_id)
        now_value = now()
        source_scope = scope or ("attachment" if parent_source_ref else "page")
        with self.guard():
            self.db.execute(
                """INSERT INTO research_sources
                   (source_ref,source_id,canonical_url,original_url,title,published_at,kind,status,
                    parent_source_ref,config_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_ref) DO UPDATE SET
                     title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE research_sources.title END,
                     published_at=COALESCE(excluded.published_at,research_sources.published_at),
                     kind=CASE WHEN research_sources.status='candidate' THEN excluded.kind ELSE research_sources.kind END,
                     status=CASE WHEN research_sources.status IN ('candidate','failed','unsupported','unreadable','auth_required')
                                 THEN excluded.status ELSE research_sources.status END,
                     updated_at=excluded.updated_at""",
                (source_ref, source_id, url, url, title, published_at, kind, status,
                 parent_source_ref, source_id, now_value, now_value),
            )
            self.db.execute(
                """INSERT INTO research_project_sources
                   (project_id,source_ref,status,relation,relation_status,scope,discovered_job_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id,source_ref) DO UPDATE SET
                     status=CASE WHEN research_project_sources.status='candidate' THEN excluded.status
                                 ELSE research_project_sources.status END,
                     discovered_job_id=excluded.discovered_job_id,
                     updated_at=excluded.updated_at""",
                (project_id, source_ref, status, "unconfirmed", "unconfirmed",
                 source_scope, job_id, now_value, now_value),
            )
        self.commit()

    def set_source_version(self, source_ref: str, version: str, status: str, *,
                           make_current: bool = True, project_id: int | None = None) -> None:
        project_id = self.project_id if project_id is None else int(project_id)
        with self.guard():
            old = self.db.execute(
                "SELECT latest_version,relation_status FROM research_project_sources WHERE project_id=? AND source_ref=?",
                (project_id, source_ref),
            ).fetchone()
            if not make_current and old is not None and old["latest_version"] is not None:
                self.db.execute(
                    "UPDATE research_sources SET status=?,updated_at=? WHERE source_ref=?",
                    (status, now(), source_ref),
                )
            else:
                changed_version = old is not None and old["latest_version"] not in (None, version)
                if changed_version:
                    self.db.execute(
                        """UPDATE research_project_sources
                              SET latest_version=?,status=?,relation='unconfirmed',
                                  relation_status='unconfirmed',updated_at=?
                            WHERE project_id=? AND source_ref=?""",
                        (version, status, now(), project_id, source_ref),
                    )
                else:
                    self.db.execute(
                        "UPDATE research_project_sources SET latest_version=?,status=?,updated_at=? "
                        "WHERE project_id=? AND source_ref=?",
                        (version, status, now(), project_id, source_ref),
                    )
            self.db.execute(
                "UPDATE research_sources SET status=?,updated_at=? WHERE source_ref=?",
                (status, now(), source_ref),
            )
        self.commit()

    def relation(self, source_ref: str, source_version: str | None = None,
                 project_id: int | None = None) -> dict[str, Any]:
        project_id = self.project_id if project_id is None else int(project_id)
        if source_version:
            historical = self.db.execute(
                """SELECT relation,relation_status,scope FROM research_source_relations
                    WHERE project_id=? AND source_ref=? AND source_version=?
                    ORDER BY id DESC LIMIT 1""",
                (project_id, source_ref, source_version),
            ).fetchone()
            if historical is not None:
                return dict(historical)
            current = self.db.execute(
                "SELECT latest_version FROM research_project_sources WHERE project_id=? AND source_ref=?",
                (project_id, source_ref),
            ).fetchone()
            if current is None or current["latest_version"] != source_version:
                return {"relation": "unconfirmed", "relation_status": "unconfirmed", "scope": "page"}
        value = self.db.execute(
            "SELECT relation,relation_status,scope FROM research_project_sources WHERE project_id=? AND source_ref=?",
            (project_id, source_ref),
        ).fetchone()
        return dict(value) if value else {"relation": "unconfirmed", "relation_status": "unconfirmed", "scope": "page"}

    def source_version_row(self, source_ref: str, version: str | None = None,
                           project_id: int | None = None) -> dict[str, Any] | None:
        project_id = self.project_id if project_id is None else int(project_id)
        if version is None:
            found = self.db.execute(
                """SELECT v.* FROM research_source_versions v
                   JOIN research_project_sources ps ON ps.source_ref=v.source_ref
                  WHERE ps.project_id=? AND v.source_ref=? ORDER BY v.id DESC LIMIT 1""",
                (project_id, source_ref),
            ).fetchone()
        else:
            found = self.db.execute(
                """SELECT v.* FROM research_source_versions v
                   JOIN research_project_sources ps ON ps.source_ref=v.source_ref
                  WHERE ps.project_id=? AND v.source_ref=? AND v.version=?""",
                (project_id, source_ref, version),
            ).fetchone()
        return row(found)

    def persist_version(self, *, source_ref: str, raw: bytes, title: str, url: str,
                        published_at: str | None, kind: str, status: str,
                        body_text: str, page_texts: list[dict[str, Any]],
                        scope: str, parent_source_ref: str | None,
                        error: str | None, job_id: int, project_id: int | None = None) -> dict[str, Any]:
        project_id = self.project_id if project_id is None else int(project_id)
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.db.execute(
            "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
            (source_ref, digest),
        ).fetchone()
        if existing is None:
            current = now()
            with self.guard():
                self.db.execute(
                    """INSERT INTO research_source_versions
                       (source_ref,version,title,url,published_at,fetched_at,kind,status,content_sha256,
                        body_text,page_texts_json,scope,parent_source_ref,error,created_at,created_job_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source_ref, digest, title, url, published_at, current, kind, status, digest,
                     body_text, json_text(page_texts), scope, parent_source_ref, error, current, job_id),
                )
            self.commit()
            existing = self.db.execute(
                "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                (source_ref, digest),
            ).fetchone()
        self.set_source_version(source_ref, digest, status, project_id=project_id)
        return dict(existing) if existing is not None else {}

    def persist_failed_version(self, *, source_ref: str, raw: bytes, url: str,
                               title: str, published_at: str | None, kind: str,
                               status: str, error: str, parent_source_ref: str | None,
                               job_id: int, project_id: int | None = None) -> dict[str, Any]:
        project_id = self.project_id if project_id is None else int(project_id)
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.db.execute(
            "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
            (source_ref, digest),
        ).fetchone()
        if existing is None:
            current = now()
            with self.guard():
                self.db.execute(
                    """INSERT INTO research_source_versions
                       (source_ref,version,title,url,published_at,fetched_at,kind,status,content_sha256,
                        body_text,page_texts_json,scope,parent_source_ref,error,created_at,created_job_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source_ref, digest, title, url, published_at, current, kind, status, digest,
                     "", "[]", "page", parent_source_ref, error, current, job_id),
                )
            self.commit()
            existing = self.db.execute(
                "SELECT * FROM research_source_versions WHERE source_ref=? AND version=?",
                (source_ref, digest),
            ).fetchone()
        old_link = self.db.execute(
            "SELECT latest_version FROM research_project_sources WHERE project_id=? AND source_ref=?",
            (project_id, source_ref),
        ).fetchone()
        self.set_source_version(source_ref, digest, status,
                                make_current=old_link is None or old_link["latest_version"] is None,
                                project_id=project_id)
        return dict(existing) if existing is not None else {}

    def record_relation(self, *, source_ref: str, source_version: str, relation: str,
                        relation_status: str, quote: str, reason: str, scope: str,
                        job_id: int, project_id: int | None = None) -> None:
        project_id = self.project_id if project_id is None else int(project_id)
        with self.guard():
            self.db.execute(
                """INSERT INTO research_source_relations
                   (project_id,job_id,source_ref,source_version,relation,relation_status,quote,reason,scope,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (project_id, job_id, source_ref, source_version, relation, relation_status,
                 quote, reason, scope, now()),
            )
            latest = self.db.execute(
                "SELECT latest_version FROM research_project_sources WHERE project_id=? AND source_ref=?",
                (project_id, source_ref),
            ).fetchone()
            if latest is not None and latest["latest_version"] == source_version:
                self.db.execute(
                    """UPDATE research_project_sources
                          SET relation=?,relation_status=?,scope=?,updated_at=?
                        WHERE project_id=? AND source_ref=?""",
                    (relation, relation_status, scope, now(), project_id, source_ref),
                )
        self.commit()

    def merge_project_research(self, source_project_id: int, target_project_id: int) -> None:
        source_project_id = int(source_project_id)
        target_project_id = int(target_project_id)
        if source_project_id == target_project_id:
            return
        source_links = self.db.execute(
            "SELECT * FROM research_project_sources WHERE project_id=?",
            (source_project_id,),
        ).fetchall()
        for raw_link in source_links:
            link = dict(raw_link)
            target = self.db.execute(
                "SELECT * FROM research_project_sources WHERE project_id=? AND source_ref=?",
                (target_project_id, link["source_ref"]),
            ).fetchone()
            if target is None:
                self.db.execute(
                    """INSERT INTO research_project_sources
                       (project_id,source_ref,latest_version,status,relation,relation_status,scope,
                        discovered_job_id,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (target_project_id, link["source_ref"], link["latest_version"], link["status"],
                     link["relation"], link["relation_status"], link["scope"], link["discovered_job_id"],
                     link["created_at"], link["updated_at"]),
                )
            else:
                existing = dict(target)
                verified = {"verified", "rejected"}
                choose_source = (existing.get("latest_version") is None or
                                 (link.get("updated_at") or "") > (existing.get("updated_at") or ""))
                choose_relation = (link.get("relation_status") in verified and
                                   existing.get("relation_status") not in verified)
                if choose_source or choose_relation:
                    self.db.execute(
                        """UPDATE research_project_sources
                              SET latest_version=CASE WHEN ? THEN ? ELSE latest_version END,
                                  status=CASE WHEN ? THEN ? ELSE status END,
                                  relation=CASE WHEN ? THEN ? ELSE relation END,
                                  relation_status=CASE WHEN ? THEN ? ELSE relation_status END,
                                  scope=CASE WHEN ? THEN ? ELSE scope END,
                                  updated_at=CASE WHEN ? THEN ? ELSE updated_at END
                            WHERE project_id=? AND source_ref=?""",
                        (choose_source, link["latest_version"], choose_source, link["status"],
                         choose_relation, link["relation"], choose_relation, link["relation_status"],
                         choose_relation, link["scope"], choose_source or choose_relation,
                         link["updated_at"], target_project_id, link["source_ref"]),
                    )
        relations = self.db.execute(
            "SELECT * FROM research_source_relations WHERE project_id=? ORDER BY id",
            (source_project_id,),
        ).fetchall()
        for raw_relation in relations:
            relation = dict(raw_relation)
            self.db.execute(
                """INSERT INTO research_source_relations
                   (project_id,job_id,source_ref,source_version,relation,relation_status,quote,reason,scope,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (target_project_id, relation["job_id"], relation["source_ref"], relation["source_version"],
                 relation["relation"], relation["relation_status"], relation["quote"], relation["reason"],
                 relation["scope"], relation["created_at"]),
            )
        self.db.execute("DELETE FROM research_source_relations WHERE project_id=?", (source_project_id,))
        self.db.execute("DELETE FROM research_project_sources WHERE project_id=?", (source_project_id,))


def list_sources(db: Any, project_id: int) -> list[dict[str, Any]]:
    return ResearchStorage(db, project_id=project_id).list_sources()


def read_saved_source(db: Any, project_id: int, source_ref: str, version: str, *,
                      page: int | None = None, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
    return ResearchStorage(db, project_id=project_id).read_saved_source(
        source_ref, version, page=page, offset=offset, limit=limit,
    )


def current_dependencies(db: Any, project_id: int,
                         dependencies: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return ResearchStorage(db, project_id=project_id).current_dependencies(dependencies)


def merge_project_research(db: Any, source_project_id: int, target_project_id: int) -> None:
    ResearchStorage(db).merge_project_research(source_project_id, target_project_id)


__all__ = [
    "ResearchStorage",
    "current_dependencies",
    "list_sources",
    "merge_project_research",
    "read_saved_source",
]
