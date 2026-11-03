# 과거 사용법·변경 기록

현재 사용법은 README.md와 PUBLIC_API.md를 참고하세요. 아래 문서는 과거 버전의 화면·계약 안내를 보존한 것입니다.

# WE·MEET M0/M1 AI Core · v0.10

**SW팀은 이 패키지의 함수에 입력을 전달하고, 반환된 결과와 사용자 확인을 자체 DB에 저장합니다.** AI가 회원·그룹·권한·투표·알림을 운영하지 않습니다. 대화는 SW가 보관하며 매 호출에 필요한 문맥을 명시적으로 전달합니다.


## v0.10: SW팀은 action + state로 연결

첫 실행은 `python examples/goal_setup_quickstart.py --output runs/setup-demo`입니다. API 키 없이 작성된 fixture로 COACH→REVIEW→RESPOND→CONFIRM을 실행합니다. 실제 호출과 품질 검증으로 집계하지 않습니다.

- `GoalAI.advance_goal_setup(GoalSetupRequest(...))`가 단계 연결·필수 항목 판정·응답 반영을 담당합니다. SW는 반환된 `data.state`를 저장해 다음 요청에 전달합니다. 자세한 요청은 [SW 연결 안내](SW_INTEGRATION.md)를 참고하세요.
- A는 자연어 코칭입니다. **목표 검토하기**를 요청할 때만 B 분석과 C 추천 초안을 연결 실행합니다. 일반 발언과 코칭 답변은 C를 자동 변경하지 않습니다.
- C 기본 화면은 `ASSISTED`입니다. 대화에서 아는 값은 유지하고 나머지는 미확인 추천으로 채웁니다. 수락·수정·답변은 C 안에서 수행하며 A로 돌아갈 필요가 없습니다.
- 저수준 `DraftRequest`의 기본값은 기존 `GROUNDED`로 유지합니다. `ASSISTED`는 연결 함수와 화면에서 명시적으로 선택합니다. 기존 `SUGGEST`와 개별 함수도 유지합니다.
- 수락·직접 수정·확정은 AI 호출0입니다. 자연어 날짜/공개 범위/횟수 해석과 다른 제안은 모델 호출이 필요할 수 있고, 해석한 값은 재확인 대상으로 표시합니다.
- 결과의 `status`와 `data.needed_inputs`, `can_generate_group_plan`, `group_spec`을 함께 확인하세요. REVIEW가 OK여도 수락 전에는 확정 명세가 없습니다.

검증 범위와 최종 재검사/재시작 대기 항목은 [검증 기록](VERIFICATION.md)에 명시했습니다.

아래 v0.9 설명은 이전 화면의 동작을 설명하는 호환성 기록입니다. 현재 기본 화면은 위 흐름입니다.

## v0.9: A는 대화, B는 근거 분류, C는 검토 명세

- A는 `coaching_mode="DIALOGUE"`로 자연어 응답만 반환합니다. `reply`를 채팅에 한 번 표시합니다. 별도 목표 명세 카드·선택 버튼은 만들지 않고 C도 수정하지 않습니다.
- A의 **B · 대화 정리**는 분석만 실행합니다. B의 **C · 검토 초안 작성**을 눌러야 C가 실행됩니다.
- B의 `fields`는 사용자 발언 근거이며, `coaching_notes`는 AI 메시지의 실제 인용·이후 사용자 응답·미선택/선택/거절/모호함을 별도로 기록합니다. 사용자 한 명의 선택은 그룹 전체 합의가 아닙니다.
- C의 기본 `draft_mode="GROUNDED"`는 B의 근거 목록에서만 값을 채웁니다. 없는 날짜·완료 기준·인증·공개 범위는 미정이며 C 진입은 막지 않습니다.
- 미정 카드의 **A에서 대화로 보완**은 주제만 전달합니다. 이동만으로 메시지·API 요청을 만들지 않습니다.
- **AI에게 제안받기**는 `draft_mode="SUGGEST"`, `target_fields=["필드명"]`, `previous_draft`를 보냅니다. 해당 항목만 미확인 AI 제안으로 채우며 이미 확인·수정한 값은 보존합니다.
- 예전 카드 보완 클라이언트의 명시적 `target_fields` 요청은 SUGGEST로 호환합니다. 기존 LEGACY/PROACTIVE 코칭과 복합 workflow API도 유지하지만 기본 화면은 자동 B→C를 실행하지 않습니다.
- 일반 LIVE 사용은 열려 있습니다. 캐시·중복 방지는 유지하며 대량 평가는 명시적 작업으로만 실행합니다.

