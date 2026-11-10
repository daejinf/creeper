$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 이상과 가상환경 생성을 확인하세요.' }
& .venv\Scripts\python.exe -m pip install -e '.[web,test,database]'
if ($LASTEXITCODE -ne 0) { throw '패키지 설치에 실패했습니다.' }
Write-Host '설치 완료. .\start.ps1 로 모듈 테스트 화면을 실행하세요.'
