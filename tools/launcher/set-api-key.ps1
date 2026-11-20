# setup-api-key.cmd가 실행한다. Timely API 키를 .env에 저장한다.
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:MoimRoot

Write-Host '=== MOIM: Timely API 키 설정 ===' -ForegroundColor Cyan
Initialize-EnvFile
if (Get-EnvValue 'TIMELY_API_KEY') {
    Write-Warn '.env에 이미 키가 있습니다. 새로 입력하면 덮어씁니다.'
}
if (-not (Read-ApiKeyToEnv)) { exit 1 }
Write-Host ''
Write-Host '    완료. 서버가 이미 켜져 있다면 창을 닫고 start-moim.cmd를 다시 실행하세요.' -ForegroundColor White
