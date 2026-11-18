# ARCHITECTURE

현재 확정된 구조와 데이터·API·AI 경계. 이 문서의 `모듈 판정표`가 `src/wemeet_ai` 재사용 판정의 단일 원본이다. 판정은 에이전트의 PROPOSED 값(2026-11-18, 0A-1)이며 0B-1에서 크리퍼의 기존 테스트를 실행한 결과로 다시 확인하고 필요하면 변경한다. 같은 역할을 하는 구조를 두 벌 두지 않는다. REPLACE·DROP은 실행 경로에서 제외하고 KEEP은 같은 역할의 새 구조를 만들지 않는다.

## 1. 구현 기본 방향 (APPROVED DIRECTION, bootstrap §4 이관)

- Python 3.12
- FastAPI와 Jinja2
- PostgreSQL
- SQLAlchemy와 Alembic
- vanilla JavaScript
- UTC timestamp 저장, Asia/Seoul 표시
- 페이지 골격과 최초 데이터는 Jinja2
- AI 작업과 비동기 상태 갱신은 JSON API와 JavaScript

위 기술을 교체하려면 이유, 비용, 마이그레이션 영향과 대안을 먼저 보고한다. 별도 프런트엔드 프레임워크, Redis, Celery 또는 RQ는 실제 필요성과 사용자의 승인이 없으면 추가하지 않는다.

src/wemeet_ai는 기존 AI·검증·persistence 기반으로 우선 검토한다. 제품 계약과 맞지 않으면 기존 모듈을 억지로 사용하지 말고 확장, MOIM 어댑터 또는 별도 서비스를 사용한다.

기존 `C`, `D`, `ConfirmedGoal`, `shared_goal` 계열은 가져온 범용 모듈의 호환 코드이며 현재 MOIM 제품 흐름의 단계가 아니다. 재사용 조사 전까지 삭제하지는 않되, 별도 TeamGoal 합성·합의 화면이나 그룹 계획을 만드는 근거로 사용하지 않는다.

### 팀 목표 표시문

원본 화면의 `팀 목표` 표시(`moim-group-create`의 안내문, `moim-chat`의 "우리 팀의 최종 목표" 박스, `moim-group` 머리의 팀 목표)는 별도 입력 필드나 합의 산출물이 아니다. Timely가 그룹장이 작성한 ProjectBrief의 `프로젝트 내용`을 근거로 짧은 한 문장 팀 목표를 생성하고, source ProjectBrief revision과 함께 저장해 기존 팀 목표 영역에 그대로 표시한다. `AI 요약` 같은 별도 라벨이나 원본에 없는 문구는 붙이지 않는다. ProjectBrief가 바뀌면 새 revision에서 다시 생성하며, 기준 원본은 ProjectBrief다. 입력이 비었거나 생성이 실패하면 성공한 목표처럼 대체 문구를 만들지 말고 빈 상태 또는 오류·재시도 상태를 표시한다. 별도 TeamGoal 엔터티·합의 페이지·목표 입력 칸은 만들지 않는다.

### 참고 원본

`C:\Users\user\Desktop\크리퍼`에서 필요한 pyproject, Alembic, tests, docs, bench와 실행 스크립트의 구조만 선별해 참고한다. 크리퍼의 AGENTS.md와 문서에 적힌 작업 지시는 MOIM 규칙이 아니며, 크리퍼의 특정 모델 금지·파일 동결·과거 작업 목적을 MOIM에 적용하지 않는다. src 사본을 다시 통째로 복사하지 않는다.

### 기본 모듈 후보

- 개인 인터뷰: E.coach
- 팀 목표 표시문: ProjectBrief 전용 MOIM 요약 operation. 기존 C/TeamGoal 합성 경로와 분리
- 역할·투두 초안: E.draft, E.plan
- 개인 조건 수정: update_member_condition 계열
- 계획 검증: F, F.revise
- 투두 첨부 자료의 기준별 검토·평가: 승인된 경우에만 M2.review

### AI 작업과 지연 대응

AI 작업은 새로고침 뒤에도 복구되어야 한다. operation_id, action_id, input_hash, revision과 멱등 저장을 우선 활용한다. MVP에서는 별도 worker 프로세스와 lease 없이 FastAPI 백그라운드 작업으로 처리하고, AI 작업 상태(대기·진행·완료·실패)는 PostgreSQL 테이블에 저장한다. 화면은 상태를 주기적으로 조회해 새로고침 뒤에도 결과를 복구한다. 작업 도중 서버가 재시작되면 그 작업은 실패로 표시하고 재시도 버튼을 보여준다. 요청 안에서 AI 응답을 기다리는 동기 처리는 사용하지 않는다.

