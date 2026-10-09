from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import as_list, clean_text, json_text, normalize_timestamp, now_iso, sha256_text


class ImportErrorDetail(ValueError):
    pass


@dataclass
class ImportResult:
    inserted: int
    updated: int
    unchanged: int
    total: int


def _normal_record(raw: dict[str, Any], line_number: int) -> dict[str, Any]:
    try:
        record_id = int(raw.get("id"))
    except (TypeError, ValueError) as exc:
        raise ImportErrorDetail(f"line {line_number}: id must be an integer") from exc
    if record_id <= 0:
        raise ImportErrorDetail(f"line {line_number}: id must be positive")
    for field in ("equipment", "facts", "inferences"):
        value = raw.get(field)
        if value is not None and not isinstance(value, list):
            raise ImportErrorDetail(f"line {line_number}: {field} must be an array or null")
    try:
        current_version = max(1, int(raw.get("current_version") or 1))
    except (TypeError, ValueError) as exc:
        raise ImportErrorDetail(f"line {line_number}: current_version must be an integer") from exc
    title = clean_text(raw.get("title")) or clean_text(raw.get("project_name")) or f"记录 {record_id}"
    kind = clean_text(raw.get("kind")) or "tender"
    source_id = clean_text(raw.get("source_id")) or "unknown"
    now = now_iso()
    result = {
        "id": record_id,
        "source_id": source_id,
        "source_name": clean_text(raw.get("source_name")),
        "canonical_url": clean_text(raw.get("canonical_url")) or clean_text(raw.get("original_url")),
        "original_url": clean_text(raw.get("original_url")) or clean_text(raw.get("canonical_url")),
        "kind": kind,
        "title": title,
        "project_name": clean_text(raw.get("project_name")) or title,
        "published_at": normalize_timestamp(raw.get("published_at")) or clean_text(raw.get("published_at")),
        "buyer": clean_text(raw.get("buyer")),
        "notice_number": clean_text(raw.get("notice_number")),
        "deadline_at": normalize_timestamp(raw.get("deadline_at"), end_of_day=True) or clean_text(raw.get("deadline_at")),
        "company": clean_text(raw.get("company")),
        "equipment": as_list(raw.get("equipment")),
        "facts": as_list(raw.get("facts")),
        "inferences": as_list(raw.get("inferences")),
        "project_key": clean_text(raw.get("project_key")),
        "possible_duplicate_of": raw.get("possible_duplicate_of"),
        "duplicate_of": raw.get("duplicate_of"),
        "related_record_id": raw.get("related_record_id"),
        "archive_path": clean_text(raw.get("archive_path")),
        "content_sha256": clean_text(raw.get("content_sha256")),
        "first_seen_at": normalize_timestamp(raw.get("first_seen_at")) or now,
        "last_seen_at": normalize_timestamp(raw.get("last_seen_at")) or now,
        "current_version": current_version,
        "fetched_at": normalize_timestamp(raw.get("fetched_at")) or normalize_timestamp(raw.get("last_seen_at")) or now,
        "parse_status": clean_text(raw.get("parse_status")) or "ok",
        "parse_error": clean_text(raw.get("parse_error")),
        "body_text": str(raw.get("body_text") or ""),
    }
    for key in ("possible_duplicate_of", "duplicate_of", "related_record_id"):
        value = result[key]
        if value in (None, ""):
            result[key] = None
        else:
            try:
                result[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise ImportErrorDetail(f"line {line_number}: {key} must be an integer or null") from exc
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            rows: list[dict[str, Any]] = []
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ImportErrorDetail(f"line {line_number}: invalid JSON") from exc
                if not isinstance(raw, dict):
                    raise ImportErrorDetail(f"line {line_number}: JSON record must be an object")
                rows.append(_normal_record(raw, line_number))
    except UnicodeDecodeError as exc:
        raise ImportErrorDetail("import file must be UTF-8") from exc
    except OSError as exc:
        raise ImportErrorDetail(f"cannot read import file: {exc}") from exc
    if not rows:
        raise ImportErrorDetail("import file contains no records")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ImportErrorDetail("import file contains duplicate record ids")
    return rows


def _content_signature(record: dict[str, Any]) -> str:
    values = {key: record.get(key) for key in (
        "source_id", "canonical_url", "original_url", "kind", "title", "project_name", "published_at",
        "buyer", "notice_number", "deadline_at", "company", "project_key",
        "possible_duplicate_of", "duplicate_of", "related_record_id", "body_text", "parse_status", "parse_error",
    )}
    values["equipment_json"] = record.get("equipment", [])
    values["facts_json"] = record.get("facts", [])
    values["inferences_json"] = record.get("inferences", [])
    values["body_text"] = re.sub(r"\s+", " ", str(values.get("body_text") or "")).strip()
    return sha256_text(json_text(values))


def _db_signature(row: sqlite3.Row) -> str:
    values = {key: row[key] for key in (
        "source_id", "canonical_url", "original_url", "kind", "title", "project_name", "published_at",
        "buyer", "notice_number", "deadline_at", "company", "equipment_json", "facts_json", "inferences_json",
        "project_key", "possible_duplicate_of", "duplicate_of", "related_record_id", "body_text", "parse_status", "parse_error",
    )}
    # Decode JSON arrays so formatting differences do not trigger an AI run.
    for key in ("equipment_json", "facts_json", "inferences_json"):
        try:
            values[key] = json.loads(values[key] or "[]")
        except (TypeError, json.JSONDecodeError):
            pass
    values["body_text"] = re.sub(r"\s+", " ", str(values.get("body_text") or "")).strip()
    return sha256_text(json_text(values))


def _insert_version(db: sqlite3.Connection, record: dict[str, Any], version: int, changed_kind: str) -> None:
    db.execute(
        """INSERT OR REPLACE INTO record_versions
        (record_id,version,fetched_at,original_url,archive_path,content_sha256,body_text,parse_status,parse_error,changed_kind,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (record["id"], version, record["fetched_at"], record["original_url"], record["archive_path"],
         record["content_sha256"], record["body_text"], record["parse_status"], record["parse_error"], changed_kind, now_iso()),
    )


def _record_values(record: dict[str, Any], current_version: int, *, relations: bool = True) -> tuple[Any, ...]:
    return (
        record["id"], record["source_id"], record["source_name"], record["canonical_url"], record["original_url"],
        record["kind"], record["title"], record["project_name"], record["published_at"], record["buyer"],
        record["notice_number"], record["deadline_at"], record["company"], json_text(record["equipment"]),
        json_text(record["facts"]), json_text(record["inferences"]), record["project_key"],
        record["possible_duplicate_of"] if relations else None,
        record["duplicate_of"] if relations else None,
        record["related_record_id"] if relations else None,
        record["archive_path"], record["content_sha256"],
        record["first_seen_at"], record["last_seen_at"], current_version, record["fetched_at"], record["parse_status"],
        record["parse_error"], record["body_text"], json_text(record),
    )


def _mark_stale(db: sqlite3.Connection, project_id: int) -> None:
    db.execute("UPDATE judgments SET stale=1 WHERE project_id=?", (project_id,))
    db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))


def _project_for_record(db: sqlite3.Connection, record_id: int) -> int | None:
    row = db.execute("SELECT project_id FROM project_records WHERE record_id=?", (record_id,)).fetchone()
    return int(row[0]) if row else None


def _normalized_project_title(value: str | None) -> str:
    value = str(value or "")
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"(?:更正|变更|澄清|中标|成交|结果|候选人|公开|询价|招标|采购|公告)", "", value)
    return value


def _safe_related_target(db: sqlite3.Connection, record: dict[str, Any]) -> tuple[int | None, str]:
    """Only merge a related notice after checking independent source identity."""
    target_id = record.get("related_record_id")
    if target_id is None:
        return None, "primary"
    target = db.execute("SELECT * FROM records WHERE id=?", (int(target_id),)).fetchone()
    if target is None:
        return None, "primary"
    same_source = str(target["source_id"] or "") == str(record.get("source_id") or "")
    same_notice = bool(record.get("notice_number")) and bool(target["notice_number"]) and \
        str(record.get("notice_number")) == str(target["notice_number"])
    same_buyer = bool(record.get("buyer")) and bool(target["buyer"]) and \
        str(record.get("buyer")).strip() == str(target["buyer"]).strip()
    same_title = _normalized_project_title(record.get("title")) == _normalized_project_title(target["title"])
    if same_source and same_notice and same_buyer and same_title:
        return _project_for_record(db, int(target_id)), "related"
    return None, "primary"


def _merge_projects(db: sqlite3.Connection, source_id: int, target_id: int) -> int:
    if source_id == target_id:
        return target_id
    # Keep the target project's history as the canonical history. The source
    # project's records remain available, with their original relation retained.
    source_rows = db.execute("SELECT record_id,relation FROM project_records WHERE project_id=?", (source_id,)).fetchall()
    for row in source_rows:
        db.execute("UPDATE project_records SET project_id=?, relation=? WHERE record_id=?", (target_id, row["relation"], row["record_id"]))
    # Keep historical AI judgments, decisions, and jobs attached to the
    # canonical project when a reliable source relation is discovered later.
    for table in ("judgments", "decisions", "jobs", "agent_runs"):
        db.execute(f"UPDATE {table} SET project_id=? WHERE project_id=?", (target_id, source_id))
    source_gate = db.execute("SELECT override_action,reason FROM project_gate WHERE project_id=?", (source_id,)).fetchone()
    target_gate = db.execute("SELECT override_action,reason FROM project_gate WHERE project_id=?", (target_id,)).fetchone()
    if source_gate and target_gate:
        # Keep one override row while retaining the other explanation in its
        # audit reason. The source records and decisions remain intact.
        merged_reason = f"{target_gate['reason'] or ''}；合并项目原备注：{source_gate['reason'] or ''}".strip("；")
        merged_action = target_gate["override_action"] or source_gate["override_action"]
        db.execute("UPDATE project_gate SET override_action=?,reason=?,updated_at=? WHERE project_id=?",
                   (merged_action, merged_reason, now_iso(), target_id))
        db.execute("DELETE FROM project_gate WHERE project_id=?", (source_id,))
    elif source_gate:
        db.execute("UPDATE project_gate SET project_id=? WHERE project_id=?", (target_id, source_id))
    db.execute("UPDATE links SET project_id=? WHERE project_id=?", (target_id, source_id))
    db.execute("UPDATE links SET target_project_id=? WHERE target_project_id=?", (target_id, source_id))
    db.execute("DELETE FROM projects WHERE id=?", (source_id,))
    # A merge changes the primary record set and any bounded related context.
    # Preserve old judgments as history, but never leave the moved project's
    # latest conclusion selectable as if its inputs were unchanged.
    _mark_stale(db, target_id)
    return target_id


def _ensure_project(db: sqlite3.Connection, record: dict[str, Any]) -> int:
    record_id = int(record["id"])
    existing = _project_for_record(db, record_id)
    target: int | None = None
    relation = "primary"
    linked = record.get("duplicate_of")
    if linked is not None:
        target = _project_for_record(db, int(linked))
        if target:
            relation = "duplicate"
    if target is None:
        target, relation = _safe_related_target(db, record)
    if existing is not None:
        if target is not None and target != existing:
            existing = _merge_projects(db, existing, target)
        db.execute("UPDATE project_records SET relation=? WHERE record_id=?", (relation, record_id))
        return existing
    if target is None:
        target = record_id
        db.execute("INSERT OR IGNORE INTO projects(id,created_at,updated_at) VALUES (?,?,?)", (target, now_iso(), now_iso()))
    db.execute("INSERT OR REPLACE INTO project_records(project_id,record_id,relation,added_at) VALUES (?,?,?,?)",
               (target, record_id, relation, now_iso()))
    return target


def import_records(db: sqlite3.Connection, path: Path) -> ImportResult:
    """Read and validate the entire file before entering one SQLite transaction."""
    rows = read_jsonl(path)
    inserted = updated = unchanged = 0
    # The caller records import status. This function itself is atomic: any
    # exception rolls back all record and project changes.
    with db:
        for record in rows:
            old = db.execute("SELECT * FROM records WHERE id=?", (record["id"],)).fetchone()
            if old is None:
                db.execute(
                    """INSERT INTO records
                    (id,source_id,source_name,canonical_url,original_url,kind,title,project_name,published_at,buyer,notice_number,
                     deadline_at,company,equipment_json,facts_json,inferences_json,project_key,possible_duplicate_of,duplicate_of,
                     related_record_id,archive_path,content_sha256,first_seen_at,last_seen_at,current_version,fetched_at,parse_status,
                     parse_error,body_text,source_payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _record_values(record, record["current_version"], relations=False),
                )
                _insert_version(db, record, record["current_version"], "initial")
                inserted += 1
            else:
                old_signature = _db_signature(old)
                new_signature = _content_signature(record)
                old_project = _project_for_record(db, int(record["id"]))
                if old_signature == new_signature:
                    metadata_changed = (int(record["current_version"] or 1) != int(old["current_version"] or 1) or
                                        record["content_sha256"] != old["content_sha256"] or
                                        record["archive_path"] != old["archive_path"])
                    if metadata_changed:
                        version = max(int(old["current_version"] or 1) + 1,
                                      int(record["current_version"] or 1))
                        db.execute("""UPDATE records SET source_name=?,archive_path=?,content_sha256=?,last_seen_at=?,fetched_at=?,
                                       current_version=?,source_payload_json=? WHERE id=?""",
                                   (record["source_name"], record["archive_path"], record["content_sha256"], record["last_seen_at"],
                                    record["fetched_at"], version, json_text(record), record["id"]))
                        _insert_version(db, record, version, "metadata")
                        updated += 1
                    else:
                        unchanged += 1
                        db.execute("""UPDATE records SET source_name=?,last_seen_at=?,fetched_at=?,
                                       source_payload_json=? WHERE id=?""",
                                   (record["source_name"], record["last_seen_at"], record["fetched_at"],
                                    json_text(record), record["id"]))
                else:
                    version = max(int(old["current_version"] or 1) + 1, int(record["current_version"] or 1))
                    db.execute(
                        """UPDATE records SET source_id=?,source_name=?,canonical_url=?,original_url=?,kind=?,title=?,project_name=?,
                           published_at=?,buyer=?,notice_number=?,deadline_at=?,company=?,equipment_json=?,facts_json=?,inferences_json=?,
                           project_key=?,possible_duplicate_of=?,duplicate_of=?,related_record_id=?,archive_path=?,content_sha256=?,
                           first_seen_at=?,last_seen_at=?,current_version=?,fetched_at=?,parse_status=?,parse_error=?,body_text=?,source_payload_json=?
                           WHERE id=?""",
                        (*_record_values(record, version, relations=False)[1:], record["id"]),
                    )
                    changed_kind = "content" if str(old["body_text"] or "") != record["body_text"] else "metadata"
                    _insert_version(db, record, version, changed_kind)
                    # The signature intentionally excludes content_sha256 and
                    # current_version, so any difference here is a正文/结构化
                    # change that should invalidate an AI judgment. A pure page
                    # template hash change keeps the signature unchanged.
                    if old_project is not None:
                        _mark_stale(db, old_project)
                    updated += 1
        # Resolve foreign-key relations only after every record has been staged;
        # JSONL order is not guaranteed to put a referenced id first.
        for record in rows:
            relation_values: dict[str, int | None] = {}
            for key in ("possible_duplicate_of", "duplicate_of", "related_record_id"):
                candidate = record.get(key)
                exists = candidate is not None and db.execute("SELECT 1 FROM records WHERE id=?", (candidate,)).fetchone()
                relation_values[key] = int(candidate) if exists else None
            db.execute("UPDATE records SET possible_duplicate_of=?,duplicate_of=?,related_record_id=? WHERE id=?",
                       (relation_values["possible_duplicate_of"], relation_values["duplicate_of"],
                        relation_values["related_record_id"], record["id"]))
        for record in rows:
            _ensure_project(db, record)
        # An explicit duplicate/relation may point to a record that appeared
        # later in the file. Resolve all relations again once every id exists.
        for record in rows:
            _ensure_project(db, record)
    return ImportResult(inserted=inserted, updated=updated, unchanged=unchanged, total=len(rows))
