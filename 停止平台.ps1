$ErrorActionPreference = 'Stop'
$screeningRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$screeningServiceFile = Join-Path $screeningRoot 'data\service.json'
if (-not (Test-Path -LiteralPath $screeningServiceFile)) { Write-Host '没有本平台的运行记录。'; exit 0 }
$screeningServiceInfo = Get-Content -LiteralPath $screeningServiceFile -Raw -Encoding UTF8 | ConvertFrom-Json
$screeningProcess = Get-Process -Id $screeningServiceInfo.pid -ErrorAction SilentlyContinue
if (-not $screeningProcess) { Write-Host '平台已停止。'; exit 0 }
$screeningExpectedPython = Join-Path $screeningRoot '.venv\Scripts\python.exe'
$screeningCommand = Get-CimInstance Win32_Process -Filter "ProcessId = $($screeningServiceInfo.pid)"
$screeningTimeMatches = [Math]::Abs(($screeningProcess.StartTime.ToUniversalTime() - [datetime]$screeningServiceInfo.started_at).TotalSeconds) -lt 3
if ($screeningProcess.Path -ne $screeningExpectedPython -or -not $screeningTimeMatches -or $screeningCommand.CommandLine -notmatch 'uvicorn.*backend\.main:app') { throw '进程身份与本平台启动记录不匹配，已取消停止操作。' }
Stop-Process -Id $screeningProcess.Id
Write-Host '平台已停止，数据和决定均已保留。'