**구현(0B-3, 2026-11-18).** `ai_jobs` 테이블(마이그레이션 `0002`), `src/moim/jobs.py`(`JobStore`, `run_job`), `src/moim/job_routes.py`.

- 상태 흐름: `PENDING → RUNNING → SUCCEEDED | FAILED`, `FAILED → PENDING`(재시도, `attempt` +1). 모든 전이는 `id + revision + 현재 상태` 조건의 단일 UPDATE이고 성공할 때마다 `revision`이 1 올라간다. 조건이 맞지 않으면 `RevisionConflict`(API 409).
- API: `POST /api/jobs`(`operation_id`, `kind`, `input`) → 202, `Location`과 `status_url`. 같은 `operation_id`와 같은 입력의 재요청은 새 작업을 만들지 않고 200으로 기존 작업을 돌려준다. 같은 `operation_id`에 입력이 다르면 409. `GET /api/jobs/{id}`로 상태와 결과 조회(저장한 입력은 돌려주지 않는다). `POST /api/jobs/{id}/retry`(`revision` 필요).
- 실행: FastAPI `BackgroundTasks`. worker 프로세스, lease, 큐 의존성은 없다. 핸들러는 `create_app(job_handlers={kind: fn})`으로 등록하며, 등록하지 않은 `kind`는 422다. 핸들러 예외는 `JobError(code, message)`면 그대로, 그 밖에는 `HANDLER_ERROR`와 고정 문구로 저장한다(예외 원문은 저장·기록하지 않는다).
- 서버 시작(lifespan)에서 `PENDING`·`RUNNING`으로 남은 작업은 `FAILED / SERVER_RESTARTED`로 바꾼다. 같은 DB를 보는 서버가 둘이면 서로의 진행 중 작업을 실패로 바꿀 수 있으므로 MVP는 서버 프로세스 하나를 전제한다.
- 화면 조회 보조: `src/moim/static/job-poller.js`(`MoimJobs.pollJob`, `renderJobState`). 출력은 `textContent`만 쓰고 저장소·쿠키를 쓰지 않는다. 상태 URL을 페이지가 들고 있으면 새로고침 뒤 같은 작업에 다시 붙는다.
- 로그: `moim.jobs` 로거에 job_id·kind·state·attempt만 기록한다. 입력과 결과는 기록하지 않는다.

AI가 DB를 직접 임의 수정하지 않는다. 구조화된 변경안, 권한·revision·기간 검증, 사용자 확인과 저장 성공을 거친다.

- 긴 AI 작업은 입력 저장 → operation 발급 → 빠른 202와 상태 URL → 백그라운드 작업 실행 → 상태 조회·복구 흐름을 기본으로 한다. 구간별 지연 측정은 MVP 범위에서 하지 않는다.
- 사용자 메시지 한 번은 Timely 호출 한 번으로 처리하고 자동 재시도는 하지 않는다. E.coach와 E.draft 같은 기능 연쇄 호출은 별도 사용자 동작과 별도 operation으로 분리한다.
- 전체 대화를 매번 보내지 않고 구조화된 MemberState, 이전 요약과 필요한 최근 메시지만 보낸다.
- 0B 측정 전에는 현재 기본 max_tokens 1500을 유지하고, 모듈별 상한은 출력 완전성과 안정성을 먼저 충족한 뒤 지연·비용 측정으로 확정한다. 토큰 절약 때문에 정상 출력을 잘라서는 안 된다.

## 2. 모듈 판정표

판정: KEEP(그대로) / ADAPT(어댑터 또는 내부 수정) / REPLACE(새로 구현) / DROP(사용 안 함). 줄 번호는 2026-11-18 기준 `src/wemeet_ai/` 아래 경로다. "테스트"는 크리퍼 `tests/`에 있는 파일이다. 0B-1에서 격리한 복사본으로 실행한 결과는 이 절 끝의 `0B-1 재확인 결과`에 있다(이 저장소의 `tests/`는 MOIM 자체 테스트다).

| 모듈 | 판정 |
|---|---|
| M0 (`m0/client`·`runtime`·`tracing`·`records`) | **ADAPT** |
| M0의 `versions`·`cache` | 실행 경로 제외 (M0 판정에 포함) |
| M1-A `a_coaching` | **DROP** |
| M1-B `b_analysis` | **DROP** |
| M1-C `c_goal_draft` | **DROP** |
| M1-D `d_group_plan` | **DROP** |
| M1-E `e_member_plan` | **ADAPT** |
| M1-F `f_validation` | **KEEP** |
| M2 `m2/` | **KEEP** |
| persistence `persistence/` | **REPLACE** |
| 보조 `shared/` | KEEP |
| 보조 `continuity.py`·`cli.py` | DROP |

