$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $projectRoot "backend\requirements-lock.txt"

function Get-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) { return "py -3.12" }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
        if ($LASTEXITCODE -eq 0) { return "python" }
    }

    throw "Python 3.12가 필요합니다. 설치 후 새 터미널에서 다시 실행하세요."
}

$pythonCommand = Get-Python312
if (-not (Test-Path $venvPython)) {
    Write-Host "[1/4] Python 가상환경 생성"
    if ($pythonCommand -eq "py -3.12") {
        & py -3.12 -m venv (Join-Path $projectRoot ".venv")
    } else {
        & python -m venv (Join-Path $projectRoot ".venv")
    }
}

Write-Host "[2/4] Python 패키지 설치"
& $venvPython -m pip install --disable-pip-version-check -r $requirements
& $venvPython -m pip install --disable-pip-version-check --no-deps -e (Join-Path $projectRoot "backend")

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 24.14.0이 필요합니다. 설치 후 새 터미널에서 다시 실행하세요."
}
$nodeVersion = (& node --version).TrimStart("v")
if ($nodeVersion -ne "24.14.0") {
    throw "Node.js 24.14.0이 필요하지만 현재 버전은 $nodeVersion 입니다."
}

Write-Host "[3/4] 프론트엔드 패키지 설치"
& npm.cmd ci --prefix (Join-Path $projectRoot "frontend")

$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $projectRoot ".env.example") $envFile
}

Write-Host "[4/4] 완료"
Write-Host "환경 설정이 끝났습니다. .\start.ps1 로 실행하세요."
