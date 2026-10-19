$ErrorActionPreference = 'Stop'
$documentConverterCandidates = @(
    $env:LIBREOFFICE_PATH,
    (Join-Path $env:ProgramFiles 'LibreOffice\program\soffice.com'),
    (Join-Path ${env:ProgramFiles(x86)} 'LibreOffice\program\soffice.com')
)
if ($documentConverterCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) }) {
    Write-Host '自动文档转换组件已就绪。'
    return
}
if (Get-Command soffice -ErrorAction SilentlyContinue) { return }
$documentWinget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $documentWinget) { throw '缺少自动文档转换组件和winget；请部署LibreOffice并设置LIBREOFFICE_PATH。' }
& $documentWinget.Source install --id TheDocumentFoundation.LibreOffice --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
if ($LASTEXITCODE -ne 0) { throw '自动文档转换组件安装失败，请检查网络和安装权限。' }
Write-Host '自动文档转换组件已安装。'