**0B 보정 결과 (2026-11-18):** 위 표의 DROP·실행 제외 모듈(A·B·C·D, `persistence/`, `continuity.py`, `cli.py`, `m0/versions.py`, `m0/cache.py`)은 소스에서 제거했고 KEEP·ADAPT 모듈(`m0`, `m1/e_member_plan`, `m1/f_validation`, `m2`, `shared`)만 남았다. 이 문서의 아래 모듈별 서술에 있는 줄 번호와 제거된 모듈의 설명은 기준선(Git `a9a9721`) 시점의 기록이다. 제거 확인은 `tests/test_packaging.py`.

### M0 — ADAPT

- 계약 일치: `Result`·`Meta`·`Usage`·`AIError`와 JSON 스키마 검증(`m0/client.py:57-81`)은 맞다. 불일치는 자동 재시도 세 곳(`client.py:63` JSON 복구 재호출, `client.py:91` 전송 재시도, `runtime.py:66` 검증 재호출)이다. "사용자 메시지 한 번에 Timely 호출 한 번, 자동 재시도 없음"과 충돌한다. `runtime.py:71`이 JSON 작업에 `max_tokens` 4096을 넘겨 `client.py:89`의 1500을 덮어쓴다(문서 전제와 다름).
- 개인정보 경계: `sanitize`(`shared/privacy.py`)와 `scrub`(`m0/tracing.py:29`)이 키·이메일·휴대폰·이미지 data URL을 가린다. 다만 `provider.request/response`가 본문 전체를 emit하고(`client.py:99,105,126,130`) `json.parsed`가 파싱 결과를 emit한다(`client.py:67`). 디버그 sink에서만 켜지므로(`runtime.py:58`) 기본값은 안전하나 LIVE 규칙은 원문 로그를 금지한다.
- 테스트: `test_m0.py` 30개 통과(0B-1 실행). 판정 변경 없음.
- 복잡도: 약 430줄이며 `client.py`가 166줄이다.
- 수정 비용: 낮다. 해당 줄 제거와 호출 로그 한 줄 추가(0B-2).
- 실행 경로 제외: `versions.py`는 디스크가 바뀌면 `SOURCE_RELOAD_REQUIRED`를 반환한다(`runtime.py:27`). 개발 서버와 충돌하므로 제외한다. `cache`는 `cache=None`으로 쓰지 않는다.

### M1-A — DROP

- 계약 일치: 공개 대화를 코칭하는 공동 목표 코치(`m1/a_coaching/__init__.py`의 `POLICY`)다. MOIM은 공개 팀 대화가 없고 개인 AI 대화(E.coach)만 쓴다.
- 개인정보 경계: 공개 대화 전제라 private 경계 설계와 반대 방향이다.
- 테스트: `test_modules.py`에 포함. 가져올 대상이 아니므로 확인하지 않는다.
- 복잡도: 49줄. 수정 비용: 새 계약으로 바꿀 이유가 없어 사용하지 않는 편이 싸다.

### M1-B — DROP

- 계약 일치: 공개 메시지를 분석해 팀 목표 사실을 만든다(`b_analysis`, `persistence/execution.py`의 `public_messages` 루트). MOIM에는 해당 단계가 없다.
- 개인정보 경계: 공개 대화 입력 전제.
- 테스트: `test_modules.py`에 포함. 미확인.
- 복잡도: 83줄. 수정 비용: 의미 변경이 필요해 재사용 이득이 없다.

### M1-C — DROP

- 계약 일치: `GoalDraft`·`ConfirmedGoal` 합성 경로(`c_goal_draft`)로, 헌법 영역 `1`·`2`와 이 문서가 금지한 TeamGoal 합성 근거다. 팀 목표 표시문은 ProjectBrief 전용 새 operation으로 만든다.
- 개인정보 경계: `privacy_scope` 합의 모델을 전제한다. MOIM의 공개 범위 규칙과 다르다.
- 테스트: `test_kickoff*.py`·`test_modules.py`에 포함. 미확인.
- 복잡도: 141줄. 수정 비용: 재작성에 가깝다.

### M1-D — DROP

