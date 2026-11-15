# CLAUDE.md

이 파일은 이 저장소에서 코드를 작업할 때 Claude Code(claude.ai/code)에 제공되는 지침입니다.

## 프로젝트 개요

**wemeet-ai-core v0.15.0** — 그룹 목표 코칭, 분석, 계획을 위한 독립 AI 모듈(M0/M1)로 구성된 Python 라이브러리입니다. SW 팀이 사용하는 AI 팀의 산출물이며, 웹 제품이 아닙니다. 각 모듈 함수(A–F)는 독립적으로 호출할 수 있으며, 순차적인 파이프라인이 필요하지 않습니다.

주 AI 제공자는 **Timely GPT**(OpenAI 호환 엔드포인트)입니다. 테스트 벤치 웹 UI는 선택적 도구이며 주 제품이 아닙니다.

## 명령어

### 설정 및 실행 (Windows)
```powershell
.\run.cmd                        # 최초 실행 시 venv 설치, Timely API 키 입력 요청, 개발 서버 시작
.\run.cmd -Port 9000             # 포트 지정
.\run.cmd -NoReload              # 파일 변경 시 자동 재시작 비활성화
.\run.cmd -SetupOnly             # 의존성만 설치하고 서버는 시작하지 않음
```

### 수동 설정
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install ".[web,database,test]"
```

### 테스트 실행
```powershell
.venv\Scripts\python -m pytest                      # 전체 테스트 (REPLAY 모드, API 키 불필요)
.venv\Scripts\python -m pytest tests/test_m0.py     # 단일 파일
.venv\Scripts\python -m pytest -k "test_coaching"   # 이름 패턴으로 실행
```

`test_records_api.py`, `test_kickoff*.py`, `test_bench.py`의 테스트는 `WEMEET_GOAL_DATABASE_URL`이 필요하며, 없으면 건너뜁니다.

### API 키 등록 (Windows, 셸에 노출되지 않음)
```powershell
.\tools\set-timely-key.ps1
```

### 예제 실행 (REPLAY 모드, API 키 불필요)
```powershell
.venv\Scripts\python examples/a_coaching.py
.venv\Scripts\python examples/e_member_plan.py --output result.json
```

### 실제 API로 실행
```powershell
$env:TIMELY_API_KEY = [Environment]::GetEnvironmentVariable("TIMELY_API_KEY", "User")
$env:TIMELY_MODEL = "openai/gpt-5.6-luna"
$env:TIMELY_BASE_URL = "https://hello.timelyai.io/api/v2/chat/bridge/openai"
.venv\Scripts\python examples/a_coaching.py --live
```

## 아키텍처

### 계층 구조

```
src/wemeet_ai/
  m0/          # 전송 계층 — Timely HTTP API와 통신하는 유일한 코드
  m1/a–f/      # 독립 기능 모듈 (코칭, 분석, 초안, 계획, 검증)
  shared/      # Pydantic 계약, 개인정보 규칙, 캘린더 유틸리티
  continuity.py  # 기록을 요청으로 변환하는 순수 함수 (DB/IO 없음)
  persistence/   # PostgreSQL 어댑터 (선택 사항; 코어 라이브러리에서 import하지 않음)
```

**M0**(`m0/client.py`, `m0/runtime.py`)는 모든 HTTP 전송, 재시도, JSON 복구, 캐싱, 트레이싱을 처리합니다. M1 함수는 `Runtime.run(task, request, output_schema, instructions)`를 호출하며 HTTP를 직접 다루지 않습니다.

**M1 모듈**은 각각 자신의 하위 패키지에 있습니다. 각 모듈은 타입이 지정된 `*Request` Pydantic 모델과 하나 이상의 최상위 함수를 export합니다. 함수는 상태를 가지지 않으며, 코칭 상태(`state`)는 호출자에게 반환되고 다음 호출 때 다시 전달해야 합니다.

### 결과 계약

모든 공개 함수는 `shared/contracts.py`의 `Result[T]`를 반환합니다:
- `status`: `"OK"` | `"NEEDS_INPUT"` | `"FAILED"`
- `data`: 타입이 지정된 출력 (`status == "OK"`일 때만)
- `needed_inputs`: 누락된 필드 설명 목록 (`NEEDS_INPUT`일 때)
- `error.retryable`: 재시도가 안전한지 여부 (`FAILED`일 때)
- `meta.execution_source`: `"LIVE"` | `"CACHE"` | `"CODE"` | `"REPLAY"`

**F(검증) 특수 사례:** `status == "OK"`이면서 `data.valid == false`이면 검증에 실패했다는 의미이며, 오류가 아닙니다.

### 환경 변수

| 변수 | 용도 |
|---|---|
| `TIMELY_API_KEY` | LIVE 호출에 필요 |
| `TIMELY_MODEL` | 모델 이름 (기본값: `openai/gpt-5.6-luna`) |
| `TIMELY_BASE_URL` | 끝에 `/v1`이 없는 기본 URL |
| `WEMEET_GOAL_DATABASE_URL` | 목표/기록 저장용 PostgreSQL DSN |
| `WEMEET_RECOVERY_KEY` | 암호화된 복구 저장소용 Fernet 키 |

API 키는 환경 변수에서만 읽습니다. 코드, JSON, `.env` 자동 로드에서는 절대 읽지 않습니다.

### 캐싱

동일한 호출의 중복을 제거하려면 `Runtime(cache=MemoryResultCache(), scope_id="<authenticated-action-id>")`를 전달하세요. 캐시 키에는 작업, 모델, API 키 해시, 프롬프트 버전, 요청 페이로드가 포함됩니다. 사용자가 새 초안을 명시적으로 요청할 때만 `fresh=True`를 사용하세요.

### 저장소

**PostgreSQL이 정식(canonical) 저장소입니다.** SQLite 클래스는 기존 데이터 마이그레이션의 회귀 테스트용으로만 존재합니다. 코어 Python 패키지(`wemeet_ai.*`)는 SQLite나 FastAPI를 import하지 않으며, 이들은 `tools/`와 `persistence/`에 있습니다.

### 테스트 벤치 (선택적 웹 UI)

`tools/bench/`는 대화형 테스트를 위한 FastAPI 앱입니다. `.\run.cmd` 또는 `.\start.ps1`로 시작합니다. `src/`를 직접 사용하며 Python 변경 시 자동으로 다시 로드됩니다. 기록은 PostgreSQL에 저장됩니다(로컬 대체용으로는 `runtime/records.sqlite3`). 이 UI를 외부에 배포하지 마세요.

## 주요 규칙

- 모든 공개 입력/출력은 `extra="forbid"`가 설정된 Pydantic `Contract` 모델입니다.
- 요청 필드의 `confirmed=True`는 SW 호출자가 사용자 동의를 확인했다는 뜻이며, 이는 계약상의 의무이지 라이브러리가 강제하는 게이트가 아닙니다.
- 결과 저장: 저장하기 전에 항상 `shared/privacy.py`의 `sanitize(result.model_dump(mode="json"))`를 호출하세요.
- E 모듈 코칭 상태(`result.data.state`)는 실패하더라도 보존하여 다음 호출 때 다시 전송해야 합니다.
- 비공개 대화 데이터는 구성원 간에 절대 섞이지 않아야 합니다.