검증 결과: 로컬145개 통과, A/B/C LIVE120개 API·코드 검사 통과, 실제 A3턴→B→C 흐름 통과. 사람 의미 검토와 SW 직접 인수는 대기입니다. [검증 기록](VERIFICATION.md)에 버전별 범위와 호출량을 구분했습니다.

## 1. 책임과 전체 흐름

| 담당 | 책임 |
|---|---|
| M0 | DeepSeek 통신, 오류·사용량·실행 추적, 호출 한도, 선택적 캐시 계약 |
| M1 | 공동 코칭, 분석, 목표 초안, 개인 진단·계획, 코드 검증 |
| SW팀 | 인증·그룹 소속·공개 범위 권한·DB·사용자 선택·투표·계획 승인·알림 |
| 테스트 도구 | SQLite 자동 저장, 입력/응답 확인, 합성 예제, 개발자 로그 |

**공개 대화 → A 코칭(선택) → B 분석 → C AI 초안 → 사람 검토 → 공동 명세 → D 공동 계획 → E 본인 코칭/기여 목표 검토 → 개인 계획 → F 검증 → 평가 기준 확인 → SW 저장/승인**

B의 MISSING은 대화에 없는 항목이라는 뜻입니다. C에서도 미정으로 유지하며 사용자가 요청하면 AI가 제안할 수 있습니다. AI 제안은 사람 확인 전 확정 조건이 아닙니다. 개인 조건이 없어도 공동 계획은 진행할 수 있습니다. 계획 생성·저장은 서비스 활성화와 다릅니다.

[단계별 요청·응답·저장 계약](SW_INTEGRATION.md) / [M2 계약](SW_M2_CONTRACT.md)

## 2. 설치와 키 없는 로컬 실행

Python 3.11 이상. 아래 명령의 작업 디렉터리는 `ai-core/`입니다.

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install -e ".[web,test]"
$env:WEMEET_OFFLINE = "1"
python examples/sw_quickstart.py --output runs/sw-quickstart
python -m pytest
```

Linux/macOS는 `source .venv/bin/activate`를 사용합니다. 키 없이 실행 예제와 로컬 계약 검사를 수행할 수 있습니다. 예제는 `OfflineFixtureClient`를 주입하며 **REPLAY / SCRIPTED_FIXTURE**, 실제 HTTP·토큰 0으로 표시합니다. 이것은 새 프롬프트의 실제 품질 검증이 아닙니다.

예제는 9개 실행 단계의 입력·응답, `group-public.json`, `personal-private.json`, `m2-reference.json`, `report.json`을 생성합니다. 튜토리얼의 확인은 작성된 테스트 입력의 모의 사용자 확인이며 사람 검토 통계·운영 승인을 만들지 않습니다.

## 3. 실제 모델 설정과 최소 Python 사용법

일반 LIVE 코칭·분석은 사용할 수 있습니다. 아래 설정과 호출 예시는 SW 연동용이며 **사용자가 실행할 때 유료 호출이 발생**합니다. 키는 서버 환경변수/비밀 관리 시스템으로 주입합니다. 파일·브라우저·프론트 환경변수에 넣지 않습니다.

| 환경변수 | 기본값 / 의미 |
|---|---|
| `DEEPSEEK_API_KEY` | 서버에서만 주입, 누락 시 AUTH_FAILED |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `WEMEET_OFFLINE` | 일반 사용은 `0`, 로컬 전용 검사에서만 `1`로 차단 |
| `WEMEET_DB_PATH` | 테스트 어댑터 SQLite 경로 |
| `WEMEET_SCENARIO_DB_PATH` | 시나리오/평가 저장소를 별도 지정할 때 사용 |

```python
import json
from wemeet_ai import GoalAI
from wemeet_ai.cache import MemoryResultCache
from wemeet_ai.contracts import AnalysisRequest

