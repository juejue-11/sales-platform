param([int]$Port = 8766, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$screeningRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$screeningPython = Join-Path $screeningRoot '.venv\Scripts\python.exe'
$screeningData = Join-Path $screeningRoot 'data'
$screeningUrl = "http://127.0.0.1:$Port"
if (-not (Test-Path -LiteralPath $screeningPython)) { throw '请先运行 安装依赖.ps1。' }
if (-not (Test-Path -LiteralPath (Join-Path $screeningRoot 'frontend\dist\index.html'))) { throw '前端尚未构建，请先运行 安装依赖.ps1。' }
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
