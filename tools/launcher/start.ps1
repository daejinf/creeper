# start-moim.cmd가 실행한다. 처음 한 번은 설치까지, 그 다음부터는 바로 서버를 띄운다.
#   1) Python 3.12 확인 → .venv 생성·패키지 설치(pyproject/constraints가 바뀌면 다시 설치)
#   2) .env 준비 → 환경변수로 불러오기 → Timely API 키 확인
#   3) 로컬 PostgreSQL(.local\pgdata, 포트 54329) 생성·시작 → DB 생성 → 마이그레이션
#   4) uvicorn 개발 서버 시작 → 준비되면 브라우저 열기
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:MoimRoot
Write-Host '=== MOIM 로컬 실행 ===' -ForegroundColor Cyan
Write-Host "    폴더: $script:MoimRoot"

# ---------- 1. Python ----------
function Test-Python312([string]$exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    $v = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    return ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq '3.12')
}

function Find-Python312 {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $p = & $py.Source -3.12 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Python312 "$p".Trim())) { return "$p".Trim() }
    }
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", 'C:\Python312\python.exe',
                     "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Python312 $c) { return $c }
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Python312 $cmd.Source)) { return $cmd.Source }
    return $null
}

Write-Step '1/4 Python 환경 확인'
$venvPy = Join-Path $script:MoimRoot '.venv\Scripts\python.exe'
if (-not (Test-Python312 $venvPy)) {
    $base = Find-Python312
    if (-not $base) {
        Stop-Moim ("Python 3.12를 찾지 못했습니다.`n" +
            "    https://www.python.org/downloads/ 에서 Python 3.12.x(Windows installer 64-bit)를 설치하세요.`n" +
            "    설치 첫 화면에서 'Add python.exe to PATH'를 체크한 뒤, 이 파일을 다시 실행하세요.")
    }
    Write-Ok "Python: $base"
    Write-Ok '가상환경(.venv)을 만듭니다...'
    & $base -m venv .venv
    if ($LASTEXITCODE -ne 0) { Stop-Moim '가상환경 생성에 실패했습니다.' }
}

$marker = Join-Path $script:MoimRoot '.venv\.moim-deps'
$depsHash = ((Get-FileHash -Algorithm SHA256 (Join-Path $script:MoimRoot 'pyproject.toml')).Hash + '-' +
    (Get-FileHash -Algorithm SHA256 (Join-Path $script:MoimRoot 'constraints.txt')).Hash)
$installed = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { '' }
if ($installed -ne $depsHash) {
    Write-Ok '패키지를 설치합니다 (처음 한 번은 몇 분 걸립니다)...'
    & $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
    & $venvPy -m pip install --disable-pip-version-check -e . -c constraints.txt
    if ($LASTEXITCODE -ne 0) { Stop-Moim '패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요.' }
    Set-Content -Path $marker -Value $depsHash -Encoding ASCII
}
Write-Ok 'Python 패키지 준비 완료'

# ---------- 2. 설정(.env)과 API 키 ----------
Write-Step '2/4 설정(.env)과 Timely API 키 확인'
Initialize-EnvFile
Import-EnvFile
if (-not $env:MOIM_ENV) { $env:MOIM_ENV = 'development' }
if (-not $env:TIMELY_API_KEY) {
    Write-Warn 'TIMELY_API_KEY가 없습니다. 키가 없어도 서버는 켜지지만 AI 기능(역할 후보·인터뷰·투두·검토)은 실패합니다.'
    $answer = Read-Host '    지금 키를 입력할까요? (Y/N)'
    if ($answer -match '^[Yy]') { [void](Read-ApiKeyToEnv) }
} else {
    Write-Ok 'TIMELY_API_KEY 확인됨 (값은 표시하지 않음)'
}

# ---------- 3. PostgreSQL ----------
function Find-PgBin {
    $c = Get-Command pg_ctl.exe -ErrorAction SilentlyContinue
    if ($c) { return (Split-Path $c.Source) }
    $root = Join-Path $env:ProgramFiles 'PostgreSQL'
    $dirs = @(Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'bin\pg_ctl.exe') } |
        Sort-Object @{ Expression = { $_.Name -eq '16' }; Descending = $true }, @{ Expression = { $_.Name }; Descending = $true })
    if ($dirs.Count -gt 0) { return (Join-Path $dirs[0].FullName 'bin') }
    return $null
}

Write-Step '3/4 데이터베이스 준비'
if ($env:MOIM_DATABASE_URL -notmatch '^postgresql\+psycopg://([^:@/]+):([^@]*)@([^:/]+):(\d+)/([A-Za-z0-9_]+)') {
    Stop-Moim '.env의 MOIM_DATABASE_URL 형식이 올바르지 않습니다 (.env.example 참고).'
}
$dbUser = $Matches[1]; $dbPass = $Matches[2]; $dbHost = $Matches[3]; $dbPort = $Matches[4]
$dataDir = Join-Path $script:MoimRoot '.local\pgdata'
# 이 폴더의 로컬 DB(.local\pgdata)를 직접 관리하는 경우: 주소가 이 PC이고, 이미 만든 저장소가 있거나 포트가 비어 있을 때.
# 포트를 다른 PostgreSQL이 쓰고 있고 저장소도 없으면 .env가 가리키는 기존 DB를 그대로 쓴다.
$managed = ($dbHost -in @('127.0.0.1', 'localhost')) -and
    ((Test-Path (Join-Path $dataDir 'PG_VERSION')) -or (Test-PortFree ([int]$dbPort)))