- 계약 일치: `ConfirmedGoal`로 그룹 계획을 만든다(`d_group_plan`). MOIM에는 그룹 계획이 없고 역할 배정 후 개인 투두만 만든다.
- 개인정보 경계: 공동 계획에 개인 소유자를 금지(`d_group_plan`의 `check`)하는 구조라 개인 투두 흐름과 맞지 않는다.
- 테스트: `test_kickoff_plans.py`·`test_modules.py`. 미확인.
- 복잡도: 32줄. 수정 비용: 불필요.

### M1-E — ADAPT

- 계약 일치: 개인 `MemberState`, `E.coach`·`E.draft`·`E.plan`, `update_member_condition`이 MOIM의 개인 인터뷰와 투두 초안에 대응한다. `Task`는 `deliverable`·`completion_criteria`를 이미 가진다(`shared/contracts.py`). 입력이 `GoalContext`·`Contribution` 기준이라 ProjectBrief와 최종 배정표를 변환하는 MOIM 어댑터가 필요하다. 배정된 역할은 현재 입력이 아니다.
- 개인정보 경계: `MemberCoachRequest.private_owner`가 소유자 외 메시지·상태를 거부하고, `generate_member_plan`이 `constraints`·`private_notes`를 제거한 뒤 생성한다(`e_member_plan`). 경계 설계가 MOIM과 맞는다.
- 테스트: `test_modules.py` 50개 통과(0B-1 실행, A~F 전체 포함). 판정 변경 없음.
- 복잡도: 373줄로 가장 크다.
- 수정 비용: 중간. 어댑터를 새로 쓰고 내부는 유지한다. 일부(`E.plan` 뒤쪽)만 읽고 판정했으나 기존 테스트 통과로 동작은 확인했다. 코드 정독은 0B-2 이후 단계에서 어댑터를 쓸 때 보완한다.

### M1-F — KEEP

- 계약 일치: LLM 호출이 없는 결정적 검증 `inspect_plan`(기간, 중복, 의존, 순환, 시간 예산). MOIM 4단계의 기간·업무량·담당자 검증에 그대로 대응한다. ProjectBrief revision과 팀 결과물 중복·누락 검사는 새 코드다. `F.revise`(`revise_plan`)는 정독하지 못했다.
- 개인정보 경계: 입력된 계획만 다루고 외부 호출이 없다.
- 테스트: `test_modules.py`에 포함되어 통과(0B-1 실행). 판정 변경 없음.
- 복잡도: 103줄. 수정 비용: 낮다.

### M2 — KEEP

- 계약 일치: `TaskSnapshot`이 MOIM 투두 계약과 같고, `review_completion`의 `_validate_review`가 기준 개수·순서·근거 참조·`decision` 일관성을 결정적으로 검사한다(`m2/completion_review.py`). `PASS`/`REVIEW_NEEDED`/`NEEDS_INPUT`이 MOIM 정의와 같다. GATE D 승인 전에는 실행 경로를 비활성으로 둔다.
- 개인정보 경계: 이미지 data URL 마스킹(`m0/tracing.py:29`), 프롬프트가 성실성·정직성·투표 판단을 금지(`m2/prompts.py`). 첨부 파일 보관은 GATE D 결정 전까지 확정하지 않는다.
- 테스트: `test_m2.py` 7개, `test_m2_evidence.py` 14개 통과(0B-1 실행). 판정 변경 없음.
- 복잡도: `evidence.py` 439줄이 가장 크며 PyMuPDF·Pillow·python-docx에 의존한다.
- 수정 비용: 낮다. 허용 범위는 GATE D의 결정(이미지·PDF·DOCX)에 맞춰 조정한다.

### persistence — REPLACE

- 계약 일치: 스키마가 `groups`·`members`·`shared_goals`·`member_goals`·`plan_versions` 중심이다(`persistence/schema.py:10-57`). `users`·세션·초대방·ProjectBrief·assignment revision이 없고 `ConfirmedGoal`에 묶여 있다(`repository.py:9,51`). `completion_appeals`·`completion_votes`(`schema.py:124,138`)의 투표는 MOIM에 없다(GATE D는 사용자 이의 제기만 다룬다).
- 개인정보 경계: `private_profiles`와 record scope 설계는 참고할 가치가 있으나 그룹 공유 모델이 다르다. 복구 저장소는 Fernet 파일이라(`recovery.py:8`) "AI 작업 상태는 PostgreSQL 테이블" 방침과 다르다.
- 테스트: `test_goal_database.py` 10개, `test_completion_repository.py` 6개, `test_records_api.py` 10개, `test_unified_storage.py` 10개 통과·1개 실패(실패 사유는 `0B-1 재확인 결과`). 이 테스트들이 통과해도 REPLACE 판정은 바뀌지 않는다. 스키마가 MOIM과 다르다는 것이 판정 근거다.
- 복잡도: 약 1,300줄. `ExecutionAdapter`(`execution.py`)의 operation_id·action_id·input_hash·revision 멱등 패턴과 10분 이상 RUNNING 만료(`execution.py:73`)는 설계 참고용이다.
- 수정 비용: 높다. 새 MOIM 스키마와 Alembic 마이그레이션을 새로 만들고 기존 persistence는 실행 경로에서 제외한다.

