$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    $basePython = $null
    $baseArgs = @()

    $pyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        & $pyLauncher.Source -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $basePython = $pyLauncher.Source
            $baseArgs = @('-3')
        }
    }

    if ($null -eq $basePython) {
        $pythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
        if ($null -ne $pythonCommand) {
            & $pythonCommand.Source -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $basePython = $pythonCommand.Source }
        }
    }

    if ($null -eq $basePython) {
        throw 'Python 3.11 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치할 때 Add Python to PATH를 선택하세요.'
    }

    Write-Host '1/2 가상환경(.venv)을 생성합니다.'
    & $basePython @baseArgs -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw '가상환경 생성에 실패했습니다. Python 설치에서 venv와 pip가 포함됐는지 확인하세요.'
    }
}

& $venvPython -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw '기존 .venv의 Python이 3.11 미만입니다. .venv를 지우고 run.cmd를 다시 실행하세요.' }

Write-Host '2/2 실행에 필요한 패키지를 설치하거나 업데이트합니다.'
$installTemp = Join-Path $PSScriptRoot '.venv\tmp'
New-Item -ItemType Directory -Path $installTemp -Force | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TEMP = $installTemp
    $env:TMP = $installTemp
    & $venvPython -m pip install --disable-pip-version-check -e '.[web,test,database]'
    $installExitCode = $LASTEXITCODE
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
if ($installExitCode -ne 0) { throw '패키지 설치에 실패했습니다. 인터넷 연결과 pip 오류 메시지를 확인하세요.' }

$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $hashBytes = $sha256.ComputeHash([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'pyproject.toml')))
    $projectHash = [BitConverter]::ToString($hashBytes).Replace('-', '')
} finally {
    $sha256.Dispose()
}
Set-Content -LiteralPath (Join-Path $PSScriptRoot '.venv\.wemeet-setup') -Value $projectHash -Encoding Ascii -NoNewline
Write-Host '설치 완료.'
