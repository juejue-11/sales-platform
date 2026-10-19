$ErrorActionPreference = 'Stop'
$screeningRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$screeningRuntime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$screeningPython = Join-Path $screeningRoot '.venv\Scripts\python.exe'
$screeningBundledPython = Join-Path $screeningRuntime 'python\python.exe'
if (-not (Test-Path -LiteralPath $screeningPython)) {
    if (Test-Path -LiteralPath $screeningBundledPython) { $screeningBasePython = $screeningBundledPython }
    else { $screeningBasePython = (Get-Command python -ErrorAction Stop).Source }
    & $screeningBasePython -m venv (Join-Path $screeningRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建Python环境失败。' }
}
& $screeningPython -m pip install -r (Join-Path $screeningRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '后端依赖安装失败。' }
# Keep the collector independently runnable while preparing it from one entry.
$collectorRoot = if ($env:SCREENING_COLLECTOR_ROOT) { [System.IO.Path]::GetFullPath($env:SCREENING_COLLECTOR_ROOT) } else { Join-Path (Split-Path -Parent $screeningRoot) 'procurement-collector' }
$collectorRequirements = Join-Path $collectorRoot 'requirements.txt'
if (Test-Path -LiteralPath $collectorRequirements) {
    $collectorPythonFile = Join-Path $collectorRoot 'data\python_path.txt'
    if ($env:SCREENING_COLLECTOR_PYTHON) { $collectorPython = $env:SCREENING_COLLECTOR_PYTHON }
    elseif (Test-Path -LiteralPath $collectorPythonFile) { $collectorPython = (Get-Content -LiteralPath $collectorPythonFile -Raw -Encoding UTF8).Trim() }
    else {
        $collectorPython = Join-Path $collectorRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $collectorPython)) {
            & $screeningPython -m venv (Join-Path $collectorRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw '创建采集模块Python环境失败。' }
        }
    }
    if (-not (Test-Path -LiteralPath $collectorPython)) { throw '采集模块Python路径无效，请检查其 data\python_path.txt 或 SCREENING_COLLECTOR_PYTHON。' }
    & $collectorPython -m pip install -r $collectorRequirements
    if ($LASTEXITCODE -ne 0) { throw '采集模块依赖安装失败。' }
    if (-not (Test-Path -LiteralPath $collectorPythonFile)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $collectorPythonFile) -Force | Out-Null
        $collectorPython | Set-Content -LiteralPath $collectorPythonFile -Encoding UTF8
    }
} else { Write-Warning '未找到采集模块；平台仍可浏览已有资料，采集页会显示接入状态。' }
& (Join-Path $screeningRoot 'install-document-converter.ps1')
$screeningNodeDirectory = Join-Path $screeningRuntime 'node\bin'
if (Test-Path -LiteralPath (Join-Path $screeningNodeDirectory 'node.exe')) { $env:PATH = "$screeningNodeDirectory;$env:PATH" }
$screeningBundledPnpm = Join-Path $screeningRuntime 'bin\fallback\pnpm.cmd'
if (Test-Path -LiteralPath $screeningBundledPnpm) { $screeningPnpm = $screeningBundledPnpm }
else { $screeningPnpm = (Get-Command pnpm -ErrorAction Stop).Source }
$screeningNode = (Get-Command node -ErrorAction Stop).Source
$screeningNodeVersion = [version]((& $screeningNode -p 'process.versions.node').Trim())
if ($screeningNodeVersion -lt [version]'22.19.0') { throw 'Pi SDK需要Node.js 22.19或更新版本，请更新Node后重新安装。' }
Push-Location (Join-Path $screeningRoot 'agent-runtime')
try {
    & $screeningPnpm install --frozen-lockfile --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'Pi SDK依赖安装失败。' }
    & $screeningPnpm build
    if ($LASTEXITCODE -ne 0) { throw 'Pi SDK分析组件构建失败。' }
} finally { Pop-Location }
Push-Location (Join-Path $screeningRoot 'frontend')
try {
    & $screeningPnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
    & $screeningPnpm build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
} finally { Pop-Location }
Write-Host '采集模块、平台后端、Pi SDK分析组件与前端已准备好。运行 start-platform.ps1 即可。'
