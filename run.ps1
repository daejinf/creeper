param(
    [switch]$NoReload,
    [switch]$SetupOnly,
    [switch]$SkipApiKeyPrompt,
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

if (-not $env:TIMELY_API_KEY) {
    $env:TIMELY_API_KEY = [Environment]::GetEnvironmentVariable('TIMELY_API_KEY', 'User')
}

if (-not $env:TIMELY_API_KEY -and -not $SkipApiKeyPrompt) {
    Write-Host ''
    Write-Host '실제 AI(LIVE)를 사용하려면 Timely API 키가 필요합니다.'
    Write-Host '키 없이 REPLAY만 사용하려면 아무것도 입력하지 말고 Enter를 누르세요.'
    $timelySecret = Read-Host 'Timely API 키 (입력 내용은 숨겨집니다)' -AsSecureString
    $timelyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($timelySecret)
    try {
        $timelyValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($timelyPointer)
        if ([string]::IsNullOrWhiteSpace($timelyValue)) {
            Write-Warning 'API 키를 등록하지 않았습니다. REPLAY는 사용할 수 있지만 LIVE 호출은 실패합니다.'
        } else {
            $env:TIMELY_API_KEY = $timelyValue.Trim()
            [Environment]::SetEnvironmentVariable('TIMELY_API_KEY', $env:TIMELY_API_KEY, 'User')
            Write-Host 'API 키를 화면에 노출하지 않고 등록했습니다. 이번 실행부터 LIVE를 사용할 수 있습니다.'
        }
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($timelyPointer)
        $timelyValue = $null
        $timelySecret.Dispose()
    }
}

if ($NoReload) {
    & (Join-Path $PSScriptRoot 'start.ps1') -NoReload -Port $Port
} else {
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port
}
exit $LASTEXITCODE
