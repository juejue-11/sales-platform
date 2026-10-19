import json
import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
 entry_url TEXT, adapter TEXT NOT NULL, enabled INTEGER NOT NULL,
 access_status TEXT NOT NULL, access_note TEXT,
 frequency_hours INTEGER NOT NULL DEFAULT 24,
 last_success_at TEXT, last_failure_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, stats_json TEXT
);
CREATE TABLE IF NOT EXISTS source_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES runs(id),
 source_id TEXT NOT NULL REFERENCES sources(id), started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, found_count INTEGER NOT NULL DEFAULT 0,
 fetched_count INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL REFERENCES sources(id),
 canonical_url TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
 published_at TEXT, buyer TEXT, project_name TEXT, notice_number TEXT,
 deadline_at TEXT, company TEXT, equipment_json TEXT NOT NULL DEFAULT '[]',
 facts_json TEXT NOT NULL DEFAULT '[]', inferences_json TEXT NOT NULL DEFAULT '[]',
 project_key TEXT, possible_duplicate_of INTEGER REFERENCES records(id),
 duplicate_of INTEGER REFERENCES records(id),
 related_record_id INTEGER REFERENCES records(id),
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 current_version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(source_id, canonical_url)
);
CREATE INDEX IF NOT EXISTS idx_records_notice ON records(notice_number);
CREATE INDEX IF NOT EXISTS idx_records_project ON records(project_key);
CREATE INDEX IF NOT EXISTS idx_records_deadline ON records(deadline_at);
CREATE TABLE IF NOT EXISTS versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL REFERENCES records(id),
 version INTEGER NOT NULL, fetched_at TEXT NOT NULL, original_url TEXT NOT NULL,
 archive_path TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 mime_type TEXT NOT NULL, body_text TEXT, parse_status TEXT NOT NULL,
 parse_error TEXT, UNIQUE(record_id, version)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL REFERENCES records(id),
 version INTEGER NOT NULL, run_id INTEGER NOT NULL REFERENCES runs(id),
 event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, details TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(occurred_at);
CREATE TABLE IF NOT EXISTS version_attachments (
 record_id INTEGER NOT NULL REFERENCES records(id), version INTEGER NOT NULL,
 attachments_json TEXT NOT NULL DEFAULT '[]', PRIMARY KEY(record_id,version)
);
CREATE TABLE IF NOT EXISTS filtered_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id INTEGER NOT NULL REFERENCES runs(id),
 source_id TEXT NOT NULL REFERENCES sources(id),
 title TEXT NOT NULL,
 url TEXT NOT NULL,
 published_at TEXT,
 reason TEXT NOT NULL,
 filtered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filtered_entries_run ON filtered_entries(run_id);
CREATE INDEX IF NOT EXISTS idx_filtered_entries_day ON filtered_entries(filtered_at);
"""


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    columns = {row[1] for row in db.execute('PRAGMA table_info(records)')}
    if 'related_record_id' not in columns:
        db.execute('ALTER TABLE records ADD COLUMN related_record_id INTEGER REFERENCES records(id)')
    return db


def sync_sources(db, sources):
    for s in sources:
        db.execute("""INSERT INTO sources
            (id,name,category,entry_url,adapter,enabled,access_status,access_note,frequency_hours)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,category=excluded.category,entry_url=excluded.entry_url,
            adapter=excluded.adapter,enabled=excluded.enabled,
            access_status=excluded.access_status,access_note=excluded.access_note,
            frequency_hours=excluded.frequency_hours""",
            (s['id'], s['name'], s['category'], s.get('entry_url'), s['adapter'],
             int(s.get('enabled', False)), s.get('access_status', 'unverified'),
             s.get('access_note', ''), s.get('frequency_hours', 24)))
    db.commit()


def as_dict(row):
    return dict(row) if row is not None else None


def json_value(value):
    return json.dumps(value, ensure_ascii=False)
