# LIVE_TESTING

AI 로그·실패 처리 규칙, LIVE 예산, 실행 조건과 기록. 승인 근거는 `docs/PRODUCT.md`의 D-A7이다.

## 1. 로그 규칙

provider 요청·응답·파싱 로그에는 전체 messages/body, AI 응답·파싱 결과 원문, 개인 대화, private profile, API 키와 이미지 data URL을 기록하지 않는다. 모델, 스키마명, 메시지 수, 길이, 안전한 해시, 요청 ID, latency, usage, 상태와 오류 코드 같은 메타데이터만 기록한다.

## 2. 실패 처리

MVP의 실패 처리는 단순하게 유지한다. 타임아웃, 공급자 오류, JSON/스키마 오류는 모두 실패로 저장하고 사용자에게 오류와 재시도 버튼을 보여준다. 자동 재시도, UNKNOWN 분류, backoff와 자동 JSON 복구 호출은 하지 않는다. 사용자가 재시도를 누르면 새 호출로 처리하며, 시간 초과 뒤 재시도로 같은 요청이 두 번 과금될 수 있음을 감수한다. 실제 Timely 호출마다 단계, 모델, 성공·실패, 소요 시간과 usage를 로그 한 줄로 남겨 호출 횟수를 셀 수 있게 한다.

## 3. 결정적 테스트와 LIVE 테스트

두 가지는 서로 대체하지 않는다. 결정적 테스트 대상은 `docs/SECURITY.md`의 테스트 매트릭스를 따른다.

LIVE 테스트 대상:

- Timely 연결과 실제 모델
- 응답 스키마와 구조화 출력
- provider request ID, latency와 usage
- PostgreSQL 저장과 재조회
- 필요한 경우 스트리밍과 vision 지원 여부

fixture와 mock 결과는 LIVE로 보고하지 않는다.

## 4. 호출 예산

| 단계 | 상한 | 승인 상태 |
|---|---:|---|
| 0B | 최대 12회 | 승인(D-A7) |
| 2단계 팀 목표 표시문 | 최대 3회 | 승인 범위의 초기 상한(D-A7) |
| 3단계 개인 인터뷰 | 최대 6회 | 초기 상한 |
| 3단계 역할 후보 생성 | 미정 | GATE B에서 별도 승인 |
| 4단계 | 최대 4회 | 초기 상한 |
| GATE D vision 사전 확인 | 최대 3회 | 초기 상한 |
| 5단계 M2.review | 최대 3회 | 초기 상한 |

성공, 실패, 타임아웃과 사용자가 누른 재시도를 각각 1회로 센다. 예산 초과 전에는 이유와 추가 횟수를 보고하고 승인을 받는다. 작은 표본으로 p95를 주장하지 않는다.

### 4-1. 공급자 호출·실패 계약 (0B-2 구현, 2026-11-18)

`wemeet_ai.m0.client.TimelyClient`가 공급자 HTTP를 아는 유일한 모듈이며 계약은 `tests/test_m0_provider.py`가 검증한다.

- `complete_text`·`complete_json` 한 번은 HTTP 요청 한 번이다. `Runtime.run` 한 번도 모델 호출 한 번이다. 재시도, backoff(`sleeper`), 자동 JSON 복구, 검증 실패 뒤 재호출과 호출별 예산 예약(`budget`)을 코드에서 제거했다.
- 실패는 `AIError(code, message, retryable)`로 올라오고 `Runtime`이 `Result(status=FAILED, error=...)`로 바꾼다. `retryable=True`는 "사용자가 재시도 버튼을 보여줄 만한 오류"라는 표시일 뿐 자동 재호출이 아니다. 코드: `TIMEOUT`, `NETWORK_ERROR`, `RATE_LIMITED`, `UPSTREAM_ERROR`, `INVALID_JSON`, `SCHEMA_INVALID`, `AUTH_FAILED`, `INSUFFICIENT_CREDITS`, `MODEL_NOT_AVAILABLE`, `INVALID_REQUEST`, `TRUNCATED_OUTPUT`, `EMPTY_RESPONSE`, `INCOMPLETE_OUTPUT`.
- 키가 없거나 `WEMEET_OFFLINE=1`이면 HTTP를 보내지 않고 호출 로그도 남기지 않는다.
- trace 이벤트: `provider.request`는 호출 ID·모델·메시지 수·본문 바이트 수·SHA-256만, `provider.response`는 호출 ID·상태·본문 바이트 수만, `json.parsed`는 스키마명·바이트 수만, `input.prepared`는 바이트 수와 해시만 담는다. 오류 응답 본문은 기록하지 않는다.
- 호출 로그: 실제 HTTP 호출마다 로거 `moim.provider`에 정확히 한 줄(`provider_call call_id= stage= model= ok= error= latency_ms= prompt_tokens= completion_tokens= total_tokens= request_id=`)을 성공·실패와 관계없이 남긴다. 이 줄의 수가 LIVE 호출 수다. debug trace가 꺼져 있어도 남는다.
- 알려진 미처리: `Runtime.run`의 JSON 작업 `max_completion_tokens` 4096은 클라이언트 기본 1500을 덮어쓴다(`docs/ARCHITECTURE.md`). 모듈별 상한은 0B 측정 뒤 확정한다. `versions.py`의 `SOURCE_RELOAD_REQUIRED`와 함께 **0B-3에서도 정리하지 않았고**, 첫 AI 제품 기능(3단계) 전에 정한다.

