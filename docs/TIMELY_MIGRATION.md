# Timely GPT 전환 · 0.13.0 검증 기록

## 구현한 범위

- 기본 M0 클라이언트 `TimelyClient`, 모델 `openai/gpt-5.6-luna`.
- base URL `https://hello.timelyai.io/api/v2/chat/bridge/openai`; `/chat/completions`를 붙여 호출.
- 서버 `TIMELY_API_KEY`만 인증에 사용. DeepSeek 키를 Timely로 보내지 않음.
- DeepSeek 전용 `thinking` 제거. 자연어·JSON Schema 검증, 오류 분류, 재시도·총 HTTP 4회 제한·캐시·추적 유지.
- M1-A~F 공개 함수와 기존 SQLite 보존. 과거 모델명과 토큰은 변경하지 않음.

## 수행한 검사

| 검사 | 결과 | 실제 AI 호출 |
|---|---|---|
| 회귀 검사 | 128 통과 | 없음: 가짜 HTTP/작성 fixture/코드 검사 |
| 200개 시나리오 데이터 계약 | 200 통과 | 없음 |
| 새 가상환경 wheel 설치 | 통과, FastAPI 없이 코어 import | 없음 |
| A~F 독립 예제 6개 | 통과 | 없음: fixture/code |
| 기록 저장·복원·연결 예제 | 통과 | 없음: fixture |
| wheel 소스 일치·비밀 검사 | 통과 | 없음 |
| 8010 서버 재시작·브라우저 | 0.13.0, Timely 설정됨 표시, 기존 기록 8개 복원 | 없음 |
| 기존 DB | records 8 / messages 36 / drafts 29 / artifacts 39 유지 | 없음 |

FastAPI TestClient의 httpx 지원 중단 예정 경고 1건이 있습니다. 테스트 실패는 없습니다.

## 실제 GPT 검증 결과 (키 등록 후 추가 수행)

- 실제 HTTP 총 **11회**(초기 실패·보정·재검사 포함), 한도 12회 유지.
- A/B/C/D/E.coach/E.plan 여섯 진입점 모두 최종 OK. 성공한 B/C/D/개인 계획은 재호출하지 않았습니다.
- 개인 코칭 초기 응답은 개인 대화가 비어 있는데도 추출 사실을 반환하여 2회 실행에서 실패했습니다. 본인 발언 전에는 추출 조건·응답 상태를 비워 두도록 경계를 보완하고, 재검사 1회 성공을 확인했습니다. 원본 실패 결과는 attempts에 보존했습니다.
- A가 달력의 28일 참고 범위를 마감일로 오해한 내용을 발견했습니다. 참고표는 날짜 제한이 아님을 명시한 뒤 12/21 마감이 유지되는 응답을 확인했습니다.
- 최신 A 코칭 10,774ms로 **10초 목표 미달**입니다. 최초 응답은 11,552ms였습니다. 단일 사례이며 p95 측정은 아닙니다.
- 개인 계획은 주 30분 기준으로 3주 각 30분 작업을 배정했습니다. F 코드 검증에서 기간·주간 시간 예산 비교를 통과했습니다.
- 실제 키가 코드·화면 파일·로그·평가 결과·기존 DB에 포함되지 않는지 비교 검사했습니다. 인증 헤더는 저장하지 않았습니다.
- 대화형 개인 답변의 LIVE 다중 턴, 다양한 분야 품질, SW팀 직접 인수와 사람의 내용 검토는 아직 수행하지 않았습니다. 에이전트의 제한된 내용 검토를 사람 인수 완료로 집계하지 않습니다.

## 키 등록 후 확인 순서

프로젝트 폴더의 본인 PowerShell에서 다음을 실행합니다. 키는 채팅·명령행 문자열에 붙이지 않습니다.

```powershell
.\tools\set-timely-key.ps1
$env:TIMELY_API_KEY = [Environment]::GetEnvironmentVariable('TIMELY_API_KEY', 'User')
.venv\Scripts\python evaluations/timely_migration.py --live
```

A/B/C/D/E.coach/E.plan을 실행하며 전송·보정 포함 총 HTTP 12회 한도를 로컬 파일에 저장합니다. 성공 단계는 다시 호출하지 않습니다. 실패만 재실행하려면 같은 예산 안에서 `--retry-failed`를 추가합니다. 모델이나 입력이 달라지면 결과를 혼합하지 않도록 중단합니다. 실행 결과는 익명 기준 입력을 쓴 로컬 테스트 자료이며 실제 사용자 승인으로 취급하지 않습니다.

서버를 종료한 뒤 `./start.ps1`로 다시 시작하면 새 사용자 환경변수 키를 읽습니다. 화면 조회·복원은 API를 호출하지 않습니다. 기존 기록에는 이전 DeepSeek 결과가 보일 수 있습니다. 새 LIVE 실행의 모델·run_id·사용량으로 구분하세요.

## 전달물

`artifacts/wemeet_ai_core-0.13.0-py3-none-any.whl`, 루트 README, `docs/MIGRATION.md`, 오류 안내, JSON Schema/OpenAPI를 사용하세요. 이전 wheel과 manifest는 기존 외부 백업 폴더 `before-013-timely`에 해시 검증 후 보관했습니다. 키·개인 대화·사용자 DB는 wheel에 포함하지 않습니다.
