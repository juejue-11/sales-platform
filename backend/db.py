from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_name TEXT,
    canonical_url TEXT,
    original_url TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    project_name TEXT,
    published_at TEXT,
    buyer TEXT,
    notice_number TEXT,
    deadline_at TEXT,
    company TEXT,
    equipment_json TEXT NOT NULL DEFAULT '[]',
    facts_json TEXT NOT NULL DEFAULT '[]',
    inferences_json TEXT NOT NULL DEFAULT '[]',
    project_key TEXT,
    possible_duplicate_of INTEGER,
    duplicate_of INTEGER,
    related_record_id INTEGER,
    archive_path TEXT,
    content_sha256 TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    current_version INTEGER NOT NULL DEFAULT 1,
    fetched_at TEXT,
    parse_status TEXT,
    parse_error TEXT,
    body_text TEXT NOT NULL DEFAULT '',
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (possible_duplicate_of) REFERENCES records(id),
    FOREIGN KEY (duplicate_of) REFERENCES records(id),
    FOREIGN KEY (related_record_id) REFERENCES records(id)
);
CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id);
CREATE INDEX IF NOT EXISTS idx_records_deadline ON records(deadline_at);
CREATE INDEX IF NOT EXISTS idx_records_project_key ON records(project_key);
CREATE TABLE IF NOT EXISTS record_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    fetched_at TEXT,
    original_url TEXT,
    archive_path TEXT,
    content_sha256 TEXT,
    body_text TEXT,
    parse_status TEXT,
    parse_error TEXT,
    changed_kind TEXT NOT NULL DEFAULT 'content',
    created_at TEXT NOT NULL,
    UNIQUE(record_id, version)
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_records (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    record_id INTEGER NOT NULL UNIQUE REFERENCES records(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'primary',
    added_at TEXT NOT NULL,
    PRIMARY KEY(project_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_project_records_project ON project_records(project_id);
CREATE TABLE IF NOT EXISTS project_gate (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    override_action TEXT,
    reason TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL,
    summary TEXT NOT NULL,
    demand_summary TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    unknowns_json TEXT NOT NULL DEFAULT '[]',
    next_actions_json TEXT NOT NULL DEFAULT '[]',
    options_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_judgments_project ON judgments(project_id, id DESC);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    selected_option_id TEXT,
    judgment_id INTEGER,
    judgment_fingerprint TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, id DESC);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    input_fingerprint TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_links_project ON links(project_id, target_project_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript(SCHEMA)
    # Lightweight migrations for databases created by an earlier development
    # build. Secrets and source data remain in this backend database only.
    decision_columns = {row[1] for row in db.execute("PRAGMA table_info(decisions)")}
    if "judgment_id" not in decision_columns:
        db.execute("ALTER TABLE decisions ADD COLUMN judgment_id INTEGER")
    if "judgment_fingerprint" not in decision_columns:
        db.execute("ALTER TABLE decisions ADD COLUMN judgment_fingerprint TEXT")
    return db


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
