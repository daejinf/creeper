param(
    [switch]$NoReload,
    [switch]$SetupOnly,
    [ValidateRange(1, 65535)]
    [int]$Port = 8010
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$setupStamp = Join-Path $PSScriptRoot '.venv\.wemeet-setup'
$projectFile = Join-Path $PSScriptRoot 'pyproject.toml'
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $hashBytes = $sha256.ComputeHash([IO.File]::ReadAllBytes($projectFile))
    $projectHash = [BitConverter]::ToString($hashBytes).Replace('-', '')
} finally {
    $sha256.Dispose()
}
$needsSetup = -not (Test-Path -LiteralPath $venvPython)

if (-not $needsSetup) {
    & $venvPython -c 'import fastapi, sqlalchemy, uvicorn, wemeet_ai' 2>$null
    $needsSetup = $LASTEXITCODE -ne 0
}

if (-not $needsSetup) {
    $installedHash = if (Test-Path -LiteralPath $setupStamp) { (Get-Content -LiteralPath $setupStamp -Raw).Trim() } else { '' }
    $needsSetup = $installedHash -ne $projectHash
}

if ($needsSetup) {
    Write-Host '첫 실행에 필요한 Python 환경과 패키지를 설치합니다.'
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($SetupOnly) {
    Write-Host '실행 준비가 완료되었습니다.'
    exit 0
}

if ($NoReload) {
    & (Join-Path $PSScriptRoot 'start.ps1') -NoReload -Port $Port
} else {
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port
}
exit $LASTEXITCODE
