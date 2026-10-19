$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPathFile = Join-Path $project 'data\python_path.txt'
$python = if (Test-Path -LiteralPath $pythonPathFile) { (Get-Content -LiteralPath $pythonPathFile -Raw).Trim() } else { (Get-Command python -ErrorAction Stop).Source }
Push-Location $project
try {
    & $python -m collector.runner
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "Collector exited with code $exitCode. See data\logs." }
} finally {
    Pop-Location
}
