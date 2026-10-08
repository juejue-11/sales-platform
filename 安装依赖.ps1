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
$screeningNodeDirectory = Join-Path $screeningRuntime 'node\bin'
if (Test-Path -LiteralPath (Join-Path $screeningNodeDirectory 'node.exe')) { $env:PATH = "$screeningNodeDirectory;$env:PATH" }
$screeningBundledPnpm = Join-Path $screeningRuntime 'bin\fallback\pnpm.cmd'
if (Test-Path -LiteralPath $screeningBundledPnpm) { $screeningPnpm = $screeningBundledPnpm }
else { $screeningPnpm = (Get-Command pnpm -ErrorAction Stop).Source }
Push-Location (Join-Path $screeningRoot 'frontend')
try {
    & $screeningPnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
    & $screeningPnpm build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
} finally { Pop-Location }
Write-Host '依赖与前端已准备好。运行 启动平台.ps1 即可。'
