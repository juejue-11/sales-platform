"""User supplied procurement documents and append-only explanation corrections.

The caller owns authorization, locking and the transaction. This module never
fetches URLs or writes into the collector archive. Uploaded bytes are retained
in the platform database so a parser failure does not lose the supplied file.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import sqlite3
from pathlib import PurePath
from typing import Any
from urllib.parse import quote

from ..core.utils import json_text, now_iso
from ..documents.public_documents import MAX_BYTES, MAX_TEXT, parse_document
from .model import normalize_document


class SupplementError(ValueError):
    """A validation error safe to present to the user."""


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS procurement_uploads (
            document_id TEXT PRIMARY KEY REFERENCES procurement_documents(id),
            project_id INTEGER NOT NULL REFERENCES projects(id),
            original_bytes BLOB NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS procurement_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            requirement_id TEXT NOT NULL, text TEXT NOT NULL, reason TEXT NOT NULL,
            material_version TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_procurement_corrections_project
            ON procurement_corrections(project_id, id);
    """)


def prepare_document(payload: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Validate and parse one bounded file without trusting a client path."""
    record_id = payload.get("record_id")
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
        raise SupplementError("请选择本次采购的资料记录")
    name = payload.get("name")
    if (not isinstance(name, str) or not name.strip() or len(name) > 200
            or any(char in name for char in '/\\\x00\r\n') or name in {'.', '..'}):
        raise SupplementError("文件名不合法")
    name = name.strip()
    extension = PurePath(name).suffix.lower()
    if extension not in {'.pdf', '.docx', '.doc', '.txt'}:
        raise SupplementError("支持 PDF、Word 和 UTF-8 文本文件")
    encoded = payload.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise SupplementError("文件不能超过 12 MiB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise SupplementError("文件内容编码无效") from None
    if not raw or len(raw) > MAX_BYTES:
        raise SupplementError("请提供非空且不超过 12 MiB 的文件")
    digest = hashlib.sha256(raw).hexdigest()
    document_id = f"supplement-{record_id}-{digest[:32]}"
    # This inert URI is used only for the existing format parser. The saved
    # document URL is empty: an upload never becomes an external source URL.
    document = parse_document(raw, record_id=record_id,
                              url=f"upload://local/{quote(name)}", name=name)
    if extension == '.txt':
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            document.update(status='unreadable', error='文本文件需使用 UTF-8 编码')
        else:
            if len(text) > MAX_TEXT:
                document.update(status='failed', error='文本超过解析上限')
            else:
                document.update(text=text, status='parsed' if text.strip() else 'unreadable',
                                mime_type='text/plain', error='')
    document.update(id=document_id, url='', name=name)
    for item in document.get('items', []):
        item['source_document_id'] = document_id
    normalized = normalize_document(document, record_id)
    if normalized is None:
        raise SupplementError("文件解析结果无效")
    return normalized, raw


def save_document(db: sqlite3.Connection, project_id: int, document: dict[str, Any], raw: bytes) -> None:
    membership = db.execute('SELECT 1 FROM project_records WHERE project_id=? AND record_id=?',
                            (project_id, document['record_id'])).fetchone()
    if not membership:
        raise SupplementError("资料记录不属于本次采购")
    db.execute("""INSERT INTO procurement_documents
        (id,record_id,name,url,status,pages,sha256,fetched_at,mime_type,byte_size,
         text,page_texts_json,items_json,error,is_current,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(id) DO UPDATE SET is_current=1""",
        (document['id'], document['record_id'], document['name'], '', document['status'],
         document['pages'], document['sha256'], document['fetched_at'], document['mime_type'],
         document['byte_size'], document['text'], json_text(document['page_texts']),
         json_text(document['items']), document['error'], now_iso()))
    db.execute("""INSERT INTO procurement_uploads(document_id,project_id,original_bytes,created_at)
        VALUES (?,?,?,?) ON CONFLICT(document_id) DO NOTHING""",
        (document['id'], project_id, raw, now_iso()))


def uploaded_file(db: sqlite3.Connection, project_id: int, document_id: str) -> dict[str, Any] | None:
    row = db.execute("""SELECT u.original_bytes,d.name,d.mime_type FROM procurement_uploads u
        JOIN procurement_documents d ON d.id=u.document_id
        JOIN project_records pr ON pr.record_id=d.record_id
        WHERE pr.project_id=? AND u.document_id=?""", (project_id, document_id)).fetchone()
    return dict(row) if row else None


def merge_project(db: sqlite3.Connection, source_id: int, target_id: int) -> None:
    """Retain uploads and corrections when an importer unifies a project."""
    for table in ('procurement_uploads', 'procurement_corrections'):
        db.execute(f'UPDATE {table} SET project_id=? WHERE project_id=?', (target_id, source_id))


def corrections(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute("""SELECT id,requirement_id,text,reason,
        material_version,created_at FROM procurement_corrections WHERE project_id=? ORDER BY id""",
        (project_id,)).fetchall()]


def save_correction(db: sqlite3.Connection, project_id: int, payload: dict[str, Any],
                    *, material_version: str, requirement_ids: set[str]) -> list[dict[str, Any]]:
    requirement_id = payload.get('requirement_id')
    if requirement_id not in requirement_ids and requirement_id != 'omission':
        raise SupplementError("请选择已有要求或反馈遗漏")
    values = []
    for key, limit in [('text', 12000), ('reason', 2000)]:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise SupplementError("请填写纠正内容和具体理由，并控制文本长度")
        values.append(value.strip())
    db.execute("""INSERT INTO procurement_corrections
        (project_id,requirement_id,text,reason,material_version,created_at) VALUES (?,?,?,?,?,?)""",
        (project_id, requirement_id, *values, material_version, now_iso()))
    return corrections(db, project_id)
