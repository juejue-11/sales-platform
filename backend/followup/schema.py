"""SQLite schema owned by the procurement follow-up module.

The schema has no dependency on the screening service's table definitions.  A
follow-up row is keyed by project id, while all of its child rows carry that
key as part of their primary key.  This permits a temporary sqlite database to
exercise the module in isolation and also prevents cross-project ids from
being accepted by the update methods.
"""

from __future__ import annotations

import sqlite3
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS followups (
    project_id INTEGER PRIMARY KEY,
    is_following INTEGER NOT NULL DEFAULT 0 CHECK (is_following IN (0,1)),
    stage TEXT NOT NULL DEFAULT 'evaluating'
        CHECK (stage IN ('evaluating','working','submitted','awaiting_result','finished')),
    disposition TEXT NOT NULL DEFAULT 'following'
        CHECK (disposition IN ('following','paused','abandoned')),
    owner TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    outcome TEXT
        CHECK (outcome IS NULL OR outcome IN ('won','lost','abandoned','other')),
    submitted_at TEXT,
    submission_channel TEXT,
    receipt_json TEXT,
    plan_json TEXT,
    plan_id TEXT,
    plan_fingerprint TEXT,
    source_fingerprint TEXT,
    source_version TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0,1)),
    changed_tasks_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followup_tasks (
    project_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    basis TEXT NOT NULL CHECK (basis IN ('required','conditional','suggested')),
    owner TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    prerequisites_json TEXT NOT NULL DEFAULT '[]',
    materials_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo','doing','done','skipped')),
    completion_note TEXT NOT NULL DEFAULT '',
    skipped_reason TEXT,
    plan_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id) REFERENCES followups(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_project_status
    ON followup_tasks(project_id, status, due_at);

CREATE TABLE IF NOT EXISTS followup_materials (
    project_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    task_id TEXT,
    name TEXT NOT NULL,
    basis TEXT NOT NULL CHECK (basis IN ('required','conditional','suggested')),
    status TEXT NOT NULL DEFAULT 'not_prepared'
        CHECK (status IN ('not_prepared','preparing','provided','pending_check','needs_supplement')),
    ai_check_status TEXT NOT NULL DEFAULT 'not_checked'
        CHECK (ai_check_status IN ('not_checked','checking','passed','needs_supplement','failed')),
    ai_check_note TEXT NOT NULL DEFAULT '',
    provided_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    plan_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, task_id) REFERENCES followup_tasks(project_id, id)
        ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_followup_materials_project_status
    ON followup_materials(project_id, status);

CREATE TABLE IF NOT EXISTS followup_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES followups(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_followup_events_project
    ON followup_events(project_id, id DESC);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the module's tables on ``conn`` and enable sqlite FKs.

    The caller owns the connection and may use either the default tuple rows
    or ``sqlite3.Row``; repository code only relies on column names where it
    asks sqlite for a row.
    """

    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("conn must be a sqlite3.Connection")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(followups)")}
    if "is_following" not in columns:
        conn.execute("ALTER TABLE followups ADD COLUMN is_following INTEGER NOT NULL DEFAULT 0")
    if "note" not in columns:
        conn.execute("ALTER TABLE followups ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    conn.commit()

