$ErrorActionPreference = 'Stop'
$screeningRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$screeningServiceFile = Join-Path $screeningRoot 'data\service.json'
if (-not (Test-Path -LiteralPath $screeningServiceFile)) { Write-Host '没有本平台的运行记录。'; exit 0 }
$screeningServiceInfo = Get-Content -LiteralPath $screeningServiceFile -Raw -Encoding UTF8 | ConvertFrom-Json
$screeningProcess = Get-Process -Id $screeningServiceInfo.pid -ErrorAction SilentlyContinue
if (-not $screeningProcess) { Write-Host '平台已停止。'; exit 0 }
$screeningExpectedPython = Join-Path $screeningRoot '.venv\Scripts\python.exe'
$screeningCommand = Get-CimInstance Win32_Process -Filter "ProcessId = $($screeningServiceInfo.pid)"
$screeningTimeMatches = [Math]::Abs(($screeningProcess.StartTime.ToUniversalTime() - ([datetime]$screeningServiceInfo.started_at).ToUniversalTime()).TotalSeconds) -lt 3
if ($screeningProcess.Path -ne $screeningExpectedPython -or -not $screeningTimeMatches -or $screeningCommand.CommandLine -notmatch 'uvicorn.*backend\.main:app') { throw '进程身份与本平台启动记录不匹配，已取消停止操作。' }
$screeningChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($screeningProcess.Id)"
$screeningOwnedPythonIds = @($screeningProcess.Id)
foreach ($screeningChild in $screeningChildren) {
    if ($screeningChild.Name -eq 'python.exe' -and $screeningChild.CommandLine -match 'uvicorn.*backend\.main:app' -and $screeningChild.CommandLine -match "--port\s+$($screeningServiceInfo.port)(?:\s|$)") {
        $screeningOwnedPythonIds += $screeningChild.ProcessId
    }
}
# Only stop SDK runners that are direct children of the verified platform process.
$screeningRunnerPath = [regex]::Escape((Join-Path $screeningRoot 'agent-runtime\dist\runner.js'))
foreach ($screeningOwnerId in $screeningOwnedPythonIds) {
    $screeningAgentChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $screeningOwnerId"
    foreach ($screeningAgentChild in $screeningAgentChildren) {
        if ($screeningAgentChild.Name -eq 'node.exe' -and $screeningAgentChild.CommandLine -match $screeningRunnerPath) {
            Stop-Process -Id $screeningAgentChild.ProcessId -ErrorAction SilentlyContinue
        }
    }
}
foreach ($screeningChild in $screeningChildren) {
    if ($screeningChild.Name -eq 'python.exe' -and $screeningChild.CommandLine -match 'uvicorn.*backend\.main:app' -and $screeningChild.CommandLine -match "--port\s+$($screeningServiceInfo.port)(?:\s|$)") {
        Stop-Process -Id $screeningChild.ProcessId -ErrorAction SilentlyContinue
    }
}
Stop-Process -Id $screeningProcess.Id -ErrorAction SilentlyContinue
Write-Host '平台已停止，数据和决定均已保留。'
