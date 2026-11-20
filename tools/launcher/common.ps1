# MOIM 로컬 실행 도우미 공통 함수 (start.ps1, set-api-key.ps1에서 불러 쓴다).
# 비밀값(.env의 DB 비밀번호, TIMELY_API_KEY)은 화면이나 로그에 출력하지 않는다.

$script:MoimRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:EnvFile = Join-Path $script:MoimRoot '.env'
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Stop-Moim([string]$msg) {
    Write-Host "`n[실패] $msg" -ForegroundColor Red
    exit 1
}

function Test-PortFree([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1', $port); return $false } catch { return $true } finally { $client.Dispose() }
}

function New-RandomSecret([int]$length = 24) {
    $chars = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'.ToCharArray()
    $bytes = New-Object byte[] $length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

function Read-EnvLines {
    if (-not (Test-Path $script:EnvFile)) { return @() }
    return @([System.IO.File]::ReadAllLines($script:EnvFile, $script:Utf8NoBom))
}

function Write-EnvLines([string[]]$lines) {
    [System.IO.File]::WriteAllLines($script:EnvFile, $lines, $script:Utf8NoBom)
}

function Get-EnvValue([string]$name) {
    foreach ($line in Read-EnvLines) {
        if ($line -match "^\s*$([regex]::Escape($name))\s*=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return ''
}

function Set-EnvValue([string]$name, [string]$value) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in Read-EnvLines) {
        if ($line -match "^\s*$([regex]::Escape($name))\s*=") {
            if (-not $found) { $lines.Add("$name=$value"); $found = $true }
        } else { $lines.Add($line) }
    }
    if (-not $found) { $lines.Add("$name=$value") }
    Write-EnvLines $lines.ToArray()
}

# .env가 없으면 만든다. 예시의 CHANGE_ME 비밀번호는 무작위 값으로 바꾼다(로컬 전용 DB 비밀번호).
function Initialize-EnvFile {
    if (-not (Test-Path $script:EnvFile)) {
        $pw = New-RandomSecret
        $lines = @(
            '# MOIM 로컬 실행 설정 (Git에 올리지 않음). start-moim.cmd가 자동으로 만들었다.',
            'MOIM_ENV=development',
            "MOIM_DATABASE_URL=postgresql+psycopg://moim:$pw@127.0.0.1:54329/moim_dev",
            '# 테스트 전용 DB(이름이 _test로 끝나야 함)',
            "MOIM_TEST_DATABASE_URL=postgresql+psycopg://moim:$pw@127.0.0.1:54329/moim_test",
            '# Timely API 키: setup-api-key.cmd로 넣는다. 비워 두면 사용자 환경변수 TIMELY_API_KEY를 쓴다.',
            'TIMELY_API_KEY='
        )
        Write-EnvLines $lines
        Write-Ok '.env 파일을 새로 만들었습니다 (로컬 DB 비밀번호는 무작위로 생성).'
        return
    }
    $db = Get-EnvValue 'MOIM_DATABASE_URL'
    $dataDir = Join-Path $script:MoimRoot '.local\pgdata'
    if ($db -match ':CHANGE_ME@' -and -not (Test-Path $dataDir)) {
        $pw = New-RandomSecret
        foreach ($name in 'MOIM_DATABASE_URL', 'MOIM_TEST_DATABASE_URL') {
            $v = Get-EnvValue $name
            if ($v) { Set-EnvValue $name ($v -replace ':CHANGE_ME@', ":$pw@") }
        }
        Write-Ok '.env의 CHANGE_ME 비밀번호를 무작위 값으로 바꿨습니다.'
    }
}

# .env의 값을 현재 프로세스 환경변수로 불러온다. 빈 값은 건너뛴다(기존 사용자 환경변수 유지).
function Import-EnvFile {
    foreach ($line in Read-EnvLines) {
        if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
        $name = $Matches[1]
        $value = $Matches[2].Trim().Trim('"').Trim("'")
        if ($value) { Set-Item -Path "Env:$name" -Value $value }
    }
}

# 화면에 보이지 않게 Timely API 키를 입력받아 .env에 저장한다.
function Read-ApiKeyToEnv {
    Write-Host ''
    Write-Host '    Timely API 키를 붙여넣고 Enter를 누르세요. (입력 내용은 화면에 표시되지 않습니다)' -ForegroundColor White
    Write-Host '    붙여넣기: 창 안에서 마우스 오른쪽 클릭 또는 Ctrl+V' -ForegroundColor DarkGray
    $secure = Read-Host '    API 키' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    $key = ($key -replace '\s', '')
    if (-not $key) { Write-Warn '입력이 비어 있어 저장하지 않았습니다.'; return $false }
    Set-EnvValue 'TIMELY_API_KEY' $key
    $env:TIMELY_API_KEY = $key
    $tail = if ($key.Length -ge 4) { $key.Substring($key.Length - 4) } else { '' }
    Write-Ok "저장했습니다 (.env, 끝 4자리 ...$tail, 길이 $($key.Length)자)."
    return $true
}
