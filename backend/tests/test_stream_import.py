import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.collection.manager import CollectionManager, CollectionUnavailable, _CompletedProcess


def _status():
    return {
        "available": True,
        "running": False,
        "sources": [{"id": "source-a", "enabled": True}],
        "runs": [],
        "schedule": {},
    }


def _publish(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.next")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _manager(tmp_path: Path, importer, *, timeout: float = 1.0):
    root = tmp_path / "collector"
    data = root / "data"
    export = data / "export"
    export.mkdir(parents=True)
    config = root / "sources.json"
    config.write_text("{}", encoding="utf-8")
    source = export / "records.jsonl"
    source.write_text("", encoding="utf-8")
    screening = SimpleNamespace(source_path=source, import_file=importer)
    manager = CollectionManager(
        screening,
        root=root,
        data=data,
        config=config,
        timeout_seconds=timeout,
        import_poll_interval=0.005,
    )

    async def control(_request):
        return _status()

    manager._control = control
    return manager, source


async def _wait_finished(manager: CollectionManager) -> None:
    assert manager._task_handle is not None
    await asyncio.wait_for(asyncio.shield(manager._task_handle), timeout=2)


def test_imports_each_atomic_snapshot_while_process_is_running(tmp_path):
    async def scenario():
        calls = []
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        import_gate = asyncio.Event()
        process_gate = asyncio.Event()

        async def importer():
            calls.append(source.read_text(encoding="utf-8"))
            if len(calls) == 1:
                first_started.set()
                await import_gate.wait()
            else:
                second_started.set()
            return {
                "inserted": 1,
                "updated": 1,
                "unchanged": len(calls),
                "total": len(calls),
            }

        manager, source = _manager(tmp_path, importer)

        async def execute(*_args, **_kwargs):
            _publish(source, "first\n")
            await first_started.wait()
            assert not process_gate.is_set()
            _publish(source, "second\n")
            await process_gate.wait()
            return _CompletedProcess(0, "", "")

        manager._execute_process = execute
        try:
            await manager.start("source-a")
            await asyncio.wait_for(first_started.wait(), timeout=1)
            assert calls == ["first\n"]
            assert not process_gate.is_set()

            import_gate.set()
            await asyncio.wait_for(second_started.wait(), timeout=1)
            process_gate.set()
            await _wait_finished(manager)

            assert calls == ["first\n", "second\n"]
            assert manager._task is not None
            assert manager._task.status == "succeeded"
            assert manager._task.import_result == {
                "inserted": 2,
                "updated": 2,
                "unchanged": 2,
                "total": 2,
                "batches": 2,
            }
        finally:
            await manager.aclose()

    asyncio.run(scenario())


def test_empty_startup_placeholder_waits_for_first_record(tmp_path):
    async def scenario():
        calls = []

        async def importer():
            calls.append(source.read_text(encoding="utf-8"))
            return {"inserted": 1, "updated": 0, "unchanged": 0, "total": 1}

        manager, source = _manager(tmp_path, importer)

        async def execute(*_args, **_kwargs):
            _publish(source, "")
            await asyncio.sleep(0.04)
            assert calls == []
            _publish(source, "first\n")
            return _CompletedProcess(0, "", "")

        manager._execute_process = execute
        try:
            await manager.start()
            await _wait_finished(manager)
            assert calls == ["first\n"]
            assert manager._task is not None
            assert manager._task.status == "succeeded"
        finally:
            await manager.aclose()

    asyncio.run(scenario())


def test_failed_snapshot_retries_without_busy_loop_and_clears_error(tmp_path):
    async def scenario():
        calls = []
        retried = asyncio.Event()

        async def importer():
            calls.append(source.read_text(encoding="utf-8"))
            if len(calls) == 1:
                raise RuntimeError("temporary import failure")
            retried.set()
            return {"inserted": 2, "updated": 1, "unchanged": 0, "total": 2}

        manager, source = _manager(tmp_path, importer)

        async def execute(*_args, **_kwargs):
            _publish(source, "first\n")
            await retried.wait()
            return _CompletedProcess(0, "", "")

        manager._execute_process = execute
        try:
            await manager.start("source-a")
            await _wait_finished(manager)
            assert calls == ["first\n", "first\n"]
            assert manager._task is not None
            assert manager._task.status == "succeeded"
            assert manager._task.import_result == {
                "inserted": 2,
                "updated": 1,
                "unchanged": 0,
                "total": 2,
            }
        finally:
            await manager.aclose()

    asyncio.run(scenario())


def test_failed_later_snapshot_retains_cumulative_success_counts(tmp_path):
    async def scenario():
        calls = []
        first_done = asyncio.Event()
        retry_done = asyncio.Event()

        async def importer():
            calls.append(source.read_text(encoding="utf-8"))
            if len(calls) == 1:
                first_done.set()
                return {"inserted": 1, "updated": 1, "unchanged": 0, "total": 1}
            if len(calls) == 2:
                raise RuntimeError("temporary second import failure")
            retry_done.set()
            return {"inserted": 2, "updated": 3, "unchanged": 1, "total": 2}

        manager, source = _manager(tmp_path, importer)

        async def execute(*_args, **_kwargs):
            _publish(source, "first\n")
            await first_done.wait()
            _publish(source, "second\n")
            await retry_done.wait()
            return _CompletedProcess(0, "", "")

        manager._execute_process = execute
        try:
            await manager.start("source-a")
            await _wait_finished(manager)
            assert calls == ["first\n", "second\n", "second\n"]
            assert manager._task is not None
            assert manager._task.status == "succeeded"
            assert manager._task.import_result == {
                "inserted": 3,
                "updated": 4,
                "unchanged": 1,
                "total": 2,
                "batches": 2,
            }
        finally:
            await manager.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("returncode", [1, 2])
def test_process_failure_keeps_successful_import_result(tmp_path, returncode):
    async def scenario():
        calls = []
        imported = asyncio.Event()

        async def importer():
            calls.append(source.read_text(encoding="utf-8"))
            imported.set()
            return {"inserted": 1, "updated": 0, "unchanged": 0, "total": 1}

        manager, source = _manager(tmp_path, importer)

        async def execute(*_args, **_kwargs):
            _publish(source, "first\n")
            await imported.wait()
            return _CompletedProcess(returncode, "", "collector stopped")

        manager._execute_process = execute
        try:
            await manager.start("source-a")
            await _wait_finished(manager)
            assert calls == ["first\n"]
            assert manager._task is not None
            assert manager._task.import_result == {
                "inserted": 1,
                "updated": 0,
                "unchanged": 0,
                "total": 1,
            }
            assert manager._task.status == ("failed" if returncode == 1 else "partial")
        finally:
            await manager.aclose()

    asyncio.run(scenario())


def test_process_timeout_keeps_successful_import_result(tmp_path):
    async def scenario():
        imported = asyncio.Event()

        async def importer():
            imported.set()
            return {"inserted": 1, "updated": 0, "unchanged": 0, "total": 1}

        manager, source = _manager(tmp_path, importer)

        async def execute(*_args, **_kwargs):
            _publish(source, "first\n")
            await imported.wait()
            raise CollectionUnavailable("采集命令超时")

        manager._execute_process = execute
        try:
            await manager.start("source-a")
            await _wait_finished(manager)
            assert manager._task is not None
            assert manager._task.status == "failed"
            assert "超时" in (manager._task.error or "")
            assert manager._task.import_result == {
                "inserted": 1,
                "updated": 0,
                "unchanged": 0,
                "total": 1,
            }
        finally:
            await manager.aclose()

    asyncio.run(scenario())
