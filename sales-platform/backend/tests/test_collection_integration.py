from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.collection.manager import (
    CollectionInvalid,
    CollectionManager,
    CollectionUnavailable,
    _CompletedProcess,
)
from backend.routes.collection import router


def _manager(tmp_path: Path, importer=None) -> CollectionManager:
    root = tmp_path / "collector"
    root.mkdir()
    data = root / "data"
    export = data / "export"
    export.mkdir(parents=True)
    config = root / "sources.json"
    config.write_text(json.dumps({"sources": []}), encoding="utf-8")
    source = export / "records.jsonl"
    source.write_text("", encoding="utf-8")
    screening = SimpleNamespace(source_path=source, import_file=importer or (lambda: {"total": 0}))
    return CollectionManager(
        screening,
        root=root,
        config=config,
        data=data,
        python_executable=sys.executable,
        timeout_seconds=0.2,
        import_poll_interval=0.01,
    )


def _status(*, running=False, source_id="source-a"):
    return {
        "available": True,
        "message": "ok",
        "running": running,
        "sources": [{
            "id": source_id,
            "name": "Source A",
            "category": "news",
            "enabled": True,
            "access_status": "public",
            "access_note": "",
            "entry_url": "https://example.test",
            "last_run": None,
        }],
        "runs": [],
        "schedule": {
            "available": True,
            "registered": True,
            "enabled": True,
            "at": "07:30",
            "state": "ready",
            "message": "ok",
        },
    }


def _app(manager: CollectionManager) -> FastAPI:
    app = FastAPI()
    app.state.collection = manager
    app.include_router(router)
    return app


def test_missing_collector_module_is_readonly_unavailable(tmp_path):
    manager = _manager(tmp_path)
    status = asyncio.run(manager.status())
    assert status["available"] is False
    assert status["sources"] == []
    assert status["task"] is None


