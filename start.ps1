param([switch]$NoReload)
$ErrorActionPreference = 'Stop'
Remove-Item Env:WEMEET_GOAL_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '먼저 .\setup.ps1 을 실행하세요.' }
if (-not $env:TIMELY_API_KEY) { $env:TIMELY_API_KEY = [Environment]::GetEnvironmentVariable('TIMELY_API_KEY', 'User') }
if (-not $env:WEMEET_RECOVERY_KEY) { $env:WEMEET_RECOVERY_KEY = [Environment]::GetEnvironmentVariable('WEMEET_RECOVERY_KEY', 'User') }
if (-not $env:WEMEET_GOAL_DATABASE_URL) { $env:WEMEET_GOAL_DATABASE_URL = [Environment]::GetEnvironmentVariable('WEMEET_GOAL_DATABASE_URL', 'User') }
if ($env:WEMEET_GOAL_DATABASE_URL -and (Test-Path -LiteralPath 'runtime/postgres-goals/PG_VERSION')) { & $python -m tools.db.bootstrap --start-only; if ($LASTEXITCODE -ne 0) { Write-Warning 'PostgreSQL 시작 실패. 목표 DB 저장 상태를 확인하세요.' } }
$uvicornArgs=@('-m','uvicorn','tools.bench.server:app','--app-dir',(Join-Path $PSScriptRoot 'src'),'--host','127.0.0.1','--port','8010','--timeout-graceful-shutdown','300')
$env:WEMEET_DEV_RELOAD=if($NoReload){'0'}else{'1'}
if (-not $NoReload) { $uvicornArgs+=@('--reload','--reload-dir',(Join-Path $PSScriptRoot 'src'),'--reload-dir',(Join-Path $PSScriptRoot 'tools\bench')) }
$modeLabel=if($NoReload){'수동 재시작'}else{'Python 변경 자동 반영'}
Write-Host ('킥오프: http://127.0.0.1:8010/kickoff-demo · '+$modeLabel+' · 종료: Ctrl+C')
& $python @uvicornArgs
exit $LASTEXITCODE
