# 오류 처리와 재실행

## 요청 형식 오류는 AI 호출 전 발생

```python
from pydantic import ValidationError
from wemeet_ai.m1.e_member_plan import MemberPlanRequest
try:
    request = MemberPlanRequest.model_validate(payload)
except ValidationError as exc:
    fields = [{"path": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    # SW: 해당 입력 옆에 표시합니다. 원본 입력·키 전체를 로그에 출력하지 않습니다.
```

테스트 HTTP 래퍼는 위 형식 오류를 422로 반환합니다. 함수의 공통 Result와 입력 모델 생성 오류를 구분하세요.

## 실행 결과 오류

| code | 의미 | 다음 행동 |
|---|---|---|
| AUTH_FAILED | 키 없음·401·403 | 서버 환경변수/계정을 확인. 자동 재시도 없음 |
| TIMEOUT / NETWORK_ERROR | 읽기 60초·일시 네트워크 오류 | 자동 1회 재시도 후에도 실패하면 해당 호출만 재실행 |
| RATE_LIMITED / UPSTREAM_ERROR | 429·5xx | 같은 제한 내 자동 1회 재시도 |
| EMPTY_RESPONSE | 본문/선택 결과 없음 | 실패로 표시, 성공 결과 보존 |
| TRUNCATED_OUTPUT | finish_reason=length | 성공으로 처리하지 않음 |
| INVALID_JSON / SCHEMA_INVALID | 파싱·형식 오류 | 구조 보정 1회, 계속 실패하면 입력과 오류 확인 |
| INVALID_EVIDENCE | 실제 메시지와 다른 근거 | 결과를 사실로 저장하지 않음 |
| PLAN_INVALID | 날짜·시간·담당 등 검사 실패 | 오류 위치를 검토하고 실패 단계만 수정 |
| RETRY_BUDGET_EXCEEDED | 모듈 HTTP 총 4회 초과 | 자동 무한 반복하지 않음 |
| REPEATED_QUESTION | 이미 답한 개인 조건 재질문 | 보정 후에도 실패하면 기존 state 유지 |
| COACH_POLICY | 끝난다는 거짓 약속 등 | 응답 보정 후 계속 실패하면 오류 표시 |

정보 부족은 실패가 아니라 NEEDS_INPUT입니다. needed_inputs는 한번에 표시하세요. 앞선 모듈을 다시 실행하라는 의미가 아닙니다.

E 버튼 답변 후 AI가 실패하면 결과 data.state에 이미 반영된 답변을 저장합니다. 같은 답변을 새 메시지로 중복 제출하지 않습니다. 실제 사용량은 실패에서도 meta에 남습니다.

## 설치·실행 문제

- `No module named wemeet_ai`: 프로젝트 루트에서 설치했는지, 설치에 사용한 가상환경 Python으로 실행하는지 확인.
- `No module named uvicorn`: 코어만 설치된 상태. `pip install ".[web]"` 실행.
- 포트 8010 사용 중: 기존 테스트 서버를 Ctrl+C로 종료하거나 `--port 8011` 사용.
- 키를 설정했는데 AUTH_FAILED: 서버를 실행한 터미널의 환경변수인지 확인. 서버 재시작. 키를 화면에 출력하지 않음.
- 화면의 저장 파일 불러오기 실패: 새 형식은 schema_version=0.11.0. 예전 DB 자동 호환은 지원하지 않음.

## 개발 중 코드 변경

`SOURCE_RELOAD_REQUIRED`: 파일이 변경됐지만 현재 Python 프로세스에는 이전 코드가 남아 있습니다. API 호출 없이 중단합니다. 자동 재시작이 완료된 뒤 같은 입력으로 다시 실행하세요. 자동 재시작을 끈 환경에서는 서버를 재시작해야 합니다.

## Timely 전용 오류 (0.13.0)

- `AUTH_FAILED`: TIMELY_API_KEY 미등록 또는 HTTP 401/403. 키를 확인한 후 해당 단계만 다시 실행합니다.
- `INSUFFICIENT_CREDITS`: HTTP 402. 타임리 잔액/권한을 확인하세요. 자동 재시도하지 않습니다.
- `MODEL_NOT_AVAILABLE`: HTTP 404. 모델 ID와 base URL을 확인하세요. 자동 모델 변경은 하지 않습니다.
# PostgreSQL 저장 결과 추가

- `REVISION_CONFLICT`: 최신 기록을 읽고 사용자가 변경을 합친 뒤 새 action_id로 제출합니다. 같은 동작 ID의 입력은 바꾸지 않습니다.
- `STORAGE_FAILED` + `SAVE_ONLY`: 응답은 암호화 보관됐습니다. 같은 action_id·payload로 저장만 다시 시도합니다.
- `RESULT_VALIDATION_FAILED`: 확인되지 않은 조건 등 잘못된 결과를 실패 이력으로 저장했습니다. 조건을 바로잡은 새 동작을 제출합니다.
- `RUNNING`: 같은 실행의 상태만 조회합니다. 새 동작 ID를 무작정 생성하지 않습니다.
- `UNKNOWN`: 응답 수신 여부를 확인해야 합니다. 자동 유료 재호출하지 않습니다.

