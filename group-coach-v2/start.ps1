param([switch]$NoReload)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$python=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
foreach($name in @('TIMELY_API_KEY','TIMELY_MODEL','TIMELY_BASE_URL','WEMEET_COACH_DATABASE_URL','WEMEET_COACH_RECOVERY_KEY')) {
 if(-not [Environment]::GetEnvironmentVariable($name,'Process')) { [Environment]::SetEnvironmentVariable($name,[Environment]::GetEnvironmentVariable($name,'User'),'Process') }
}
Remove-Item Env:WEMEET_COACH_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
if(-not $env:WEMEET_COACH_DATABASE_URL -or -not $env:WEMEET_COACH_RECOVERY_KEY){throw '새 DB를 tools.provision으로 먼저 설정하세요. 기존 DB로 대체하지 않습니다.'}
$argsList=@('-m','uvicorn','tools.web:app','--app-dir','src','--host','127.0.0.1','--port','8020')
if(-not $NoReload){$argsList+=@('--reload','--reload-dir','src','--reload-dir','tools','--reload-dir','web')}
& $python @argsList
