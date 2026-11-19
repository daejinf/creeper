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

- 개인 인터뷰: E.coach(D-B3: 3단계에서 호출하지 않음, 4단계의 배정 뒤 인터뷰에서 재사용)
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

### 인증 (1단계)

- 모듈: `src/moim/auth/`의 `validation`(입력 규칙), `passwords`(Argon2id), `sessions`(서버 세션), `throttle`(DB rate limit), `csrf`, `errors`(`{"error": {code, message, field?}}`), `middleware`(보안 헤더·본문 상한), `deps`(`current_user`·`require_user`·`verify_csrf`, 이후 단계가 재사용), `routes`.
- 테이블(마이그레이션 `0003_auth`): `users`, `auth_sessions`(토큰은 sha256만), `auth_throttle`.
- 화면: 원본 landing·signup·login을 `src/moim/templates/`로, 그 안의 인라인 스크립트를 `src/moim/static/*.js`로 옮겼다(CSP `script-src 'self'`). 원본 HTML은 읽기 전용이며 템플릿과 별개 파일이다. 원본과 달라진 곳은 링크(`/`, `/login`, `/signup`), 폼 속성(`name`, `method`, `maxlength`), 오류·안내용 `.msg` 줄과 `:disabled` 스타일, 비밀번호 찾기 안내, 서버 호출뿐이다. 원본 `<style>`의 모든 줄은 구현 페이지에 그대로 있다(테스트가 검사).
- 라우트: `GET /`(landing, CTA는 로그아웃이면 `/login`, 로그인이면 `/dashboard`), `GET /login`, `GET /signup`(로그인 상태면 `/dashboard`로 303), `GET /dashboard`(**2단계에서 대체될 임시 화면**: 로그인 확인과 로그아웃 버튼만 있다, 로그아웃 상태면 `/login?next=/dashboard`), `POST /api/auth/{username-check,signup,login,logout}`.
- 로그인 뒤 이동 경로 `next`는 같은 사이트 절대 경로만 허용하고 그 밖은 `/dashboard`.
- 가입은 성공하면 바로 로그인된다.

AI가 DB를 직접 임의 수정하지 않는다. 구조화된 변경안, 권한·revision·기간 검증, 사용자 확인과 저장 성공을 거친다.

- 긴 AI 작업은 입력 저장 → operation 발급 → 빠른 202와 상태 URL → 백그라운드 작업 실행 → 상태 조회·복구 흐름을 기본으로 한다. 구간별 지연 측정은 MVP 범위에서 하지 않는다.
- 사용자 메시지 한 번은 Timely 호출 한 번으로 처리하고 자동 재시도는 하지 않는다. E.coach와 E.draft 같은 기능 연쇄 호출은 별도 사용자 동작과 별도 operation으로 분리한다.
- 전체 대화를 매번 보내지 않고 구조화된 MemberState, 이전 요약과 필요한 최근 메시지만 보낸다.
- 출력 한도는 클라이언트 상수 `DEFAULT_MAX_OUTPUT_TOKENS`(4096, `m0/client.py`) 하나다(0B 보정으로 1500에서 4096으로 바뀌었고 이 문서의 옛 값 1500은 정정했다). 3단계 LIVE에서 E.coach 한 턴이 완료 토큰 1,245~2,027개를 썼고 한도에 닿은 적은 없다. 토큰 절약 때문에 정상 출력을 잘라서는 안 된다.

### 그룹·초대방·ProjectBrief (2단계)

