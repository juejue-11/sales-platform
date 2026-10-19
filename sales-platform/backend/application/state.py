"""Process resources shared by application modules; domain data stays in SQLite."""
from __future__ import annotations

from typing import Any

import asyncio
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class ApplicationState:
    db: sqlite3.Connection
    db_path: Path
    source_path: Path
    archive_root: Path
    provider: Any
    injected_provider: bool
    research_sources_path: Path | None = None
    research_client: httpx.AsyncClient | None = None
    lock: Any = field(default_factory=threading.RLock)
    last_import_signature: tuple[str, int | None, int | None] | None = None
    active_jobs: set[int] = field(default_factory=set)
    run_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    closed: bool = False
