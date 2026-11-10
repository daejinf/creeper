param(
    [switch]$NoReload,
    [ValidateRange(1, 65535)]
    [int]$Port = 8010
)
$ErrorActionPreference = 'Stop'
Remove-Item Env:WEMEET_GOAL_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '먼저 .\setup.ps1 을 실행하세요.' }
if (-not $env:TIMELY_API_KEY) { $env:TIMELY_API_KEY = [Environment]::GetEnvironmentVariable('TIMELY_API_KEY', 'User') }
if (-not $env:WEMEET_RECOVERY_KEY) { $env:WEMEET_RECOVERY_KEY = [Environment]::GetEnvironmentVariable('WEMEET_RECOVERY_KEY', 'User') }
if (-not $env:WEMEET_GOAL_DATABASE_URL) { $env:WEMEET_GOAL_DATABASE_URL = [Environment]::GetEnvironmentVariable('WEMEET_GOAL_DATABASE_URL', 'User') }
if ($env:WEMEET_GOAL_DATABASE_URL -and (Test-Path -LiteralPath 'runtime/postgres-goals/PG_VERSION')) { & $python -m tools.db.bootstrap --start-only; if ($LASTEXITCODE -ne 0) { Write-Warning 'PostgreSQL 시작 실패. 목표 DB 저장 상태를 확인하세요.' } }

function Test-LocalTcpPort {
    param([int]$Candidate)
    $listener = $null
    try {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Candidate)
        $listener.Start()
        return $true
    } catch [Net.Sockets.SocketException] {
        return $false
    } finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

$portCandidates = @($Port)
if ($Port -eq 8010) {
    $portCandidates += 8011..8099
    $portCandidates += @(18010, 28010, 38010, 48010)
}
$selectedPort = $portCandidates | Where-Object { Test-LocalTcpPort $_ } | Select-Object -First 1
if ($null -eq $selectedPort) { throw "사용 가능한 로컬 포트를 찾지 못했습니다. .\run.cmd -Port 9000 처럼 다른 포트를 지정하세요." }
if ($selectedPort -ne $Port) { Write-Warning "포트 $Port 사용이 거부되어 $selectedPort 포트로 자동 변경합니다." }

$uvicornArgs=@('-m','uvicorn','tools.bench.server:app','--app-dir',(Join-Path $PSScriptRoot 'src'),'--host','127.0.0.1','--port',"$selectedPort",'--timeout-graceful-shutdown','300')
$env:WEMEET_DEV_RELOAD=if($NoReload){'0'}else{'1'}
if (-not $NoReload) { $uvicornArgs+=@('--reload','--reload-dir',(Join-Path $PSScriptRoot 'src'),'--reload-dir',(Join-Path $PSScriptRoot 'tools\bench')) }
$modeLabel=if($NoReload){'수동 재시작'}else{'Python 변경 자동 반영'}
Write-Host ("킥오프: http://127.0.0.1:$selectedPort/kickoff-demo · "+$modeLabel+' · 종료: Ctrl+C')
& $python @uvicornArgs
exit $LASTEXITCODE