### 보조 모듈

- `shared/`: KEEP. `Message`·`Task`·`Plan`·`Result`와 `privacy`는 범용이다. `ConfirmedGoal` 호환 코드는 쓰지 않는 영역으로 격리한다.
- `continuity.py`(261줄): DROP. `RecordView` 중심이며 persistence와 함께 제외한다.
- `cli.py`: DROP. `registry`가 C·D를 포함한다. 필요하면 MOIM용 얇은 실행기를 새로 만든다.

### 0B-1 재확인 결과 (2026-11-18)

실행 방법: 크리퍼를 원본이 아닌 임시 복사본으로 옮겨 실행했다. 원본 폴더와 크리퍼의 실제 DB(포트 55432)는 건드리지 않았다. 복사본에서만 다음을 바꿨다: `tools/db/cutover.py`의 `winreg`를 끄고, 레지스트리를 직접 읽던 테스트 3개(`test_completion_api`, `test_completion_repository`, `test_goal_database`)가 환경변수 `WEMEET_GOAL_DATABASE_URL`을 읽게 했다. 이 값은 MOIM 개발 PostgreSQL(127.0.0.1:54329)의 별도 DB `creeper_scratch`를 가리킨다. 복사 때 빠진 `evaluations/scenarios.jsonl`만 원본에서 읽기 전용으로 가져왔다.

| 구분 | 결과 |
|---|---|
| 실행 | 225개: 223 통과, 2 실패, 건너뜀 0 |
| 제외 | `test_kickoff_completion_browser.py`(Playwright 브라우저 필요). 미실행 |
| 판정 변경 | 없음 |

실패 2개:

- `test_bench.py::test_source_status_has_no_model_call_or_key_material`: 모듈 집합에 `M2.review`가 더해져 기대값 `{A,B,C,D,E.coach,E.draft,E.plan,F}`와 다르다. 코드가 테스트보다 앞서 간 크리퍼 쪽 낡은 테스트다. 그대로 두며 MOIM 판정(M2 KEEP, A·B·C·D DROP)에는 영향이 없다.
- `test_unified_storage.py::test_app_cannot_delete_snapshot_or_create_schema`: 앱 DB 역할이 `(superuser, createdb, createrole) == (False, False, False)`이어야 하는데 개발 DB의 `moim` 역할이 모두 True다. 크리퍼가 전제한 비권한 앱 역할이 이 환경에 없어서 생긴 환경 차이다. MOIM의 앱 DB 역할 분리는 `docs/SECURITY.md`와 07 단계에서 따로 다룬다.

## 3. 알려진 위험 (구현 단계에서 처리)

| 위험 | 위치 | 처리 단계 |
|---|---|---|
| provider 요청·응답·파싱 원문 emit | `m0/client.py` | 0B-2에서 해결(2026-11-18): 크기·해시·ID만 기록. `docs/LIVE_TESTING.md` 4-1 |
| 자동 재시도 세 곳 | `m0/client.py`, `m0/runtime.py` | 0B-2에서 해결(2026-11-18): 모두 제거, 호출 1회. 이후 줄 번호는 위 서술과 다르다 |
| 소스 변경 시 `SOURCE_RELOAD_REQUIRED` | `m0/runtime.py:27`, `m0/versions.py` | 0B 보정(2026-11-18)에서 해결: `versions.py`·`cache.py` 제거, `Runtime`에서 게이트·캐시 삭제 |
| 기본 모델·URL이 코드 상수 | `m0/client.py` | 0B-2에서 LIVE로 확인(2026-11-18): 옛 주소가 종료되어 기본값을 직접 API 주소와 `gpt-5.6-luna`로 바꿨다. `TIMELY_BASE_URL`·`TIMELY_MODEL` 환경변수로 덮어쓴다 |
| 원본 HTML의 `innerHTML`·`localStorage` 데모 | `docs/PRODUCT.md` 2절 | 구현본에 복사하지 않음 |
