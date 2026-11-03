$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "가상환경이 없습니다. 먼저 .\setup.ps1 을 실행하세요."
}
if (-not (Test-Path (Join-Path $projectRoot "frontend\node_modules"))) {
    throw "프론트엔드 패키지가 없습니다. 먼저 .\setup.ps1 을 실행하세요."
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "PostgreSQL 컨테이너 시작"
    & docker compose -f (Join-Path $projectRoot "docker-compose.yml") up -d postgres
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL 컨테이너를 시작하지 못했습니다. Docker Desktop 상태를 확인하세요."
    }
} else {
    Write-Warning "Docker를 찾지 못했습니다. 로컬 PostgreSQL이 실행 중이어야 합니다."
}

$backend = Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory (Join-Path $projectRoot "backend") `
    -NoNewWindow -PassThru

try {
    Write-Host "백엔드: http://127.0.0.1:8000"
    Write-Host "프론트: http://127.0.0.1:5173"
    & npm.cmd run dev --prefix (Join-Path $projectRoot "frontend")
} finally {
    if (-not $backend.HasExited) {
        Stop-Process -Id $backend.Id
    }
}
