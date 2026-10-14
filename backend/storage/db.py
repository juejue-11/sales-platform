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
    options_json TEXT NOT NULL DEFAULT '[]',
    analysis_meta_json TEXT,
    report_json TEXT,
    agent_run_id INTEGER,
    research_dependencies_json TEXT NOT NULL DEFAULT '[]',
    research_policy_version TEXT
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
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    engine TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    steps_json TEXT NOT NULL DEFAULT '[]',
    usage_json TEXT NOT NULL DEFAULT '{}',
    limits_json TEXT NOT NULL DEFAULT '{}',
    investigation_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_job ON agent_runs(job_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id, id DESC);
-- Research sources are deliberately separate from collector records.  A
-- source identity is stable for a URL while every fetched representation is
-- an immutable version.  Project links and relation assessments are scoped by
-- both project and job so a research run cannot read another project's work.
CREATE TABLE IF NOT EXISTS research_sources (
    source_ref TEXT PRIMARY KEY,
    source_id TEXT NOT NULL DEFAULT '',
    canonical_url TEXT NOT NULL,
    original_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    kind TEXT NOT NULL DEFAULT 'page',
    status TEXT NOT NULL DEFAULT 'candidate',
    parent_source_ref TEXT,
    config_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_sources_url ON research_sources(canonical_url);
CREATE INDEX IF NOT EXISTS idx_research_sources_parent ON research_sources(parent_source_ref);
CREATE TABLE IF NOT EXISTS research_source_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_ref TEXT NOT NULL REFERENCES research_sources(source_ref) ON DELETE CASCADE,
    version TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'page',
    status TEXT NOT NULL,
    content_sha256 TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    page_texts_json TEXT NOT NULL DEFAULT '[]',
    scope TEXT NOT NULL DEFAULT 'page',
    parent_source_ref TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    created_job_id INTEGER,
    UNIQUE(source_ref, version)
);
CREATE INDEX IF NOT EXISTS idx_research_source_versions_ref
    ON research_source_versions(source_ref, id DESC);
CREATE TABLE IF NOT EXISTS research_project_sources (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL REFERENCES research_sources(source_ref) ON DELETE CASCADE,
    latest_version TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',
    relation TEXT NOT NULL DEFAULT 'unconfirmed',
    relation_status TEXT NOT NULL DEFAULT 'unconfirmed',
    scope TEXT NOT NULL DEFAULT 'candidate',
    discovered_job_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_research_project_sources_project
    ON research_project_sources(project_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS research_source_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    source_ref TEXT NOT NULL REFERENCES research_sources(source_ref) ON DELETE CASCADE,
    source_version TEXT NOT NULL,
    relation TEXT NOT NULL,
    relation_status TEXT NOT NULL,
    quote TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'page',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_source_relations_project
    ON research_source_relations(project_id, source_ref, id DESC);
CREATE INDEX IF NOT EXISTS idx_research_source_relations_job
    ON research_source_relations(job_id, id DESC);
-- Procurement documents are kept independently from the collector正文.  A
-- refresh can therefore add parsed attachment facts without rewriting the
-- source record or its version history.
CREATE TABLE IF NOT EXISTS procurement_documents (
    id TEXT PRIMARY KEY,
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    fetched_at TEXT,
    mime_type TEXT,
    byte_size INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    page_texts_json TEXT NOT NULL DEFAULT '[]',
    items_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    is_current INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_procurement_documents_record ON procurement_documents(record_id);
CREATE INDEX IF NOT EXISTS idx_procurement_documents_status ON procurement_documents(status);
CREATE TABLE IF NOT EXISTS procurement_refreshes (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);
-- A reading is an immutable explanation of one versioned procurement
-- material package.  It is kept separate from judgments so regenerating a
-- market opinion cannot overwrite the source-grounded procurement facts.
CREATE TABLE IF NOT EXISTS procurement_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    material_version TEXT NOT NULL,
    status TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    demand_type TEXT NOT NULL DEFAULT 'unknown',
    stage TEXT NOT NULL DEFAULT '',
    buyer TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    category_json TEXT NOT NULL DEFAULT '[]',
    sections_json TEXT NOT NULL DEFAULT '[]',
    milestones_json TEXT NOT NULL DEFAULT '[]',
    coverage_json TEXT NOT NULL DEFAULT '[]',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    unresolved_json TEXT NOT NULL DEFAULT '[]',
    input_fingerprint TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_procurement_readings_project
    ON procurement_readings(project_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_procurement_readings_status
    ON procurement_readings(status, updated_at);
CREATE TABLE IF NOT EXISTS procurement_reading_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    force INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_procurement_reading_jobs_status
    ON procurement_reading_jobs(status, id);
CREATE TABLE IF NOT EXISTS procurement_attachment_fetches (
    record_id INTEGER PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
    record_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
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
    judgment_columns = {row[1] for row in db.execute("PRAGMA table_info(judgments)")}
    if "analysis_meta_json" not in judgment_columns:
        # A missing value denotes the original enterprise-fit semantics.
        db.execute("ALTER TABLE judgments ADD COLUMN analysis_meta_json TEXT")
    if "agent_run_id" not in judgment_columns:
        db.execute("ALTER TABLE judgments ADD COLUMN agent_run_id INTEGER")
    if "research_dependencies_json" not in judgment_columns:
        db.execute("ALTER TABLE judgments ADD COLUMN research_dependencies_json TEXT NOT NULL DEFAULT '[]'")
    if "research_policy_version" not in judgment_columns:
        db.execute("ALTER TABLE judgments ADD COLUMN research_policy_version TEXT")
    if "report_json" not in judgment_columns:
        # Older judgments deliberately remain legacy records.  A NULL value
        # means the judgment was saved before the flexible report protocol.
        db.execute("ALTER TABLE judgments ADD COLUMN report_json TEXT")
    agent_run_columns = {row[1] for row in db.execute("PRAGMA table_info(agent_runs)")}
    if "investigation_json" not in agent_run_columns:
        db.execute("ALTER TABLE agent_runs ADD COLUMN investigation_json TEXT NOT NULL DEFAULT '{}'")
    db.execute("CREATE INDEX IF NOT EXISTS idx_judgments_agent_run ON judgments(agent_run_id)")
    document_columns = {row[1] for row in db.execute("PRAGMA table_info(procurement_documents)")}
    if "is_current" not in document_columns:
        # Existing document rows predate version-aware refreshes. Treat them
        # as current once so the first refresh can supersede them safely.
        db.execute("ALTER TABLE procurement_documents ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1")
    reading_columns = {row[1] for row in db.execute("PRAGMA table_info(procurement_readings)")}
    if reading_columns and "revision" not in reading_columns:
        db.execute("ALTER TABLE procurement_readings ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    research_source_columns = {row[1] for row in db.execute("PRAGMA table_info(research_sources)")}
    if research_source_columns and "published_at" not in research_source_columns:
        db.execute("ALTER TABLE research_sources ADD COLUMN published_at TEXT")
    db.commit()
    return db


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