### 4-2. 0B-2 판정

| 항목 | 판정 | 근거 |
|---|---|---|
| 결정적 안정화 | DETERMINISTIC PASS (2026-11-18) | `tests/test_m0_provider.py` 37개 통과(LIVE 뒤 기본 주소·모델·토큰 파라미터 테스트 1개 추가). 판정 당시 전체 74개, 0B 마감 시점 전체 105개 통과, 원본 HTML 해시 9/9 일치 |
| LIVE | LIVE PASS (2026-11-18) | 정상 text 1회(호출 5), 정상 structured output 1회(호출 6) 성공. 총 6회 사용, 잔여 6회 |

LIVE에서 발견해 고친 것(결정적 fixture 테스트로는 잡히지 않는 공급자 쪽 변경):

- 옛 주소 `.../chat/bridge/openai`는 종료되어 HTTP 410(`endpoint_retired`)을 반환한다. 기본 주소를 Timely 직접 API `https://hello.timelyai.io/api/v2/chat/direct/openai/v1`로 바꿨다(`TIMELY_BASE_URL`로 덮어쓸 수 있다).
- 직접 API는 접두 없는 모델명을 쓴다. 기본 모델을 `openai/gpt-5.6-luna`에서 `gpt-5.6-luna`로 바꿨다(`TIMELY_MODEL`).
- 이 모델은 `max_tokens`를 거절한다(HTTP 400 `unsupported_parameter`). `max_completion_tokens`로 바꿨다(클라이언트 기본 1500, JSON 작업 4096). 추론 토큰을 포함할 수 있어 실제 작업별 상한은 0B 측정 때 다시 본다.
- 실패 원인 확인용 호출 2회(호출 2, 4)는 오류 본문을 로컬 스크립트에서만 읽었고 저장소 로그·문서에는 남기지 않았다.

## 5. 실행 조건

- 승인된 예산 안에서만 호출한다. API 키 등록 같은 외부 권한 작업은 0B-2에서 사용자에게 따로 확인한다.
- 브라우저에서 Timely를 직접 호출하지 않는다. 서버에서만 호출한다.
- 호출 전에 남은 예산을 확인하고, 호출 뒤 아래 기록 표를 갱신한다.

## 6. 호출 기록

| 날짜 | 단계 | 모델 | 결과 | 소요 시간 | usage | 누적 횟수 |
|---|---|---|---|---|---|---:|
| 2026-11-18 | 0B-2 정상 확인 | openai/gpt-5.6-luna | 실패: 옛 주소 종료(410) | 0.87초 | 0 | 1 |
| 2026-11-18 | 0B-2 원인 확인 | openai/gpt-5.6-luna | 실패: 옛 주소 종료(410) | 미기록 | 0 | 2 |
| 2026-11-18 | 0B-2 정상 확인(새 주소) | gpt-5.6-luna | 실패: `max_tokens` 거절(400) | 1.1초 | 0 | 3 |
| 2026-11-18 | 0B-2 원인 확인 | gpt-5.6-luna | 실패: `max_tokens` 거절(400) | 미기록 | 0 | 4 |
| 2026-11-18 | 0B-2 text | gpt-5.6-luna | 성공 | 2.2초 | 31/5/36 | 5 |
| 2026-11-18 | 0B-2 structured output | gpt-5.6-luna | 성공 | 4.2초 | 121/15/136 | 6 |

잔여 예산: 6회(12회 중). usage는 입력/출력/합계 토큰. 호출 2·4는 오류 본문을 읽으려고 만든 일회용 진단 스크립트로 보냈고, 그 스크립트에는 `moim.provider` 로그를 받는 핸들러가 없어 호출 로그 한 줄과 소요 시간을 캡처하지 못했다(호출 자체는 예산에 포함했다). 따라서 이 두 호출은 4-1의 "호출마다 로그 한 줄" 증거가 불완전하다. 호출 1·3의 소요 시간은 스크립트가 출력한 로그 줄의 값이다.
