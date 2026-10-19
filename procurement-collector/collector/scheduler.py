"""Small adapter for the existing Windows Task Scheduler task.

The collector intentionally does not run a second in-process scheduler.  This
module only reads or updates the already registered daily task.  PowerShell is
invoked with an argument list and a fixed script body, never through a shell
command assembled from request data.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


TASK_NAME = "销售平台信息采集"
POWERSHELL_TIMEOUT_SECONDS = 15
_TIME_RE = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")

# The task name and schedule time are passed through a per-process environment
# block. These fixed scripts do not interpolate request values into commands.
STATUS_SCRIPT = r"""
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$ErrorActionPreference = 'Stop'
$TaskName = $env:COLLECTOR_TASK_NAME
try {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
  $trigger = @($task.Triggers | Select-Object -First 1)
  $at = if ($trigger.Count -gt 0) { [string]$trigger[0].StartBoundary } else { $null }
  [pscustomobject]@{
    task_name = [string]$task.TaskName
    state = [string]$task.State
    enabled = ([string]$task.State -ne 'Disabled')
    at = $at
  } | ConvertTo-Json -Compress
} catch {
  $message = [string]$_.Exception.Message
  $notFound = ([string]$_.FullyQualifiedErrorId -match '(?i)(objectnotfound|tasknotfound)') -or
              ($message -match '(?i)(cannot find|not found|does not exist|objectnotfound)')
  if ($notFound) {
    [Console]::Error.WriteLine('TASK_NOT_FOUND: ' + $message)
    exit 4
  }
  [Console]::Error.WriteLine($message)
  exit 5
}
"""

UPDATE_SCRIPT = r"""
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$ErrorActionPreference = 'Stop'
$TaskName = $env:COLLECTOR_TASK_NAME
$At = $env:COLLECTOR_SCHEDULE_AT
$Enabled = [bool]::Parse($env:COLLECTOR_SCHEDULE_ENABLED)
try {
  $null = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
  $trigger = New-ScheduledTaskTrigger -Daily -At $At
  Set-ScheduledTask -TaskName $TaskName -Trigger $trigger -ErrorAction Stop | Out-Null
  if ($Enabled) {
    Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
  } else {
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
  }
  [pscustomobject]@{ok = $true; at = $At; enabled = $Enabled} | ConvertTo-Json -Compress
} catch {
  $message = [string]$_.Exception.Message
  $notFound = ([string]$_.FullyQualifiedErrorId -match '(?i)(objectnotfound|tasknotfound)') -or
              ($message -match '(?i)(cannot find|not found|does not exist|objectnotfound)')
  if ($notFound) {
    [Console]::Error.WriteLine('TASK_NOT_FOUND: ' + $message)
    exit 4
  }
  [Console]::Error.WriteLine($message)
  exit 5
}
"""


class SchedulerError(RuntimeError):
    """The Task Scheduler could not be queried or updated."""


class TaskNotFound(SchedulerError):
    """The fixed collector task has not been registered yet."""


@dataclass(frozen=True)
class ScheduleStatus:
    available: bool
    registered: bool
    enabled: bool
    at: Optional[str]
    state: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "registered": self.registered,
            "enabled": self.enabled,
            "at": self.at,
            "state": self.state,
            "message": self.message,
        }


def scheduler_disabled() -> bool:
    """Allow isolated deployments to explicitly disable task access."""

    return os.environ.get("COLLECTOR_SCHEDULER_DISABLED", "") == "1"


def available() -> bool:
    return os.name == "nt" and not scheduler_disabled()


def _powershell_executable() -> str:
    # On Windows this is resolved by CreateProcess; keeping a fixed executable
    # name avoids accepting a command from the request body.
    return "powershell.exe"


def _run_powershell(script: str, parameters: Sequence[str]) -> subprocess.CompletedProcess[str]:
    argv = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": POWERSHELL_TIMEOUT_SECONDS,
        "shell": False,
    }
    # CREATE_NO_WINDOW is defined on Windows.  Supplying 0 elsewhere keeps
    # the adapter straightforward to exercise on POSIX test workers.
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if no_window:
        kwargs["creationflags"] = no_window
    # ``-Command`` receives only this fixed script.  Validated values travel
    # through an environment block so PowerShell does not concatenate them
    # into the script text (which would make its ``param`` binding ambiguous).
    env = os.environ.copy()
    if parameters:
        env["COLLECTOR_TASK_NAME"] = parameters[0]
    if len(parameters) > 1:
        env["COLLECTOR_SCHEDULE_AT"] = parameters[1]
    if len(parameters) > 2:
        env["COLLECTOR_SCHEDULE_ENABLED"] = parameters[2]
    kwargs["env"] = env
    try:
        return subprocess.run(argv, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise SchedulerError("Task Scheduler query timed out") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchedulerError(f"Task Scheduler unavailable: {exc}") from exc


def _classify_result(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode == 0:
        return result.stdout or ""
    message = (result.stderr or result.stdout or "").strip()
    if result.returncode == 4 or re.search(
        r"(?i)(task_not_found|cannot find|not found|does not exist|objectnotfound)",
        message,
    ):
        raise TaskNotFound(message or "scheduled task not found")
    raise SchedulerError(message or f"PowerShell exited with {result.returncode}")


def _decode_status(raw: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SchedulerError("invalid Task Scheduler response") from exc
    if not isinstance(value, Mapping):
        raise SchedulerError("invalid Task Scheduler response")
    return value


def _normalise_at(value: Any) -> Optional[str]:
    if value is None:
        return None
    match = _TIME_RE.search(str(value))
    return f"{match.group(1)}:{match.group(2)}" if match else None


def _register_task(at: str) -> str:
    """Register the daily task for the first schedule enable operation."""

    script = Path(__file__).resolve().parent.parent / "register_daily_task.ps1"
    argv = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-TaskName",
        TASK_NAME,
        "-At",
        at,
        "-PythonPath",
        sys.executable,
    ]
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": POWERSHELL_TIMEOUT_SECONDS,
        "shell": False,
    }
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if no_window:
        kwargs["creationflags"] = no_window
    try:
        result = subprocess.run(argv, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise SchedulerError("注册 Windows 计划任务超时") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchedulerError(f"Windows 计划任务不可用：{exc}") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise SchedulerError(message or f"计划任务注册失败（退出码 {result.returncode}）")
    return f"已注册每日采集任务（{at}）"


def get_status() -> ScheduleStatus:
    if not available():
        reason = (
            "已通过 COLLECTOR_SCHEDULER_DISABLED 禁用计划任务"
            if scheduler_disabled()
            else "当前平台不可用 Windows 计划任务"
        )
        return ScheduleStatus(False, False, False, None, "unavailable", reason)
    try:
        raw = _classify_result(_run_powershell(STATUS_SCRIPT, [TASK_NAME]))
    except TaskNotFound as exc:
        return ScheduleStatus(True, False, False, None, "not_registered", f"计划任务尚未注册：{exc}")
    except SchedulerError as exc:
        return ScheduleStatus(False, False, False, None, "unavailable", f"读取计划任务失败：{exc}")
    payload = _decode_status(raw)
    state = str(payload.get("state") or "unknown")
    enabled_value = payload.get("enabled")
    if isinstance(enabled_value, str):
        enabled = enabled_value.strip().lower() in {"true", "1", "yes", "ready"}
    elif enabled_value is None:
        enabled = state.lower() not in {"disabled", "unknown"}
    else:
        enabled = bool(enabled_value)
    at = _normalise_at(payload.get("at") or payload.get("start_boundary"))
    return ScheduleStatus(True, True, enabled, at, state, "计划任务已注册")


def validate_at(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("at must be a 24-hour HH:MM value")
    return value


def set_schedule(enabled: Any, at: Any) -> str:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    at_text = validate_at(at)
    if not available():
        raise SchedulerError(
            "已通过 COLLECTOR_SCHEDULER_DISABLED 禁用计划任务"
            if scheduler_disabled()
            else "当前平台不可用 Windows 计划任务"
        )
    # Pass values as separate argv entries.  The script body is fixed and the
    # task name is the fixed collector task; no shell concatenation is used.
    try:
        _classify_result(_run_powershell(
            UPDATE_SCRIPT,
            [TASK_NAME, at_text, "true" if enabled else "false"],
        ))
    except TaskNotFound:
        if not enabled:
            raise
        # A first enable may register the task, while later operations always
        # update the existing task in place and retain its daily trigger.
        return _register_task(at_text)
    return f"计划任务已{'启用' if enabled else '停用'}，每日 {at_text} 执行"


__all__ = [
    "TASK_NAME",
    "ScheduleStatus",
    "SchedulerError",
    "TaskNotFound",
    "available",
    "get_status",
    "_register_task",
    "scheduler_disabled",
    "set_schedule",
    "validate_at",
]
