param(
    [string]$TaskName = '销售平台信息采集',
    [string]$At = '07:30',
    [string]$PythonPath = $env:COLLECTOR_PYTHON
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $project 'run_daily.ps1'
$python = $PythonPath
if ([string]::IsNullOrWhiteSpace($python)) {
    $pythonPathFile = Join-Path $project 'data\python_path.txt'
    if (Test-Path -LiteralPath $pythonPathFile) {
        $python = (Get-Content -LiteralPath $pythonPathFile -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($python)) {
    # A scheduled task must use the interpreter that owns this checkout.  Do
    # not resolve an arbitrary `python` from PATH; callers can pass the exact
    # current sys.executable as -PythonPath (or COLLECTOR_PYTHON).
    $candidates = @(
        (Join-Path $project '.venv\Scripts\python.exe'),
        (Join-Path $project 'venv\Scripts\python.exe')
    )
    $python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($python) -or -not (Test-Path -LiteralPath $python)) {
    throw 'PythonPath is required and must point to the interpreter used by this collector'
}
$dataDir = Join-Path $project 'data'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Set-Content -LiteralPath (Join-Path $dataDir 'python_path.txt') -Value $python -Encoding utf8 -NoNewline
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description '每日采集公告和矿业新闻，保存证据并生成日报' -Force | Out-Null
Write-Output "Registered $TaskName at $At (computer local time)."