- 모듈: `src/moim/groups/`의 `service`(상태 전환과 인가, 모두 한 트랜잭션), `validation`(ProjectBrief 입력), `goals`(팀 목표 표시문), `routes`(화면·API).
- 테이블(마이그레이션 `0004_groups`): `groups`(`FORMING`·`ACTIVE`·`CANCELLED`, 현재 `brief_revision`·`cohort_revision`), `group_members`(`LEADER`·`MEMBER`), `invite_codes`(전역 유일, 폐기 사유 `REISSUED`·`ACTIVATED`·`CANCELLED`), `project_briefs`(revision별 행, 덮어쓰지 않음), `activation_cohorts`·`cohort_members`(활성화 시점 구성원 고정), `goal_displays`(revision별 표시문 상태). DB 제약이 규칙을 직접 막는다: 생성자당 `FORMING` 하나(부분 유니크), 그룹당 살아 있는 코드 하나(부분 유니크), `ACTIVE`는 brief·cohort revision이 1 이상일 때만.
- 흐름: dashboard/group의 `새 그룹 만들기`(버튼, 클릭 시 `POST /api/groups`) → 본인 `FORMING` 초대방 생성 또는 재사용 → `/invite`(그룹장: 코드·복사·새로 만들기·초대방 닫기·구성원·`그룹 생성하기`, 참여 대기자: 대기 안내) → `그룹 생성하기`는 `/groups/{id}/create`로 이동만 하며 상태를 바꾸지 않음(최소 2명, 그룹장만) → ProjectBrief 다음 버튼 `POST /api/groups/{id}/activate`가 revision 1 저장·cohort 고정·`ACTIVE`·코드 폐기·`goal_displays` 행 생성을 한 트랜잭션으로 처리 → 그룹장은 응답의 `redirect`로, 대기 중인 구성원은 4초 폴링으로 `/groups/{id}/chat`로 이동. 상태 확인과 폴링은 GET이며 상태를 바꾸지 않는다.
- 동시성: 상태를 바꾸는 요청은 그룹 행 `FOR UPDATE`(그룹이 둘이면 id 순서)와 사용자별 advisory lock을 쓴다. 두 번째 활성화는 `ALREADY_ACTIVE`, 마지막 자리 경쟁은 `ROOM_FULL`, 오래된 revision 저장은 `REVISION_CONFLICT`.
- 인가: 모든 객체 조회·변경은 `service`에서 멤버십을 확인하고, 비회원·다른 그룹 사용자는 없는 그룹과 같은 404를 받는다. 그룹장 전용(코드 재발급·초대방 닫기·활성화·ProjectBrief 수정·목표 다시 시도)은 비그룹장에게 403. `/groups/{id}/chat`은 `ACTIVE` cohort 구성원만 들어가며 3단계 전까지는 임시 화면이다.
- 팀 목표 표시문: `goals.generate_goal`이 M0 `TimelyClient.complete_json`을 한 번 호출(재시도·복구 호출 없음). 입력은 `프로젝트 내용`뿐이다. 결과 검사는 4~80자·한 줄이며 사실 근거는 사람이 LIVE 기록에서 본다. 상태 `EMPTY`(내용 없음)·`PENDING`·`RUNNING`·`SUCCEEDED`·`FAILED`. 화면에는 현재 revision의 상태만 보이고 이전 revision의 문장은 현재 목표처럼 쓰지 않는다. 서버가 시작될 때 중단된 작업은 `SERVER_RESTARTED` 실패로 바꾼다. 범용 `/api/jobs`는 쓰지 않는다(인증이 없는 라우트이고 소유자 개념이 없음).
- 화면: 원본 dashboard·invite·group-create·group의 CSS를 그대로 가져온 템플릿 4개와 임시 `chat_placeholder`·`notice`. 인라인 스크립트 없이 `static/{shell,dashboard,invite,group-create,group}.js`. 원본의 데모 동작(직접 group-create 링크, 로드 때 코드 생성, 가짜 구성원, `그룹 생성 완료` 팝업, position 이동, `localStorage` 로그인)은 가져오지 않았다. 실행 진행률·투두·활동·소식·오른쪽 패널은 만들지 않았다(5단계).

### 공용 역할 후보·비공개 순위 제출 (3단계, D-B3)