if ($managed) {
    $pgBin = Find-PgBin
    if (-not $pgBin) {
        Stop-Moim ("PostgreSQL을 찾지 못했습니다.`n" +
            "    https://www.postgresql.org/download/windows/ 에서 PostgreSQL 16을 설치하세요.`n" +
            "    (설치 중 묻는 비밀번호·포트는 MOIM과 상관없습니다. MOIM은 이 폴더 안에 따로 DB를 만듭니다.)")
    }
    $pgCtl = Join-Path $pgBin 'pg_ctl.exe'
    $pgLog = Join-Path $script:MoimRoot '.local\postgres.log'
    New-Item -ItemType Directory -Force (Join-Path $script:MoimRoot '.local') | Out-Null
    if (-not (Test-Path (Join-Path $dataDir 'PG_VERSION'))) {
        Write-Ok "로컬 DB 저장소를 만듭니다: .local\pgdata ($pgBin)"
        $pwFile = Join-Path $script:MoimRoot ".local\initdb-$(New-RandomSecret 8).tmp"
        [IO.File]::WriteAllText($pwFile, $dbPass, (New-Object System.Text.UTF8Encoding($false)))
        try {
            & (Join-Path $pgBin 'initdb.exe') -D $dataDir -U $dbUser "--pwfile=$pwFile" -A scram-sha-256 -E UTF8 --locale=C | Out-Null
            $code = $LASTEXITCODE
        } finally { Remove-Item $pwFile -Force -ErrorAction SilentlyContinue }
        if ($code -ne 0) { Stop-Moim 'initdb 실패. 폴더 경로에 한글·공백이 있으면 영문 경로(예: C:\moim)로 옮겨 다시 실행해 보세요.' }
    }
    & $pgCtl status -D $dataDir *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Ok "PostgreSQL을 시작합니다 (포트 $dbPort, 이 PC에서만 접속 가능)..."
        # PostgreSQL은 pg_ctl의 출력 핸들을 물려받으므로 파이프(| Out-Null)로 받으면 끝나지 않는다. 별도 프로세스로 기다린다.
        $pg = Start-Process -FilePath $pgCtl -NoNewWindow -PassThru -ArgumentList (
            "start -D `"$dataDir`" -l `"$pgLog`" -w -t 60 -o `"-p $dbPort -c listen_addresses=127.0.0.1`"")
        $null = $pg.Handle
        $pg.WaitForExit()
        if ($pg.ExitCode -ne 0) {
            if (Test-Path $pgLog) { Get-Content $pgLog -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
            Stop-Moim "PostgreSQL 시작에 실패했습니다. 위 로그를 확인하세요 (포트 $dbPort를 다른 프로그램이 쓰고 있으면 .env의 포트 번호를 바꾸세요)."
        }
    } else { Write-Ok 'PostgreSQL 이미 실행 중' }
} else {
    Write-Ok "직접 지정한 DB를 사용합니다 ($dbHost`:$dbPort)"
}

& $venvPy (Join-Path $PSScriptRoot 'ensure_db.py')
if ($LASTEXITCODE -ne 0) { Stop-Moim 'DB에 접속하지 못했습니다. .env의 MOIM_DATABASE_URL과 DB 실행 상태를 확인하세요.' }
& $venvPy -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { Stop-Moim 'DB 마이그레이션(alembic upgrade head)에 실패했습니다.' }
Write-Ok 'DB 준비 완료'

# ---------- 4. 서버 ----------
Write-Step '4/4 서버 시작'
$port = 8000
while (-not (Test-PortFree $port)) {
    $port++
    if ($port -gt 8010) { Stop-Moim '8000~8010 포트가 모두 사용 중입니다.' }
}
$url = "http://127.0.0.1:$port/"
$server = Start-Process -FilePath $venvPy -NoNewWindow -PassThru -ArgumentList @(
    '-m', 'uvicorn', '--factory', 'moim.main:app_factory', '--host', '127.0.0.1', '--port', "$port")

$ready = $false
for ($i = 0; $i -lt 60 -and -not $server.HasExited; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "${url}healthz"
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) {
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    Stop-Moim '서버가 시작되지 않았습니다. 위 오류 메시지를 확인하세요.'
}

Write-Host ''
Write-Host "  MOIM 실행 중: $url" -ForegroundColor Green
Write-Host '  브라우저가 자동으로 열립니다. 끝내려면 이 창을 닫거나 Ctrl+C를 누르세요.' -ForegroundColor Green
Write-Host ''
Start-Process $url
$server.WaitForExit()
