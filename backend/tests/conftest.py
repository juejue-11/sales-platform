"""Keep the module-level FastAPI app away from the user's local database."""
from __future__ import annotations

import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path


# Some tests import backend.main to use create_app. Its module-level app is
# initialized during collection, before ordinary pytest fixtures run.
_default_app_directory = TemporaryDirectory(prefix="screening-test-default-app-")
_default_app_root = Path(_default_app_directory.name)
os.environ.setdefault("SCREENING_DB_PATH", str(_default_app_root / "default.sqlite3"))
os.environ.setdefault("SCREENING_IMPORT_PATH", str(_default_app_root / "missing.jsonl"))


def pytest_sessionfinish(session, exitstatus):
    module = sys.modules.get("backend.main")
    if module is not None:
        module.app.state.service.close()
    _default_app_directory.cleanup()