cache = MemoryResultCache()  # 앱 수명 동안 공유. 요청마다 새로 만들면 재사용 불가.
ai = GoalAI(cache_backend=cache, cache_scope="authenticated-workspace-id")
with open("conversation.json", encoding="utf-8") as f:
    request = AnalysisRequest.model_validate(json.load(f))
result = ai.analyze_conversation(request)
if result.status == "OK":
    analysis = result.data  # SW가 분석 버전으로 저장. 승인 사실이 아님.
elif result.status == "NEEDS_INPUT":
    print(result.questions)  # 누락을 사용자에게 안내
else:
    print(result.error.code, result.error.retryable)  # 상태 보존 후 해당 단계만 재실행
```

예제의 `conversation.json`은 SW 연결 안내 또는 테스트 시나리오의 `group_input`을 사용합니다. [키 없는 전체 실행 예제](examples/sw_quickstart.py)는 코칭→분석→초안→명세→공동 계획→개인 코칭/계획→검증→M2 참조를 실제 같은 코어 함수로 연결합니다.

`GoalAI()`만 사용할 수도 있습니다. 캐시는 선택적 주입이며 코어가 DB를 직접 읽지 않습니다. `cache_scope`는 SW 서버의 인증된 작업 범위여야 합니다. 브라우저가 보낸 닉네임이나 임의 ID를 운영 권한으로 사용하지 마세요.

## 4. 공통 응답과 상태 처리

| 필드 | 처리 방법 |
|---|---|
| `status=OK` | 작업 실행 성공. `data.valid`, `contract_issues`, 카드 검토 상태도 확인 |
| `status=NEEDS_INPUT` | 정상 정보 부족. `questions`를 표시하고 입력 보완 |
| `status=FAILED` | `error.code/message/retryable` 확인. 이전 성공 결과는 보존 |
| `meta.run_id` | 이 실행의 ID. SW 저장·추적용 |
| `meta.execution_source` | LIVE / CACHE / CODE / REPLAY |
| `meta.usage`, `transport_attempts` | 이번 실행의 신규 토큰·실제 HTTP 시도 수 |
| `meta.source_run_id`, `cached_usage` | 캐시 원본과 원래 사용량. 신규 청구량에 중복 합산 금지 |
| `traces` | 현재 실행의 상세 내역. 기본 저장에서는 원문 상세 로그를 보관하지 않음 |

`validate_plan()`은 검사 실행이 성공하면 status=OK이며 **data.valid가 합격 여부**입니다. 부분 형식 오류는 정상 필드와 함께 `field_errors`/`contract_issues`로 반환할 수 있습니다. 유효하지 않은 값을 확정 조건으로 저장하지 않습니다.

일시적 전송 실패만 자동 재시도합니다. JSON 보정 1회·계획 보정 1회를 포함해 **모듈 실행당 실제 HTTP 최대 4회**입니다. 복합 B→C는 서로 다른 모듈이므로 각 단계의 한도가 적용됩니다. `provider-probe`는 명시적 연결 확인용이며 캐시하지 않습니다.

주요 코드: `AUTH_FAILED`, `TIMEOUT`, `NETWORK_ERROR`, `RATE_LIMITED`, `UPSTREAM_ERROR`, `INVALID_REQUEST`, `EMPTY_RESPONSE`, `TRUNCATED_OUTPUT`, `INVALID_JSON`, `SCHEMA_INVALID`, `PLAN_INVALID`, `RETRY_BUDGET_EXCEEDED`, `LIVE_CALLS_PAUSED`, `CACHE_UNAVAILABLE`. 실패를 mock으로 자동 대체하지 않습니다.

## 5. 사용자 확인·공개/개인 저장·버전

- A 추천안은 PENDING. 한 사람의 수락을 그룹 전체 합의로 저장하지 않습니다.
- C는 EXTRACTED / AI_SUGGESTED / UNRESOLVED와 PENDING / ACCEPTED / EDITED를 구분합니다. 저장된 사용자 수정값을 AI 보완이 덮어쓰지 않습니다.
- 공동 명세에는 개인 프로필이 필요 없습니다. 개인 계획에는 본인의 확인된 수준·시간 예산이 필요합니다. 모름·건너뛰기·제약 없음은 서로 다릅니다.
- 그룹 AI 요청에는 개인 조건을 넣지 않습니다. 개인 요청에는 공개 공동 명세·본인 역할·본인 대화만 넣습니다.
- `MemberPlanDraft.public_plan`만 그룹 공개 대상으로 검토합니다. `private_notes`, 개인 대화·제약은 본인 비공개 저장소에 둡니다.
- 수정은 새 plan_id와 previous_plan_id로 연결합니다. 과거 평가 기준은 덮어쓰지 않습니다.
- 기본 상세 로그에는 키·인증 헤더를 남기지 않습니다. 로컬 테스트 닉네임 선택은 운영용 개인 정보 접근 통제가 아닙니다.

## 6. M2 연결

`build_baseline(BaselineRequest(...))`는 명시적 확인 후 검증된 계획을 기준으로 평가 기준을 만듭니다. `activation=LOCAL_EVALUATION_REFERENCE_ONLY`는 서비스 활성화가 아닙니다. 개인 기준에는 확인된 기여 목표가 필요합니다.

```python
from wemeet_ai.baseline import ActivityReference, validate_activity_reference
activity = ActivityReference(
    plan_version_id=baseline["plan_version_id"],
    task_id=baseline["public_payload"]["tasks"][0]["task_id"],
    recorded_at="2026-10-28T12:00:00+09:00", text="담당 결과물 작업 기록",
)
validate_activity_reference(baseline, activity)
```

다른 계획 버전은 PLAN_VERSION_MISMATCH, 존재하지 않는 과제는 UNKNOWN_TASK로 거부합니다. 진행률·수치 비교는 코드/SW 책임이고 M2의 AI 평가는 이번 범위가 아닙니다. [자세한 전달 계약](SW_M2_CONTRACT.md)

## 7. 로컬 화면·시나리오·JSON

```powershell
$env:WEMEET_OFFLINE = "1"
python -m uvicorn wemeet_ai.adapters.web:app --host 127.0.0.1 --port 8010
```

`http://127.0.0.1:8010/`의 A~F는 작업, 오른쪽은 개발자 실행 내역입니다. F의 **SW 연결 안내**에서 현재 단계 입력과 Python 호출법을 확인·저장합니다. 1050px 이하에서는 두 열을 세로로 표시합니다.

