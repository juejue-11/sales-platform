"""Protect persisted data and runtime discovery when source packages move."""
from pathlib import Path

from backend.application.service import ScreeningService
from backend.analysis.ai import _runtime_root
from backend.core import config
from backend.research.project_research import _DEFAULT_SOURCES_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_default_database_stays_in_project_data_directory(monkeypatch):
    monkeypatch.delenv("SCREENING_DB_PATH", raising=False)
    assert config.ROOT == PROJECT_ROOT
    assert config.DATA_DIR == PROJECT_ROOT / "data"
    assert config.database_path() == PROJECT_ROOT / "data" / "screening.sqlite3"


def test_agent_runtime_discovery_preserves_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("SCREENING_AGENT_RUNTIME", raising=False)
    assert _runtime_root() == PROJECT_ROOT / "agent-runtime"
    override = tmp_path / "custom-runtime"
    monkeypatch.setenv("SCREENING_AGENT_RUNTIME", str(override))
    assert _runtime_root() == override


def test_research_configuration_remains_available_at_existing_runtime_path():
    assert _DEFAULT_SOURCES_PATH == PROJECT_ROOT / "backend" / "research_sources.json"
    assert _DEFAULT_SOURCES_PATH.is_file()


def test_saved_import_path_survives_restart_and_allows_explicit_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv("SCREENING_IMPORT_PATH", raising=False)
    monkeypatch.delenv("SCREENING_ARCHIVE_ROOT", raising=False)
    db_path = tmp_path / "screening.sqlite3"
    sample_path = tmp_path / "collector" / "export" / "five.jsonl"
    service = ScreeningService(db_path=db_path)
    service.update_settings({"import": {"path": str(sample_path), "auto_import": False}})
    service.close()

    service = ScreeningService(db_path=db_path)
    try:
        assert service.source_path == sample_path
        assert service.archive_root == sample_path.parent.parent
        assert service.get_settings()["import"]["auto_import"] is False
    finally:
        service.close()

    override = tmp_path / "override" / "export" / "records.jsonl"
    monkeypatch.setenv("SCREENING_IMPORT_PATH", str(override))
    service = ScreeningService(db_path=db_path)
    try:
        assert service.source_path == override
    finally:
        service.close()

    explicit = tmp_path / "explicit.jsonl"
    explicit_archive = tmp_path / "archive"
    service = ScreeningService(db_path=db_path, source_path=explicit, archive_root=explicit_archive)
    try:
        assert service.source_path == explicit
        assert service.archive_root == explicit_archive
    finally:
        service.close()
