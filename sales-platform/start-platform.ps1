param([int]$Port = 8766, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$screeningRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$screeningPython = Join-Path $screeningRoot '.venv\Scripts\python.exe'
$screeningData = Join-Path $screeningRoot 'data'
$screeningUrl = "http://127.0.0.1:$Port"
if (-not (Test-Path -LiteralPath $screeningPython)) { throw '请先运行 install-dependencies.ps1。' }
if (-not (Test-Path -LiteralPath (Join-Path $screeningRoot 'frontend\dist\index.html'))) { throw '前端尚未构建，请先运行 install-dependencies.ps1。' }
if (-not (Test-Path -LiteralPath (Join-Path $screeningRoot 'agent-runtime\dist\runner.js'))) { throw 'Pi SDK分析组件尚未构建，请先运行 install-dependencies.ps1。' }
$screeningBundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
if ($env:SCREENING_NODE_PATH -and (Test-Path -LiteralPath $env:SCREENING_NODE_PATH)) { $screeningNode = $env:SCREENING_NODE_PATH }
elseif (Test-Path -LiteralPath $screeningBundledNode) { $screeningNode = $screeningBundledNode }
else { $screeningNode = (Get-Command node -ErrorAction Stop).Source }
$screeningNodeVersion = [version]((& $screeningNode -p 'process.versions.node').Trim())
if ($screeningNodeVersion -lt [version]'22.19.0') { throw 'Pi SDK需要Node.js 22.19或更新版本。' }
$env:SCREENING_NODE_PATH = $screeningNode
if ($Port -lt 1024 -or $Port -gt 65535) { throw '端口应在1024至65535之间。' }
$screeningAlreadyRunning = $false
try {
    $screeningHealth = Invoke-RestMethod -Uri "$screeningUrl/api/health" -TimeoutSec 2
    if ($screeningHealth.app -eq 'sales-screening') { $screeningAlreadyRunning = $true }
    else { throw '该端口已由其他服务使用。请指定其他 -Port。' }
} catch {
    if ($_.Exception.Message -like '*该端口已由其他服务使用*') { throw }
}
if (-not $screeningAlreadyRunning) {
    # A busy existing service must not be mistaken for an unused port.
    $screeningListeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($screeningListeners) { throw '该端口已有服务监听，但健康检查暂未响应。请稍后再试，或先运行 stop-platform.cmd；运行记录已保留。' }
    New-Item -ItemType Directory -Path $screeningData -Force | Out-Null
    $screeningService = Start-Process -FilePath $screeningPython -ArgumentList @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $screeningRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $screeningData 'service.stdout.log') -RedirectStandardError (Join-Path $screeningData 'service.stderr.log') -PassThru
    $screeningServiceInfo = @{pid=$screeningService.Id; port=$Port; python=$screeningPython; started_at=$screeningService.StartTime.ToUniversalTime().ToString('o')}
    $screeningServiceInfo | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $screeningData 'service.json') -Encoding UTF8
    $screeningReady = $false
    for ($screeningAttempt = 0; $screeningAttempt -lt 30; $screeningAttempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $screeningHealth = Invoke-RestMethod -Uri "$screeningUrl/api/health" -TimeoutSec 2
            if ($screeningHealth.app -eq 'sales-screening') { $screeningReady = $true; break }
        } catch {}
        if ($screeningService.HasExited) { break }
    }
    if (-not $screeningReady) { throw "服务未成功启动，请查看 $screeningData\service.stderr.log。" }
}
Write-Host "平台已运行：$screeningUrl"
if ($OpenBrowser) { Start-Process $screeningUrl -WindowStyle Hidden }