def test_schedule_cannot_silently_target_default_paths_for_custom_data(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    manager.data = tmp_path / "different-data"
    called = []

    async def control(request):
        called.append(request)
        return {"message": "saved"}

    monkeypatch.setattr(manager, "_control", control)
    with pytest.raises(CollectionInvalid, match="默认来源"):
        asyncio.run(manager.set_schedule(True, "07:30"))
    assert called == []


def test_status_route_has_frozen_top_level_and_nested_shapes(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    state = _status()
    state["runs"] = [{
        "id": 7,
        "started_at": "2026-10-16T07:30:00+00:00",
        "finished_at": "2026-10-16T07:31:00+00:00",
        "status": "succeeded",
        "stats": {"sources_ok": 1},
    }]

    async def control(request):
        return state

    monkeypatch.setattr(manager, "_control", control)
    response = TestClient(_app(manager)).get("/api/collection")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"available", "import_connected", "message", "running", "sources", "runs", "schedule", "task"}
    assert payload["import_connected"] is True
    assert set(payload["sources"][0]) == {
        "id", "name", "category", "enabled", "access_status", "access_note", "entry_url",
        "last_success_at", "last_failure_at", "last_error", "last_run",
    }
    assert set(payload["runs"][0]) == {"id", "started_at", "finished_at", "status", "stats"}
    assert set(payload["schedule"]) == {"available", "registered", "enabled", "at", "state", "message"}


def test_busy_collector_returns_409_and_does_not_start_run(tmp_path, monkeypatch):
    manager = _manager(tmp_path)

    async def control(request):
        assert request == {"action": "status"}
        return _status(running=True)

    monkeypatch.setattr(manager, "_control", control)
    response = TestClient(_app(manager)).post("/api/collection/run", json={"source_id": "source-a"})
    assert response.status_code == 409
    assert manager._task is None


def test_single_source_is_validated_against_live_source_list(tmp_path, monkeypatch):
    manager = _manager(tmp_path)

    async def control(request):
        return _status()

    monkeypatch.setattr(manager, "_control", control)
    response = TestClient(_app(manager)).post("/api/collection/run", json={"source_id": "missing"})
    assert response.status_code == 400
    assert "未找到采集来源" in response.json()["detail"]


def test_batch_run_requires_at_least_one_enabled_source(tmp_path, monkeypatch):
    manager = _manager(tmp_path)

    async def control(request):
        state = _status()
        state["sources"][0]["enabled"] = False
        return state

    monkeypatch.setattr(manager, "_control", control)
    response = TestClient(_app(manager)).post("/api/collection/run", json={})
    assert response.status_code == 400
    assert "先启用来源" in response.json()["detail"]


def test_exit_two_imports_and_retains_partial_task(tmp_path, monkeypatch):
    imported = []

    async def importer():
        imported.append(True)
        return {"inserted": 3, "total": 3}

    manager = _manager(tmp_path, importer)

    async def control(request):
        return _status()

    async def execute(argv, payload=b"", *, timeout):
        assert argv[-2:] == ["--source", "source-a"] if "--source" in argv else True
        source = manager.screening.source_path
        published = source.with_suffix(".next")
        published.write_text('{"id":"one"}\n', encoding="utf-8")
        os.replace(published, source)
        return _CompletedProcess(2, '{"run_id": 1, "stats": {"source_failed": 1}}', "partial")

    monkeypatch.setattr(manager, "_control", control)
    monkeypatch.setattr(manager, "_execute_process", execute)

    async def scenario():
        task = await manager.start("source-a")
        for _ in range(20):
            if manager._task and manager._task.status != "running":
                break
            await asyncio.sleep(0)
        return task

    initial = asyncio.run(scenario())
    assert initial["status"] == "running"
    assert imported == [True]
    assert manager._task is not None
    assert manager._task.status == "partial"
    assert manager._task.import_result == {"inserted": 3, "total": 3}


def test_non_partial_failure_does_not_import(tmp_path, monkeypatch):
    imported = []

    async def importer():
        imported.append(True)

    manager = _manager(tmp_path, importer)

    async def control(request):
        return _status()

    async def execute(argv, payload=b"", *, timeout):
        return _CompletedProcess(1, "", "network failure")

    monkeypatch.setattr(manager, "_control", control)
    monkeypatch.setattr(manager, "_execute_process", execute)

    async def scenario():
        await manager.start()
        for _ in range(20):
            if manager._task and manager._task.status != "running":
                return
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert imported == []
    assert manager._task is not None
    assert manager._task.status == "failed"


def test_child_environment_removes_openai_secret_and_disables_scheduler(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-leak")
    monkeypatch.setenv("COLLECTOR_SCHEDULER_DISABLED", "1")
    captured = {}

    class Process:
        returncode = 0

        async def communicate(self, payload=b""):
            return b'{"available": true, "sources": [], "runs": [], "schedule": {}}', b""

    async def spawn(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = asyncio.run(manager._control({"action": "status"}))
    assert result["available"] is True
    assert "do-not-leak" not in captured["kwargs"]["env"].values()
    assert captured["kwargs"]["env"]["COLLECTOR_SCHEDULER_DISABLED"] == "1"
    assert captured["argv"][1:3] == ("-m", "collector.control")


def test_large_status_json_is_parsed_before_task_log_truncation(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    source = {
        "id": "source-a",
        "name": "Source A",
        "category": "news",
        "enabled": True,
        "access_status": "public",
        "access_note": "x" * 9_000,
        "entry_url": "https://example.test",
        "last_run": None,
    }
    output = json.dumps({
        "available": True,
        "message": "ok",
        "running": False,
        "sources": [source],
        "runs": [],
        "schedule": {},
    }, ensure_ascii=False)
    assert len(output) > 8_000

    class Process:
        returncode = 0

        async def communicate(self, payload=b""):
            return output.encode("utf-8"), b""

    async def spawn(*argv, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    status = asyncio.run(manager.status())
    assert len(status["sources"][0]["access_note"]) == 9_000


def test_scheduler_disable_flag_is_only_forwarded_when_explicit(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.delenv("COLLECTOR_SCHEDULER_DISABLED", raising=False)
    env = manager._child_env()
    assert "COLLECTOR_SCHEDULER_DISABLED" not in env


def test_timeout_and_close_terminate_process(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    terminated = []
    forever = asyncio.Event()

    class HangingProcess:
        returncode = None
        pid = 12345

        async def communicate(self, payload=b""):
            await forever.wait()

    process = HangingProcess()

    async def spawn(*argv, **kwargs):
        return process

    async def terminate(item):
        terminated.append(item)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(manager, "_terminate_process", terminate)
    with pytest.raises(CollectionUnavailable, match="超时"):
        asyncio.run(manager._execute_process([sys.executable, "-m", "collector"], timeout=0.01))
    assert terminated == [process]

    manager._processes.add(process)
    asyncio.run(manager.aclose())
    assert manager._closed is True
    assert terminated[-1] is process


def test_import_path_mismatch_is_rejected_before_run(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    manager.screening.source_path = manager.data / "other.jsonl"

    async def control(request):
        raise AssertionError("path validation must happen before control status")

    monkeypatch.setattr(manager, "_control", control)
    with pytest.raises(CollectionInvalid, match="采集结果尚未接入"):
        asyncio.run(manager.start())


def test_saved_development_import_is_visible_before_collecting_and_recovers_after_settings_update(tmp_path, monkeypatch):
    from backend.application.service import ScreeningService

    manager = _manager(tmp_path)
    database = tmp_path / "screening.sqlite3"
    service = ScreeningService(db_path=database, source_path=manager.data / "export/records.jsonl")
    service.update_settings({"import": {"path": str(manager.data / "export/development-five.jsonl"), "auto_import": False}})
    service.close()
    monkeypatch.delenv("SCREENING_IMPORT_PATH", raising=False)
    monkeypatch.delenv("SCREENING_ARCHIVE_ROOT", raising=False)
    service = ScreeningService(db_path=database)
    manager.screening = service

    async def control(request):
        return _status()

    monkeypatch.setattr(manager, "_control", control)
    try:
        client = TestClient(_app(manager))
        before = client.get("/api/collection").json()
        assert before["available"] is True
        assert before["import_connected"] is False
        service.update_settings({"import": {"path": str(manager.data / "export/records.jsonl")}})
        after = client.get("/api/collection").json()
        assert after["import_connected"] is True
        assert service.get_settings()["import"]["auto_import"] is False
        assert manager._task is None
    finally:
        service.close()
