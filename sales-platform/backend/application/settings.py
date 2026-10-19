"""Persisted settings, runtime readiness and configuration invalidation."""
from __future__ import annotations

from typing import Any, Callable

import copy
import os
from pathlib import Path
from ..analysis.ai import PiSDKProvider
from ..analysis.analysis_policy import ANALYSIS_POLICY_VERSION, DEFAULT_MARKET_GOAL, analysis_context, current_analysis_meta
from ..core.config import DEFAULT_SETTINGS, env_archive_root
from ..core.utils import json_text, json_value, now_iso
from .state import ApplicationState
from .errors import ServiceError


class ApplicationSettings:
    def __init__(self, *, state: ApplicationState, queue_pending: Callable[..., Any]) -> None:
        self.state = state
        self.queue_pending = queue_pending

    def initialize_defaults(self) -> None:
        with self.state.lock, self.state.db:
            for key in ("business", "ai", "import"):
                if self.state.db.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone() is None:
                    value = copy.deepcopy(DEFAULT_SETTINGS[key])
                    if key == "import":
                        value["path"] = str(self.state.source_path)
                    if key == "ai":
                        # A key from the process environment remains process-owned
                        # and is never copied into SQLite or API output.
                        value.pop("has_api_key", None)
                    self.state.db.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?)",
                                    (key, json_text(value), now_iso()))
            if self.state.db.execute("SELECT 1 FROM meta WHERE key='settings_version'").fetchone() is None:
                self.state.db.execute("INSERT INTO meta(key,value) VALUES ('settings_version','1')")
            # Preserve old enterprise-fit reports and human choices verbatim.
            # Policy upgrades invalidate reuse; they do not rewrite decisions
            # or initiate paid bulk model calls just by opening the database.
            for judgment in self.state.db.execute(
                    "SELECT id,project_id,analysis_meta_json FROM judgments WHERE stale=0").fetchall():
                if not current_analysis_meta(json_value(judgment["analysis_meta_json"], None)):
                    self.state.db.execute("UPDATE judgments SET stale=1 WHERE id=?", (judgment["id"],))
                    self.state.db.execute("UPDATE projects SET updated_at=? WHERE id=?",
                                    (now_iso(), judgment["project_id"]))
            self.state.db.execute("INSERT INTO meta(key,value) VALUES ('analysis_policy_version',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ANALYSIS_POLICY_VERSION,))

    def set_meta(self, key: str, value: Any) -> None:
        with self.state.lock, self.state.db:
            self.state.db.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(value) if value is not None else None))

    def get_meta(self, key: str) -> str | None:
        row = self.state.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def section(self, key: str) -> dict[str, Any]:
        row = self.state.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        value = json_value(row[0], {}) if row else {}
        return value if isinstance(value, dict) else {}

    def stored_api_key(self) -> str:
        value = self.section("ai").get("api_key")
        if isinstance(value, str) and value:
            return value
        return os.getenv("OPENAI_API_KEY", "")

    def ai_engine(self, ai: dict[str, Any] | None = None) -> str:
        # The environment switch is intentionally explicit.  It provides a
        # stable escape hatch for the legacy HTTP tests while the default is
        # always the embedded Pi SDK.
        configured = os.getenv("SCREENING_AI_ENGINE", "").strip().lower()
        if configured == "single_call":
            return "single_call"
        # A persisted/client supplied engine is deliberately ignored.  This
        # keeps the legacy path opt-in and makes Pi SDK the stable default even
        # when upgrading a database created by an older build.
        return "pi_sdk"

    def ai_config(self) -> dict[str, Any]:
        ai = self.section("ai")
        base_url = str(ai.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        model = str(ai.get("model") or os.getenv("OPENAI_MODEL", ""))
        enabled = bool(ai.get("enabled", True))
        api_key = self.stored_api_key()
        engine = self.ai_engine(ai)
        sdk_is_ready = PiSDKProvider.sdk_ready()
        provider_is_pi = isinstance(self.state.provider, PiSDKProvider) or callable(getattr(self.state.provider, "analyze", None))
        runtime_ready = (self.state.injected_provider or engine != "pi_sdk" or not provider_is_pi
                         or not isinstance(self.state.provider, PiSDKProvider) or sdk_is_ready)
        credentials_configured = enabled and bool(base_url and model and api_key)
        return {"base_url": base_url, "model": model, "enabled": enabled,
                "api_key": api_key, "engine": engine, "sdk_ready": sdk_is_ready,
                "credentials_configured": credentials_configured,
                "configured": credentials_configured and runtime_ready}

    def get_settings(self) -> dict[str, Any]:
        business = copy.deepcopy(self.section("business"))
        ai = copy.deepcopy(self.section("ai"))
        import_settings = copy.deepcopy(self.section("import"))
        for key, default in DEFAULT_SETTINGS["business"].items():
            business.setdefault(key, copy.deepcopy(default))
        for key, default in DEFAULT_SETTINGS["import"].items():
            import_settings.setdefault(key, copy.deepcopy(default))
        for key, default in DEFAULT_SETTINGS["ai"].items():
            ai.setdefault(key, copy.deepcopy(default))
        # Never expose the actual secret, including through arbitrary extra keys.
        ai.pop("api_key", None)
        ai["has_api_key"] = bool(self.stored_api_key())
        ai["enabled"] = bool(ai.get("enabled"))
        ai["engine"] = self.ai_engine(ai)
        ai["sdk_ready"] = PiSDKProvider.sdk_ready()
        version = int(self.get_meta("settings_version") or 1)
        return {"business": business, "ai": ai, "import": import_settings, "version": version,
                "analysis": {"goal": DEFAULT_MARKET_GOAL, "semantic_version": ANALYSIS_POLICY_VERSION,
                             "company_profile_present": analysis_context(business)["company_profile_present"]}}

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ServiceError("settings body must be an object")
        before = self.get_settings()
        before_ai_config = self.ai_config()
        changed_business = False
        changed_import_path: str | None = None
        changed_ai_runtime = False
        with self.state.lock, self.state.db:
            for section in ("business", "ai", "import"):
                incoming = payload.get(section)
                if not isinstance(incoming, dict):
                    continue
                current = self.section(section)
                previous = copy.deepcopy(current)
                if section == "ai":
                    if "api_key" in incoming:
                        key_value = incoming.get("api_key")
                        if isinstance(key_value, str) and key_value:
                            current["api_key"] = key_value
                    if incoming.get("clear_api_key") is True:
                        current.pop("api_key", None)
                    # Engine and runtime readiness are process-derived
                    # read-only values.  The legacy HTTP path is enabled only
                    # by SCREENING_AI_ENGINE=single_call, never by persisted
                    # client input.
                    incoming = {k: v for k, v in incoming.items()
                                if k not in {"api_key", "clear_api_key", "has_api_key", "engine", "sdk_ready"}}
                current.update(incoming)
                self.state.db.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                                (section, json_text(current), now_iso()))
                if section == "business" and current != previous:
                    changed_business = True
                if section == "ai" and any(current.get(key) != previous.get(key) for key in ("base_url", "model", "enabled", "engine")):
                    changed_ai_runtime = True
                if section == "import" and isinstance(current.get("path"), str) and current.get("path") != previous.get("path"):
                    changed_import_path = current["path"]
            version = int(self.get_meta("settings_version") or 1) + 1
            self.state.db.execute("INSERT INTO meta(key,value) VALUES ('settings_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (str(version),))
            if changed_business:
                self.state.db.execute("UPDATE judgments SET stale=1")
                self.state.db.execute("UPDATE projects SET updated_at=?", (now_iso(),))
            if changed_ai_runtime:
                # Endpoint/model/enabled changes alter the interpretation of
                # an AI result.  API key rotation alone does not.
                self.state.db.execute("UPDATE judgments SET stale=1")
                self.state.db.execute("UPDATE projects SET updated_at=?", (now_iso(),))
        if changed_import_path:
            self.state.source_path = Path(changed_import_path)
            self.state.archive_root = env_archive_root(self.state.source_path)
        after = self.get_settings()
        after_ai_config = self.ai_config()
        became_configured = not before_ai_config["configured"] and after_ai_config["configured"]
        if after_ai_config["configured"] and (changed_business or changed_ai_runtime or became_configured):
            self.queue_pending(schedule=False)
        return after