- D-B3(2026-11-19)로 3단계에는 개인 AI 인터뷰가 없다. 인터뷰는 배정 뒤 4단계에서 한다. 아래 서술 중 `인터뷰(D-B2 AI 주도)`·`E.coach 변경` 항목은 **사용 중단된 구현의 기록**이며 3단계 경로에서 호출되지 않는다.
- 모듈: `src/moim/interview/`의 `candidates`(그룹 역할 후보), `ranking`(`RankingService`: 화면 조회·순위 제출·인가), `routes`(chat 화면·API). 사용 중단(보존, 어떤 import도 없음): `coach`(E.coach 어댑터)와 `turns`(`InterviewTurnService`, 옛 인터뷰 턴 처리). 4단계 배정 뒤 인터뷰가 재사용한다. 화면은 `templates/chat.html`(원본 `moim-chat.html`의 `<style>` 194줄을 그대로 옮김)과 `static/chat.js`.
- API: `GET /api/groups/{id}/roles`(후보·내 순위 상태·`N/M` 집계를 한 번에), `POST /api/groups/{id}/preferences`(순위 제출), `POST /api/groups/{id}/roles/candidates/retry`. 옛 `/interview*` 경로는 없다.
- 테이블(마이그레이션 `0005_interview`, `0006`, `0007_interview_deprecated`): `role_candidate_sets`(ProjectBrief revision당 한 행, 후보 revision = `source_project_brief_revision`), `preference_submissions`(불변 revision, 1·2·3순위 서로 다름을 DB가 검사), `member_progress`(저장된 `INTERVIEW | WAITING_ASSIGNMENT`. `INTERVIEW`는 이제 "순위 미제출"이다). **사용 중단·보존:** `interview_turns`, `member_states`, `member_progress.interview_closed_at`은 삭제·변경하지 않고 DB 주석으로 DEPRECATED를 표시했다(`0007`은 주석만 바꾸고 데이터를 건드리지 않으며 downgrade로 주석만 제거). 3단계는 이 테이블을 쓰지도 읽지도 않는다.
- 순위 제출: 인터뷰·MemberState 없이 후보가 `SUCCEEDED`이면 바로 제출할 수 있다. 제출 전 선택은 화면에만 있고(자유롭게 변경) 제출이 immutable revision이다. 멤버별 advisory lock 아래서 같은 `action_id`는 재생(False), 다른 `action_id`로 이미 제출했으면 409 `ALREADY_SUBMITTED`라 동시 제출도 한 번만 저장한다.
- 역할 후보: ProjectBrief revision이 만들어질 때(활성화·수정) 같은 트랜잭션에서 `PENDING` 행이 생기고 백그라운드로 Timely를 1회 호출한다. 입력은 ProjectBrief(이름·내용·기간·결과물·중간 일정)와 cohort 인원 수(숫자만, 구성원 식별 정보 없음)다(D-B1 B5). 후보 수는 `max(3, 인원)` 이상 `+2` 이하(3~8 안)로, 한 사람이 하나씩 맡는 굵은 역할 단위를 요청한다(2026-11-19: 2명 팀에 8개처럼 잘게 쪼개지던 문제 수정). 서버가 그 범위·이름 ≤12자·설명 ≤60자·이름 중복 없음을 서버가 검사하고 id(`r1`…)는 서버가 붙인다. 성공하면 다시 쓰지 않는다. 실패는 `FAILED`와 오류, 명시적 다시 시도(`action_id`로 멱등)이며 고정 목록·이전 revision으로 대체하지 않는다. 새로고침·재접속·다른 구성원 조회는 저장된 행만 읽는다.
- (사용 중단, D-B3) 인터뷰(D-B2 AI 주도): 메시지·버튼 하나가 AI 한 번(Timely 한 번)이다. `interview/coach.py`의 `run_turn`이 `e_member_plan.interview_turn`(`TurnModel`: understood·value·quote·extras·reply·next{field,text,options})을 한 번 호출한다. AI가 답 해석과 다음 질문·버튼 작성을 맡고, 코드가 모든 부분을 검사한다: 인용은 사용자 메시지 안에 있어야 하고(문자열 값은 사용자 말과 글자쌍 50% 이상 겹쳐야 하며 아니면 인용을 저장), 숫자·목록은 사용자 말 안에 근거가 있어야 하고, 부가 `scope`는 의도 표현이 필요하며, 이미 아는 항목은 고쳐 말할 때만 바뀐다. 다음 질문은 새 항목 또는 재질문, 버튼 2~4개(값은 서버에만 저장, 브라우저는 id·문구만), 내부 표현 없음을 만족해야 하고 아니면 서버의 기본 질문(첫 누락 필수 항목)으로 바꾼다(`interview_event … ai_next_question_replaced_by_server_question` 로그). 질문·버튼 값은 `member_states.state.question`에 있다. 완료는 서버: `missing_required`(필수 4개가 알려졌거나 "모름" 두 번), 턴 한도에서는 항상 닫기 가능. 의미 없는 입력은 422 `UNCLEAR_ANSWER`로 거절한다. 옛 자유 대화 `coach_member_goal`은 MOIM이 호출하지 않는다. 속도: `Runtime(extra_options={"reasoning_effort": "low"})`(`minimal`은 공급자가 거절), 프롬프트 약 1.6k·완료 130~213 토큰.
- (사용 중단, 4단계 재사용용) E.coach 변경(ADAPT, 모듈 판정 유지): `coach_member_goal(extra_policy=)` 인자 추가(턴 예산·필수 항목·답변 길이 정책), `Runtime(extra_options=)` 추가, LIVE에서 드러난 실패를 고쳤다(질문 필드가 진단 항목에 없으면 실패 대신 그 항목을 더한다는 것 포함). ① 모델이 `AI_SUGGESTED`/공동 목표 근거로 만든 `scope` "사실"은 저장하지 않고 버린다(없는 사용자 메시지를 근거로 대는 사실은 여전히 실패). ② 사용하지 않는 답변 버튼 선택지의 값이 틀려도 그 선택지만 버리고 턴은 실패시키지 않는다. 둘 다 호출을 더 하지 않는 결정적 코드이고 저장되는 사실의 근거 조건은 그대로다.
- STALE: ProjectBrief가 바뀌어 새 revision이 생기면(`update_brief`, 같은 트랜잭션) 이전 후보는 현재가 아니므로 제공하지 않고, 그 후보를 참조해 `WAITING_ASSIGNMENT`이던 구성원은 `INTERVIEW`(순위 미제출)로 돌아가 새 후보에서 다시 고른다(이전 제출 행은 기록으로 남는다).
- 복구: 서버가 시작될 때 `PENDING`·`RUNNING`으로 남은 후보는 `FAILED / SERVER_RESTARTED`가 된다(옛 턴은 더 이상 만들어지지 않는다). 화면은 `GET /api/groups/{id}/roles` 하나로 후보·내 순위·집계를 한 번에 읽어 왼쪽과 오른쪽이 같은 서버 상태를 쓴다. 범용 `/api/jobs`는 쓰지 않는다.
- 이 단계에는 역할 배정, 투두 draft, 숨겨진 평가 기준이 없다(테스트가 테이블·컬럼·응답 키를 검사한다).

