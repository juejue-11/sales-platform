from __future__ import annotations

import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_SOURCE_PATH = Path(r"C:/Users/cgr/Desktop/new/销售平台信息采集系统/data/export/records.jsonl")
DEFAULT_ARCHIVE_ROOT = Path(r"C:/Users/cgr/Desktop/new/销售平台信息采集系统/data")


DEFAULT_SETTINGS: dict[str, Any] = {
    "business": {
        "company_name": "",
        "scope_description": "",
        "target_keywords": [],
        "exclude_keywords": [],
        "supported_products": "",
        "capabilities": "",
        "solution_examples": "",
        "region_preferences": "",
        "customer_role": "",
        "equipment_categories": "",
        "mineral_types": "",
        "demand_focus": "",
    },
    "ai": {
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("OPENAI_MODEL", ""),
        "enabled": bool(os.getenv("OPENAI_API_KEY")) and os.getenv("OPENAI_ENABLED", "1") != "0",
        "has_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "engine": os.getenv("SCREENING_AI_ENGINE", "pi_sdk"),
    },
    "import": {
        "path": str(DEFAULT_SOURCE_PATH),
        "auto_import": True,
        "poll_seconds": 300,
    },
    "version": 1,
}


def env_source_path() -> Path:
    return Path(os.getenv("SCREENING_IMPORT_PATH", str(DEFAULT_SOURCE_PATH)))


def env_archive_root(source_path: Path) -> Path:
    configured = os.getenv("SCREENING_ARCHIVE_ROOT")
    if configured:
        return Path(configured)
    # The collector's archive directory is next to data/export. This backend only
    # reads it; it never writes into the collector tree.
    return source_path.parent.parent


def database_path() -> Path:
    return Path(os.getenv("SCREENING_DB_PATH", str(DATA_DIR / "screening.sqlite3")))