**일반 LIVE 웹 사용은 WEMEET_OFFLINE=0으로 실행합니다.** 위 WEMEET_OFFLINE=1 명령은 명시적인 로컬 전용 검사 예시입니다.

**테스트 예제·JSON**과 `/scenarios`는 같은 200개(기본 100+조건 변형 100)를 사용합니다. 불러오기·검색·JSON 저장은 AI를 호출하지 않습니다. 기존 작업은 보존하고 새 작업으로 복사합니다. 기준안·AI 미실행과 LIVE 응답을 구별합니다. 상세 절차는 [SCENARIOS.md](SCENARIOS.md)를 참조하세요.

저장 작업을 복원하면 단계·대화·계획·실행 메타가 복원됩니다. 상세 로그를 미보관한 경우 다시 유료 호출해 채우지 않습니다. 개인 입력 확인·다운로드는 해당 구성원의 비공개 범위에서만 제공합니다.

HTTP 보조 연동: `/docs`, `/openapi.json`. 로컬 테스트 API에는 운영 인증이 없으므로 그대로 외부 공개하지 않습니다.

## 8. 캐시·새 안 생성·실제 사용량

- 선택적 코어 `ResultCache` 계약: get / put / lock. 기본 미주입 시 이전 동작 유지.
- 테스트 웹/CLI는 SQLite 어댑터를 통해 검증된 성공 결과를 24시간 재사용합니다. SQLite 잠금 병합은 한 서버 프로세스 범위입니다. 운영 다중 프로세스에서는 SW가 분산 잠금 구현을 제공합니다.
- 키는 작업·정규화 입력·모델/주소·프롬프트·검증기·참고 데이터와 작업/개인 범위를 포함합니다. 내부 자격증명 구분값은 브라우저로 전달하지 않습니다.
- 실패, 검증 오류, 부분 계약 오류는 성공 캐시에 넣지 않습니다. 개인 변경으로 공동 계획이나 다른 사람 계획을 불필요하게 다시 만들지 않습니다.
- **새 안 생성**은 명시적으로 재호출합니다. Python은 `force_refresh=True`, HTTP는 `X-Force-Refresh: true`, CLI는 `--fresh`입니다.
- 같은 강제 요청의 전송 중복에는 동일 `idempotency_key` / `X-Idempotency-Key`를 사용하세요. 다른 입력에 같은 키를 재사용하지 마세요.
- CACHE의 신규 토큰·시도는 0입니다. 부모 워크플로와 자식 실행의 토큰을 중복 합산하지 않습니다. 계정 청구 금액을 추산하지 않습니다.