### 역할 배정·배정 후 인터뷰·투두·재배정 (4단계, D-B1 B4~B9, D-B4)

- 모듈(`src/moim/roles/`): `assignment`(서버 배정, AI 없음), `coverage`(팀 결과물 분담, Timely 1회), `interview_ai`·`interview`(배정 후 AI 주도 인터뷰), `todo_ai`·`todos`(투두 초안·직접/자연어 수정·숨겨진 기준·확정·그룹 조회), `reassign`(재배정 operation과 STALE 전파). 라우트는 `interview/routes.py`, 화면은 3단계와 같은 `chat.html`·`chat.js`(재배정 화면은 없다). 3단계의 `coach.py`·`turns.py`·옛 인터뷰 테이블은 다시 연결하지 않았다.
- 흐름: 마지막 순위 제출 트랜잭션 안에서 그룹 advisory lock(`assignment:{group_id}`) 아래 한 번 배정 → cohort 전원 `ROLE_TODOS` + `assignment_revision` → 배정 직후 coverage(백그라운드) → 구성원별 인터뷰(`ASKING → CONFIRMING → DONE` + snapshot) → 인터뷰 DONE + coverage SUCCEEDED일 때 투두 초안 → 수정 → `투두 확정하기`(불변 확정 revision, 숨겨진 기준 함께 고정) → 그룹 조회 API(역할명·제목·마감일). 세부 규칙과 수치는 `docs/HANDOFF.md`의 `4단계 진행` 절이 단일 기록이다.
- 연결 키: 모든 파생 행은 `source_assignment_revision`으로 하나의 assignment revision에 묶인다(`team_coverages`, `role_interviews`·`role_interview_turns`·`role_member_states`·`role_interview_snapshots`, `todo_drafts`·`todo_items`·`todo_ai_steps`·`todo_actions`·`todo_confirmations`·`todo_confirmed_items`). 그룹당 CURRENT assignment는 부분 유일 인덱스로 하나다.
- 테이블·마이그레이션: `0008_role_assignment`(배정·coverage·인터뷰, `member_progress.ROLE_TODOS`), `0009_todos`(투두·확정, 불변 트리거), `0010_reassignment`(`role_reassignments`). downgrade는 각자 만든 테이블만 지운다.
- API(로그인·CSRF·`action_id` 멱등, cohort 밖은 404): `role-interview/start|answer|retry`, `coverage/retry`, `todos/start|edit|revise|confirm|unlock|retry`, `GET /api/groups/{id}/todos`(그룹), `POST /api/groups/{id}/reassignments`(그룹장), `GET /api/groups/{id}/reassignments`(내 요청·내게 온 요청), `POST /api/groups/{id}/reassignments/{request_id}/respond`(대상자 본인, `accept` true/false). 화면 데이터는 `GET /api/groups/{id}/roles`의 `role` 키 하나다.
- **재배정(D-B1 B4, 수행 11)**: 그룹장 요청(`assignment_revision`, `target_user_id`) + 대상자 본인의 동의가 있을 때만 서버가 새 revision(`operation='REASSIGN'`)을 쓴다. 요청·동의 본문에는 역할이 없다(역할 필드는 400). 입력은 현재 revision과 같은 cohort·후보 revision·순위 제출이다. 대상자의 새 역할은 **대상자 본인의 1→2→3순위 중 자리가 남은 역할**(떠나는 역할 제외)이고, 하나도 없을 때만 서버 RNG가 남은 역할 중에서 고른다(`draw_audit`에 기록). 동의하지 않은 다른 구성원의 역할·slot은 그대로다. 다른 구성원을 옮겨야만 하는 경우(남은 자리 없음)는 요청 단계에서 409 `NO_FREE_ROLE`로 거절한다. 그룹당 열린 요청은 하나(부분 유일 인덱스)이고, 요청·응답은 `action_id`로 멱등, 동의는 그룹 lock 아래에서 한 번만 성공한다. 거절은 `DECLINED`이고 아무것도 바뀌지 않는다.
- **STALE 전파(D-B1 B8, `reassign.mark_assignment_stale`)**: 이전 revision을 `STALE`(+`stale_reason`)로 바꾸고 그 revision의 coverage·인터뷰·snapshot·draft·확정 revision(숨겨진 기준 포함)에 `stale_at`을 쓴다. 지우거나 내용을 바꾸지 않는다. 각 서비스는 STALE 행을 현재 입력으로 쓰지 않는다(수정·확정·재시도 409, 진행 중 AI 결과 폐기, STALE coverage는 호출하지 않음). 열린 재배정 요청은 `SUPERSEDED`.
  - `REASSIGNED`: 순위와 MemberState는 유지된다. 역할이 그대로인 구성원 중 인터뷰를 마친 사람은 확인된 인터뷰 입력(MemberState·snapshot)을 새 revision으로 복사해 다시 묻지 않는다. 재배정된 사람과 인터뷰를 마치지 않은 사람은 새 인터뷰를 한다. 새 revision의 coverage를 다시 만들고(Timely 1회) 모든 구성원이 새 초안·확정을 한다. 이전 확정은 그룹 조회에서 빠진다.
  - `BRIEF_CHANGED`(`GroupService.update_brief`, 같은 트랜잭션·같은 그룹 lock): 새 후보 revision을 만들고 `WAITING_ASSIGNMENT`·`ROLE_TODOS` 구성원 전원을 `INTERVIEW`(순위 미제출)로 되돌린다. 확정 투두는 **읽기 전용으로 남아** 그 구성원이 새 assignment에서 다시 확정할 때까지 그룹 조회에 그대로 나온다(응답 필드는 세 개 그대로라 "읽기 전용" 표시는 없다. D-D1은 정하지 않았으며 5단계에서 개인정보 범위를 넓히지 않는 기술 세부로 정하고, 제품 의미가 달라지면 승인받는다).
  - cohort는 활성화 뒤 바뀌지 않고(신규 합류 차단), 순위 제출은 `ROLE_TODOS`에서 409 `ALREADY_SUBMITTED`라 바뀔 수 없다. 둘이 바뀌는 경로는 ProjectBrief 변경(순위 재제출)뿐이다.

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
- 개인정보 경계: 이미지 data URL 마스킹(`m0/tracing.py:29`), 프롬프트가 성실성·정직성·투표 판단을 금지(`m2/prompts.py`). 첨부 파일 보관·접근·삭제는 GATE D 승인(D-D1, 2026-11-19)과 `docs/SECURITY.md` 3절을 따른다.
- 테스트: `test_m2.py` 7개, `test_m2_evidence.py` 14개 통과(0B-1 실행). 판정 변경 없음.
- 복잡도: `evidence.py` 439줄이 가장 크며 PyMuPDF·Pillow·python-docx에 의존한다.
- 수정 비용: 낮다. M2 검토 형식은 D-D1 D4(이미지는 2048px 메타데이터 제거 사본, PDF, DOCX·TXT·MD 텍스트, HWPX는 결정적 추출 테스트 통과 뒤, 제외 파일은 형식·개수 고정 문구만)에 맞춰 조정한다.

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
