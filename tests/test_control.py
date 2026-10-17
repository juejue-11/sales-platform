import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collector import control, scheduler
from collector.db import connect, sync_sources
from collector.locking import is_running, locked


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "sources.json"
        self.data = self.root / "data"
        self.config.write_text(json.dumps({
            "unknown_root": {"keep": True},
            "sources": [{
                "id": "fixture", "name": "Fixture", "category": "news",
                "entry_url": "https://example.invalid", "adapter": "manual",
                "enabled": True, "custom": {"keep": "yes"},
            }],
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_status_does_not_create_database(self):
        with patch.dict(os.environ, {"COLLECTOR_SCHEDULER_DISABLED": "1"}):
            result = control.handle({"action": "status"}, config=self.config, data=self.data)
        self.assertTrue(result["available"])
        self.assertFalse(result["running"])
        self.assertFalse((self.data / "collector.sqlite3").exists())
        self.assertFalse(result["schedule"]["available"])

    def test_source_update_is_atomic_and_preserves_unknown_keys(self):
        control.handle({"action": "source", "source_id": "fixture", "enabled": False},
                       config=self.config, data=self.data)
        payload = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertTrue(payload["unknown_root"]["keep"])
        self.assertEqual(payload["sources"][0]["custom"], {"keep": "yes"})
        self.assertFalse(payload["sources"][0]["enabled"])
        original = self.config.read_bytes()
        with patch("collector.control.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                control.handle({"action": "source", "source_id": "fixture", "enabled": True},
                               config=self.config, data=self.data)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.config.parent.glob(f".{self.config.name}-*.tmp")), [])

    def test_source_request_type_and_id_validation(self):
        with self.assertRaises(control.InvalidRequest):
            control.handle({"action": "source", "source_id": "fixture", "enabled": 1},
                           config=self.config, data=self.data)
        with self.assertRaises(control.InvalidRequest):
            control.handle({"action": "source", "source_id": "unknown", "enabled": True},
                           config=self.config, data=self.data)

    def test_unknown_cli_source_rejects_before_database_creation(self):
        result = subprocess.run([
            sys.executable, "-m", "collector", "--config", str(self.config),
            "--data", str(self.data), "run", "--source", "unknown",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.data / "collector.sqlite3").exists())

    def test_legacy_run_contention_returns_three(self):
        with locked(self.data):
            result = subprocess.run([
                sys.executable, "-m", "collector", "--config", str(self.config),
                "--data", str(self.data), "run",
            ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)

    def test_partial_run_keeps_export_and_exit_two(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["sources"][0].update({"adapter": "manual", "enabled": True})
        self.config.write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run([
            sys.executable, "-m", "collector", "--config", str(self.config),
            "--data", str(self.data), "run",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["stats"]["source_failed"], 1)
        self.assertTrue((self.data / "export" / "records.jsonl").exists())

    def test_old_running_source_is_interrupted_when_newer_run_holds_lock(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["sources"].append({
            "id": "current", "name": "Current", "category": "news",
            "entry_url": "https://example.invalid/current", "adapter": "manual",
            "enabled": True,
        })
        self.config.write_text(json.dumps(config), encoding="utf-8")
        self.data.mkdir(parents=True)
        db = connect(self.data / "collector.sqlite3")
        try:
            sync_sources(db, config["sources"])
            db.execute("INSERT INTO runs(started_at,status) VALUES ('old','running')")
            db.execute("INSERT INTO source_runs(run_id,source_id,started_at,status) VALUES (1,'fixture','old','running')")
            db.execute("INSERT INTO runs(started_at,status) VALUES ('current','running')")
            db.execute("INSERT INTO source_runs(run_id,source_id,started_at,status) VALUES (2,'current','current','running')")
            db.commit()
        finally:
            db.close()
        with locked(self.data):
            with patch.dict(os.environ, {"COLLECTOR_SCHEDULER_DISABLED": "1"}):
                status = control.handle({"action": "status"}, config=self.config, data=self.data)
        by_id = {item["id"]: item for item in status["sources"]}
        self.assertEqual(by_id["fixture"]["last_run"]["status"], "interrupted")
        self.assertEqual(by_id["current"]["last_run"]["status"], "running")

    def test_lock_is_cross_process_and_releases(self):
        code = """from collector.locking import locked
import sys
try:
    with locked(sys.argv[1]):
        print('acquired')
except Exception as e:
    print(type(e).__name__)
    raise SystemExit(3)
"""
        with locked(self.data):
            child = subprocess.run([sys.executable, "-c", code, str(self.data)],
                                   capture_output=True, text=True)
            self.assertEqual(child.returncode, 3)
            self.assertIn("BusyError", child.stdout)
            self.assertTrue(is_running(self.data))
        child = subprocess.run([sys.executable, "-c", code, str(self.data)],
                               capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertFalse(is_running(self.data))

    @patch.object(scheduler, "available", return_value=True)
    @patch.object(scheduler, "_run_powershell")
    def test_scheduler_success_and_task_missing(self, run_powershell, _available):
        run_powershell.return_value = subprocess.CompletedProcess(
            [], 0, '{"state":"Ready","enabled":true,"at":"2026-10-16T07:30:00"}', ""
        )
        status = scheduler.get_status()
        self.assertEqual(status.as_dict()["at"], "07:30")
        run_powershell.return_value = subprocess.CompletedProcess([], 4, "", "TASK_NOT_FOUND")
        status = scheduler.get_status()
        self.assertFalse(status.registered)

    def test_schedule_disabled_is_unavailable(self):
        with patch.dict(os.environ, {"COLLECTOR_SCHEDULER_DISABLED": "1"}):
            self.assertFalse(scheduler.get_status().available)
            with self.assertRaises(scheduler.SchedulerError):
                scheduler.set_schedule(True, "07:30")

    @patch.object(scheduler, "available", return_value=True)
    @patch.object(scheduler, "_run_powershell")
    def test_scheduler_permission_failure_is_distinct_from_missing(self, run_powershell, _available):
        run_powershell.return_value = subprocess.CompletedProcess([], 5, "", "Access is denied")
        permission = scheduler.get_status()
        self.assertFalse(permission.available)
        self.assertIn("读取", permission.message)
        run_powershell.return_value = subprocess.CompletedProcess([], 4, "", "TASK_NOT_FOUND")
        missing = scheduler.get_status()
        self.assertTrue(missing.available)
        self.assertFalse(missing.registered)

    @patch.object(scheduler, "available", return_value=True)
    @patch.object(scheduler, "_run_powershell")
    def test_schedule_update_passes_validated_values_and_registers_first_enable(self, run_powershell, _available):
        run_powershell.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        self.assertIn("启用", scheduler.set_schedule(True, "08:15"))
        self.assertEqual(run_powershell.call_args.args[1], [scheduler.TASK_NAME, "08:15", "true"])
        self.assertIn("停用", scheduler.set_schedule(False, "08:15"))
        self.assertEqual(run_powershell.call_args.args[1], [scheduler.TASK_NAME, "08:15", "false"])
        run_powershell.reset_mock()
        run_powershell.return_value = subprocess.CompletedProcess([], 4, "", "TASK_NOT_FOUND")
        with patch.object(scheduler, "_register_task", return_value="已注册") as register:
            self.assertEqual(scheduler.set_schedule(True, "09:20"), "已注册")
        register.assert_called_once_with("09:20")

    @patch.object(scheduler, "subprocess")
    def test_register_uses_current_interpreter(self, subprocess_module):
        subprocess_module.CREATE_NO_WINDOW = 0
        subprocess_module.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        scheduler._register_task("07:30")
        argv = subprocess_module.run.call_args.args[0]
        self.assertIn("-PythonPath", argv)
        self.assertEqual(argv[argv.index("-PythonPath") + 1], sys.executable)


if __name__ == "__main__":
    unittest.main()