```powershell
wemeet-ai analyze --input conversation.json --db runs/workspace.sqlite3
wemeet-ai analyze --input conversation.json --db runs/workspace.sqlite3 --fresh
wemeet-ai prepare-group --input reviewed-group.json
wemeet-ai validate --input draft.json
```

## 9. 실패 복구와 문제 해결

| 현상 | 확인·복구 |
|---|---|
| 키 없음 / 인증 실패 | SW 서버 환경변수 확인. 키 원문을 로그·채팅에 붙이지 않음 |
| LIVE_CALLS_PAUSED | 현재 추가 호출 보류 정책. 로컬 예제·저장 응답 재검사 사용 |
| MISSING이 많음 | C 초안 작성→AI 제안 검토. 수치 목표가 없는 프로젝트는 결과물 기준 사용 |
| 카드 형식 오류 | 카드 안에서 수정·확인. 정상 카드와 편집 내용은 유지 |
| 개인 조건 부족 | E에서 본인 대화/버튼으로 보완. 다른 사람·공동 계획은 유지 |
| 이전 입력 기준 | 변경된 입력에서 해당 단계와 관련 후속 단계만 갱신 |
| 상세 로그 미보관 | 실행 메타·저장 결과 확인. 로그만 얻기 위한 재호출 금지 |
| 저장 충돌 409 | 저장 작업을 다시 불러와 최신 revision 기준으로 수정 |
| CACHE_UNAVAILABLE | 중복 유료 호출을 막기 위해 실행하지 않음. 캐시 저장소를 복구한 뒤 재시도 |
| RETRY_BUDGET_EXCEEDED | 기존 결과 보존·입력/계약 점검. 무한 자동 재실행 금지 |
| 예제 목록 연결 실패 | 목록 재시도. 기존 작업과 기존 5개 회귀 예제는 유지 |

## 10. 검증 범위와 한계

아래 v0.8 검증 수치는 보존된 과거 기록입니다. v0.9는 A/B/C 120개 단일 개선 설정 LIVE 평가와 별도 자연어 대화 흐름을 검증하며, 실행 결과는 VERIFICATION.md에서 구분해 기록합니다.

저장된 기존 LIVE 평가는 200개×2설정=400개 조합, API 완료 323개·실패 77개·코드 검사 통과 281개입니다. API 완료를 품질 통과로 표시하지 않습니다.

```powershell
python examples/recheck_saved.py --db runs/scenarios-live.sqlite3 --output runs/v8-stored-recheck.json
```

원본 결과를 읽기 전용으로 재검사합니다. 현재 저장된 B 분석 79개를 재생했고, 미정 공개 범위가 CUSTOM으로 잘못 남은 40건을 코드 수정 후 0건으로 확인했습니다. 실패한 원본 모델 본문이 없으면 재현 불가로 남깁니다. 원본 LIVE 통계·사람 검토 수를 수정하지 않습니다.

v0.8 당시 검증은 추가 LIVE 호출 0회였습니다. 이 과거 기록은 v0.9의 LIVE 호출을 포함하지 않습니다. 로컬 회귀·화면·패키지 연결 검증은 실제 모델 품질이나 SW팀의 직접 인수 검증을 대신하지 않습니다. 승인된 참고가 없으면 참고 학습 효과를 주장하지 않습니다. 모델 가중치 학습은 수행하지 않습니다.
