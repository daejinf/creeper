# HANDOFF

날짜가 있는 환경 스냅샷, 현재 상태와 다음 한 단계. 단계 작업이 끝날 때마다 갱신한다. 우선순위가 가장 낮은 문서이므로 헌법·승인 결정과 충돌하면 그쪽을 따른다.

## 현재 상태 (2026-11-19)

- 0A-1·0A-2 완료, GATE A 승인 완료(D-A6 디자인 기준, D-A7 LIVE 허용). LIVE 호출 예산은 D-A8(2026-11-18)이 개정했다: 고정 호출 상한 없음, 모든 호출 기록, 단일 작업 100회 이상 대량·부하 호출만 사전 승인, 자동 재시도·backoff·자동 JSON 복구·숨기는 폴백은 계속 금지, 같은 원인의 실패 3회 반복 시 중단하고 분석. 기록은 `docs/PRODUCT.md`.
- 0B-1 구현·검증 완료(저장소·환경·테스트 기반). 이 단계에서 LIVE 호출은 하지 않았다(횟수 0, `docs/LIVE_TESTING.md`).
- 원본 HTML 9개는 변경하지 않았고 해시 9/9가 `docs/PRODUCT.md` 기준과 같다(`tools/check_design_hashes.py`).
- 0B-2 완료(DETERMINISTIC PASS·LIVE PASS), 0B-3 구현·검증 완료(작업 상태 저장, 화면 캡처 스크립트).
- 0B 보정(2026-11-18): D-A8 기록, Git baseline, DROP 모듈·옛 CLI·오래된 egg-info 제거, 테스트의 `DROP SCHEMA` 가드. 아래 `0B 보정` 절.
- 1단계 인증 구현·검증 완료(2026-11-18, 아래 `1단계 인증` 절).
- 2단계 대시보드·그룹·ProjectBrief 구현·검증 완료(2026-11-18, 아래 `2단계 그룹·ProjectBrief` 절). 팀 목표 표시문 LIVE 2회 성공(누적 LIVE 8회). `/dashboard`는 이제 실제 화면이고 로그인 확인용 임시 화면은 지웠다. `/groups/{id}/chat`은 3단계 전까지 임시 화면이다.
- **D-B3(2026-11-19, 제품 흐름 변경 승인)**: 3단계에서 개인 AI 인터뷰를 없애고 공용 역할 후보의 비공개 1·2·3순위만 제출받는다. 인터뷰는 배정 뒤 4단계로 옮겼다. 기록·변경 전후 문장은 `docs/PRODUCT.md` D-B3, 헌법은 `AGENTS.md` 1절(4·5번 흐름 항목, 본문 두 문장)만 바꿨다. 3단계 구현은 새 흐름으로 바뀌었고 **사용자가 디자인 눈 확인을 완료했다고 알려와(2026-11-19) 3단계는 완료**다. 4단계 확정 정책은 `docs/PRODUCT.md` D-B4로 기록했다. 4단계는 시작하지 않았다. 아래 `3단계 공용 후보·순위 제출(D-B3)` 절.
- **4단계 세션 A 완료(2026-11-19)**: 서버 역할 배정(assignment revision 1회), `ROLE_TODOS`, 팀 coverage, 배정 안내, AI 주도 배정 후 인터뷰와 확인 단계, 입력 snapshot. 4단계 전체는 세션 B(투두 초안·수정·확정)와 세션 C(재배정·STALE·문서 정리)가 끝나야 완료다. 아래 `4단계 진행` 절.
- GATE B 승인 완료(D-B1, 2026-11-18): 인터뷰 항목·턴, chat 네 상태 문구와 `N/M명 제출 완료` 집계, 공급자 입력 필드, 미제출·신규 합류(차단 유지)·재배정·중복 역할, 역할 후보 생성(revision당 1회), 이름·순위 비노출 안내 문구(원본과 다른 제품 차이), 투두 공개·재확정, STALE 규칙. 이번 작업은 문서만 바꿨고 코드·테스트·원본 HTML은 변경하지 않았다.

## 환경 스냅샷 (2026-11-18, 확인한 사실만)

| 항목 | 상태 |
|---|---|
| 작업 폴더 | `C:\Users\user\Desktop\moim-design`. Git 저장소(`main`). 첫 baseline 커밋 `a9a9721`(승인 문서, 원본 HTML 9개, 0A/0B 기반). 원격 저장소 없음, push 안 함. 그 뒤 0B root hardening 커밋은 아래 `0B 보정` 절에 기록 |
| Python | 3.12.8, 가상환경 `.venv` |
| 앱 | `src/moim/`(`config.py`, `db.py`, `logging_utils.py`, `main.py`, `models.py`, `timeutil.py`, `jobs.py`, `job_routes.py`, `auth/`, `groups/`, `templates/`, `static/`). FastAPI 앱, `/healthz`, `/api/jobs`(핸들러 없음), 인증, 그룹·초대방·ProjectBrief. AI 코드는 `src/wemeet_ai`의 KEEP/ADAPT 모듈만 남았다: `m0`(client·runtime·tracing·records), `m1`(e_member_plan·f_validation), `m2`, `shared` |
| 마이그레이션 | Alembic `0001_foundation`, `0002_ai_jobs`, `0003_auth`, `0004_groups`, `0005_interview`, `0006_interview_d_b2`, `0007_interview_deprecated`(주석만), `0008_role_assignment`(4단계 세션 A). `upgrade → downgrade base → upgrade` 확인(테스트가 매번 실행), 0008은 `downgrade 0007 → upgrade`를 데이터와 함께 확인 |
| PostgreSQL | 16, 포트 54329, 로컬 데이터 `.local/pgdata`. 개발 DB `moim_dev`, 테스트 DB `moim_test`. 역할은 아직 `moim` 하나(superuser)다. 테스트의 `DROP SCHEMA public CASCADE`는 호스트 localhost·포트 54329·DB명 정확히 `moim_test`·`MOIM_ENV=test`를 모두 확인한 뒤에만 실행한다(`tests/dbsafety.py`) |
| 설정 | `.env`(Git 제외)와 `.env.example`. **앱은 `.env`를 자동으로 읽지 않는다.** 환경변수로만 읽으며 `MOIM_ENV`, `MOIM_DATABASE_URL`이 없으면 `ConfigError`로 실패한다. `alembic`은 환경변수를 먼저 불러온 셸에서 실행한다. 테스트는 `tests/conftest.py`가 설정한다 |
| 테스트 | `.\.venv\Scripts\python.exe -m pytest -q`: 393개 통과(2026-11-19 4단계 세션 A 뒤, `test_role_assignment.py` 45개 추가). 그 전 348개(2026-11-19 D-B3 뒤. 옛 `test_interview.py` 30개를 `test_ranking.py` 26개로 교체해 352에서 줄었다. 기반·공급자 계약·작업 상태·패키징·Runtime 계약·DROP 가드 164 + 인증 84 + 그룹 74 + 순위 26). 환경변수(`MOIM_*`)를 먼저 불러온 셸에서 실행한다 |
| 깨끗한 환경 검증 | 0B 보정 커밋 직전, 스테이징한 내용을 새 폴더로 내보내 새 venv에서 `pip install -e ".[test,visual]"`, import, `pytest` 164개 통과(건너뜀 0), 해시 9/9(2회), 프롬프트 검증 14개를 확인했다. 마이그레이션 up/down/up은 테스트와 개발 DB 직접 실행으로 확인 |
| 크리퍼 재확인 | 격리 복사본·격리 DB `creeper_scratch`로 225개 실행, 223 통과·2 실패(원인과 판정 영향 없음은 `docs/ARCHITECTURE.md`의 `0B-1 재확인 결과`) |
| 주의 | 크리퍼의 `tools/db/cutover.py`·`bootstrap.py`와 테스트 일부는 Windows 사용자 레지스트리(`HKCU\Environment`)로 실제 크리퍼 DB(55432)를 읽거나 쓴다. 원본에서 실행하지 않는다. MOIM에 이 방식을 가져오지 않는다 |
| 참고 원본 | `C:\Users\user\Desktop\크리퍼`. 읽기 전용 구조 참고용 |

## 0B-2 진행 상황 (2026-11-18)

- DETERMINISTIC PASS: M0에서 자동 재시도·JSON 복구 호출·검증 재호출·예산 예약을 제거했고, 원문 로그(요청·응답·파싱 결과·오류 본문)를 크기·해시·ID 기록으로 바꿨다. 호출마다 로거 `moim.provider`에 한 줄이 남는다. 테스트 `tests/test_m0_provider.py` 37개(LIVE 뒤 1개 추가), 해시 9/9. 계약은 `docs/LIVE_TESTING.md` 4-1.
- LIVE PASS: 정상 text 1회·정상 structured output 1회 성공. 누적 6회 사용(D-A8 이후 고정 호출 상한 없음)(`docs/LIVE_TESTING.md` 4-2, 6절). LIVE에서 옛 주소 종료(410), 모델명 접두, `max_tokens` 거절을 발견해 기본 주소·모델·토큰 파라미터를 고쳤다.
- 키는 사용자 환경변수 `TIMELY_API_KEY`에 있고 `.env`에는 없다. 앱은 환경변수만 읽는다.

## 0B-3 진행 상황 (2026-11-18)

- 작업 상태: `ai_jobs` 테이블과 FastAPI 백그라운드 실행. 상태 흐름·API·복구 규칙은 `docs/ARCHITECTURE.md`의 `AI 작업과 지연 대응`. 새로고침·서버 재시작 복구, 재시도, 중복 요청, revision 충돌(동시 claim 포함)을 `tests/test_jobs.py` 31개가 새 DB 연결로 다시 읽어 검증한다.
- 화면 캡처: 한 명령으로 원본 9개를 정확히 1440×900(뷰포트, 첫 화면)으로 저장한다. 스크롤되는 전체 페이지는 `--full-page`로 따로 저장하며 이때는 크기가 제각각이다.

```
$env:MOIM_ENV='development'; .\.venv\Scripts\python.exe tools\capture_screens.py
```

  결과는 `artifacts\screens\<원본 파일명>\original.png`와 `impl.png`, 나란히 보는 `artifacts\screens\index.html`. landing·signup·login·dashboard·invite·group-create·group 7쌍은 `impl.png`까지 만든다(chat·mypage는 아직 `None`이라 "구현 없음"으로 기록, 실패 아님). 로그인이 필요한 4쌍은 개발 시드(`apply_dev_seed`)가 만든 사용자의 세션 쿠키로 캡처한다. 시드는 `shot`으로 시작하는 사용자와 그 그룹만 지우고 다시 만들며(개발·테스트 환경만), 데이터베이스 환경변수(`MOIM_DATABASE_URL`)가 있어야 한다. 시드의 팀 목표 문장은 화면 확인용 손으로 쓴 FIXTURE이며 LIVE가 아니다. 캡처는 개발 서버(`uvicorn --factory moim.main:app_factory`)가 떠 있어야 한다.
- 새 의존성: `playwright`(선택 extra `visual`, `pip install -e ".[test,visual]"`). 브라우저는 이 PC에 이미 있는 것을 썼고 새로 내려받지 않았다. 새 큐·DB·프런트엔드 프레임워크는 없다.

## 0B 보정 (2026-11-18)

- **최종 커밋:** `d7e5fd7e9aca33adae33000e1f1008479ba8784b`(`0B root hardening`, baseline `a9a9721` 다음). 이 해시는 보정 내용 커밋의 것이며, 이 줄을 추가한 문서 커밋은 그 다음 커밋이다.
- D-A8: LIVE 예산 개정(`docs/PRODUCT.md`). `docs/LIVE_TESTING.md` 4절·ROADMAP·`prompts/`의 고정 호출 상한 문구를 맞췄다. 누적 LIVE 호출 6회(보정 중 LIVE 재호출 없음).
- 줄바꿈: 변경한 텍스트 파일 21개를 LF로 정상화했고 `git diff --cached --check`가 깨끗하다. 변경하지 않은 기존 파일 일부(`alembic.ini`, `tools/capture_screens.py`, `src/wemeet_ai`의 일부 모듈 등)는 baseline에 CRLF로 들어 있어 그대로 두었다. `AGENTS.md`와 원본 HTML은 커밋에 포함하지 않았다(변경 없음).
- Git: 위 환경 표. `.gitattributes`(`moim-*.html text eol=lf`)와 `.gitignore`(`.env`, `.local/`, `.venv/`, `artifacts/`, `build/`, `*.egg-info/`)가 실제로 적용됨을 `git check-ignore`와 `git ls-files --eol`로 확인했고, `core.autocrlf=true`로 새로 clone해도 원본 9개 바이트가 같았다.
- 제거: `m1/a_coaching`·`b_analysis`·`c_goal_draft`·`d_group_plan`, `persistence/`, `continuity.py`, `cli.py`, `m0/versions.py`, `m0/cache.py`, 오래된 `src/wemeet_ai_core.egg-info`(`wemeet-ai` 콘솔 엔트리와 설치된 `wemeet-ai-core` 배포판). `Runtime`은 캐시·`SOURCE_RELOAD_REQUIRED` 게이트·`fresh`·`scope_id`가 없고 호출 한 번이 모델 호출 한 번이다. `Meta`에서 `source_revision`·캐시 관련 필드를 지웠다. `tests/test_packaging.py`가 남은 모듈 집합(19개) 정확 일치, 제거 모듈 import 불가, 옛 CLI 실행 불가, 콘솔 엔트리·배포판 없음, 빌드한 wheel의 내용을 검증한다. `pyproject.toml`에 `static/*` package-data를 추가했다(없으면 설치본에서 정적 파일이 빠졌다).
- 출력 한도: `DEFAULT_MAX_OUTPUT_TOKENS` 상수 하나(4096). 4096은 기존 값을 유지한 것이고 LIVE 구조화 출력 측정으로 검증하지 않았다.

## 1단계 인증 (2026-11-18)

- 결과: 회원가입(가입 즉시 로그인), 아이디 중복 확인, 로그인(로그인 상태 유지 선택), 로그아웃, 서버 세션, landing·signup·login 템플릿 이식, 비밀번호 복구 비활성 안내. 값·구조는 `docs/SECURITY.md` 4-2, `docs/ARCHITECTURE.md`의 `인증 (1단계)`.
- landing 맨 아래 CTA: 로그아웃이면 `/login`(성공 뒤 `/dashboard`), 로그인이면 `/dashboard`(D-A5). `moim-invite`로 가지 않는다.
- 새 의존성: `argon2-cffi`(라이브러리 하나. 새 DB·큐·프런트엔드 프레임워크 없음). 새 설정 환경변수 없음.
- 검증: `tests/test_auth.py` 84개(결정적, LIVE 없음). 브라우저(내장 브라우저, 개발 서버·개발 DB)에서 중복 확인 3상태, 클라이언트 검증, 가입 → 새로고침 → 로그아웃 → 보호 페이지 차단, 잘못된 비밀번호·없는 아이디의 같은 오류, 로그인 유지 로그인, localStorage·sessionStorage·`document.cookie` 비어 있음을 확인했다. 개발 DB에 확인용 계정 `browser01`이 남아 있다.
- 1440×900 캡처: `artifacts\screens\moim-signup.html\`, `moim-login.html\`는 원본과 구현이 픽셀까지 같았다(진단용 비교, 합격 기준 아님). `moim-landing.html\`은 애니메이션(흐르는 카드·떠다니는 캐릭터) 때문에 캡처마다 일부 픽셀이 달라진다(검토에서 21픽셀 차이). 사용자가 `artifacts\screens\index.html`로 눈으로 확인한다.
- 달랐던 사실:
  - 첫 생성에서 `login.js`·`signup.js`가 문법 오류였고(자르는 위치 오류) 폼이 서버 JSON 오류 화면으로 넘어갔다. 수정했고 `test_generated_scripts_parse`가 모든 정적 스크립트를 `node --check`로 검사한다(node가 없으면 건너뜀).
  - 정적 파일이 휴리스틱 캐시되어 수정 뒤에도 옛 JS가 실행됐다. 정적 파일을 `Cache-Control: no-cache`(재검증)로 서빙한다.
- 남은 제한: `docs/SECURITY.md` 4-2의 `남은 제한`(프록시 뒤 주소, 아이디 단위 잠금의 계정 잠금 공격, 복구 없음, 탈퇴·비밀번호 변경은 6단계, `/api/jobs` 미인증).
- 실행: 앱은 `.env`를 읽지 않는다. 개발 서버는 환경변수를 불러온 셸에서 `python -m uvicorn --factory moim.main:app_factory`로 띄운다.

## 2단계 그룹·ProjectBrief (2026-11-18)

- 결과: `FORMING` 초대방 → ProjectBrief → `ACTIVE` 흐름 전체. 값·구조는 `docs/ARCHITECTURE.md`의 `그룹·초대방·ProjectBrief (2단계)`, 보안 값은 `docs/SECURITY.md` 4-3, 초대방 정원·만료·정리 정책(PROPOSED)은 `docs/ROADMAP.md` 표.
- 판정: 결정적 PASS(`tests/test_groups.py` 74개, 전체 322개). 팀 목표 표시문 LIVE PASS(2회, `docs/LIVE_TESTING.md` 6절). 원본 HTML 해시 9/9 동일(`tools/check_design_hashes.py`).
- 화면 검증: 내장 브라우저가 아니라 Playwright(실제 Chromium) 두 계정으로 dashboard `새 그룹 만들기` → 초대방 → 다른 계정의 코드 입력 참여 → 대기 화면 → 최소 2명에서 `그룹 생성하기` → ProjectBrief 입력(달력·결과물 칩·중간 일정) → 다음 버튼 → 두 계정이 각자 chat으로 이동 → 팀 목표 표시 → 지각 참여 거부 → ProjectBrief 수정(revision 2) → 새 표시문을 확인했다. 스크립트는 일회용이라 저장소에 넣지 않았다. 확인용 계정(`shote…a`, `shote…b`)과 그룹은 개발 DB에 남아 있고 캡처 시드를 다시 돌리면 정리된다.
- 1440×900 캡처: `artifacts\screens\` 아래 dashboard·invite·group-create·group 4쌍을 새로 만들었다. 원본과의 눈 확인은 `artifacts\screens\index.html`에서 사용자가 한다.
- 달랐던 사실:
  - 원본 dashboard·group에는 `새 그룹 만들기`가 `<a>`였다. 구현은 `<button>`(클릭 시 `POST /api/groups`)이며 스타일을 그대로 쓴다.
  - 원본 dashboard에 없던 `초대코드로 참여` 링크(상단)와 `로그아웃` 버튼을 상단 우측에 넣었다. 원본 landing → invite 경로가 D-A5로 바뀌어 초대받은 사람이 코드 참여 화면에 들어갈 길이 필요했고, 로그아웃은 1단계 임시 화면이 하던 일을 옮긴 것이다. 기존 `.login` 링크 스타일을 재사용했다. **D-A9(2026-11-18)로 사용자가 승인했다**(`docs/PRODUCT.md`). 마이페이지 링크는 6단계에서 복원한다.
  - 원본 사이드바의 프로필 `마이페이지` 링크는 마이페이지가 6단계라 링크 없는 표시(이름만)로 바꿨다. 이 차이도 D-A9에 포함된다.
  - 원본 invite는 참여 칸과 코드 박스가 한 화면에 함께 있다. 초대방이 없는 사용자는 코드 박스 대신 `새 그룹 만들기` 버튼을, 참여 대기자는 입력칸과 코드 박스 없이 대기 문구를 본다(초대방 활성화 대기 상태, 기존 문구·칩 스타일 재사용).
  - 원본 group-create의 `moim-group-create-position.html` 이동과 invite의 `그룹 생성 완료` 팝업은 이식하지 않았고, 원본 CSS의 쓰이지 않는 규칙(투두·패널 등)은 그대로 둔 채 구현에 필요한 최소 CSS만 덧붙였다(비활성 버튼, 안내 패널, 팀 목표 다시 시도 버튼).
  - 첫 e2e 실행에서 Playwright의 `wait_for_function`이 CSP(`unsafe-eval` 금지)에 막혔다. 제품 쪽 문제가 아니라 확인 방법이었고 셀렉터 대기로 바꿨다.
- 남은 제한: `docs/SECURITY.md` 4-3의 `남은 제한`(코드 공간 100만 개, 대기자 스스로 나가기 없음, 폴링 4초, 표시문 근거는 사람 판정).

## 3단계 공용 후보·순위 제출 (D-B3, 2026-11-19)

- 결과: ACTIVE cohort 구성원이 개인 인터뷰 없이 그룹 공용 역할 후보(ProjectBrief revision당 Timely 1회, 불변)에서 서로 다른 1·2·3순위를 고르고, 제출 전에는 화면에서 자유롭게 바꾸며, 제출하면 immutable preference submission revision과 `WAITING_ASSIGNMENT`가 저장·복구된다. 모두에게 `N/M명 제출 완료`만 보인다. 값·구조는 `docs/ARCHITECTURE.md`의 `공용 역할 후보·비공개 순위 제출 (3단계, D-B3)`, 보안은 `docs/SECURITY.md` 4-4.
- 판정: 결정적 PASS(`tests/test_ranking.py` 26개, 전체 348개, 공급자는 FIXTURE). **이번 작업에서 LIVE 호출 0회**(후보 생성 코드·프롬프트·입력은 바꾸지 않았다. 누적 LIVE 67회 그대로). 원본 HTML 해시 9/9 동일. 프롬프트 검증 14개 PASS. 실제 Chromium(Playwright, 개발 서버·개발 DB)에서 후보 6개 말풍선 → 칩 3개 선택 → 순위 변경 → 제출 → `순위를 제출했어요… 1/4명 제출 완료` → 새로고침 복구, 콘솔 오류 없음, `localStorage`·`sessionStorage` 비어 있음을 확인했다.
- 바꾼 것: 인터뷰 API(`/interview*`)·MemberState 패널·인터뷰 턴·종료 버튼·`E.coach` 호출을 3단계 경로에서 제거. `RankingService`(`ranking.py`)가 화면·제출을 맡고 `GET /api/groups/{id}/roles`가 화면 데이터를 준다. 입력창은 구조 유지를 위해 남기되 비활성이다(AI 대화 없음). 패널 제목은 `역할 순위 선택`·`역할 배정 대기`.
- 보존(삭제 안 함, 4단계 재사용): `interview/coach.py`(E.coach 어댑터), `interview/turns.py`(`InterviewTurnService`, 어디서도 import하지 않음), `wemeet_ai.m1.e_member_plan`, 테이블 `interview_turns`·`member_states`·`member_progress.interview_closed_at`(마이그레이션 `0007`이 DB 주석으로 DEPRECATED 표시만 했고 데이터는 그대로, 테스트로 down/up 뒤에도 행이 남음을 확인).
- 달랐던 사실:
  - 옛 `tests/test_interview.py`(31개)를 삭제했다. 후보·격리·순위·STALE 검증은 `test_ranking.py`로 옮겼지만 인터뷰 턴·E.coach 동작 검증은 옮기지 않았다(코드는 호출되지 않는다). 그 파일은 커밋 전 작업 사본이라 Git에서 복구할 수 없다. 4단계가 `coach.py`·`turns.py`를 다시 연결할 때 테스트를 새로 써야 한다.
  - 포트 8000에 이전 코드의 개발 서버가 떠 있어 종료하고 새 코드로 다시 띄웠다(캡처가 옛 라우트를 읽어 실패했기 때문).
  - 캡처 스크립트 시드에서 인터뷰 턴·MemberState 삽입을 지웠다(후보만 시드).
- 화면 캡처: `artifacts\screens\moim-chat.html\original.png`·`impl.png`와 추가 `impl-ranking.png`(순위 선택 중)·`impl-waiting.png`(제출 뒤), `artifacts\screens\index.html`. 시드의 후보는 손으로 쓴 FIXTURE다. 눈 확인은 사용자가 한다.
- 재검증(2026-11-19): 전용 PostgreSQL을 기동해 `tests/test_ranking.py` 26개와 전체 348개를 다시 실행했고 모두 PASS했다. 원본 HTML 해시 9/9, 프롬프트 검증 14개, `git diff --check`도 PASS했다. 캡처 4종은 모두 1440×900이며 `original.png`·`impl.png`·`impl-ranking.png`·`impl-waiting.png`를 직접 열어 레이아웃을 확인했다. 폴더에 남은 `impl-ready.png`는 D-B3 이전 개인 인터뷰 화면의 오래된 미사용 캡처이고 `index.html`에는 연결되지 않는다. 공식 단계 완료 전환에는 여전히 사용자의 원본·구현 눈 확인이 필요하다.
- 해결된 OPEN: 10턴 안에 필수 4개를 못 채우면 순위를 못 내던 막다른 길은 인터뷰가 3단계에서 없어져 사라졌다.
- 개발 DB에 남은 것: 일회용 LIVE 스크립트의 `live3a…`/`live3b…` 사용자와 그룹, 캡처 시드의 `shot*`(시드를 다시 돌리면 `shot*`만 정리된다).

## 4단계 진행 (2026-11-19)

4단계는 `prompts/04-split/`의 세 세션(A·B·C)으로 나눠 진행한다. **세션 C가 끝나기 전까지 4단계는 완료가 아니며, ROADMAP의 4단계 상태는 바꾸지 않았다.** 커밋하지 않았다(사용자 확인 뒤 커밋).

### 세션 A 완료 (수행 1~6: 역할 배정, coverage, 배정 후 인터뷰)

- 선행 게이트: 3단계 완료 기록(위 현재 상태)과 D-B4 승인 기록(`docs/PRODUCT.md`)을 확인했다.
- **배정**(`src/moim/roles/assignment.py`): 순위 제출 트랜잭션 안에서 그룹 단위 advisory lock(`assignment:{group_id}`)을 구성원 잠금보다 먼저 잡는다. 그 제출로 현재 cohort 전원이 현재 candidate revision의 `WAITING_ASSIGNMENT`가 되면 `assign_if_ready`가 한 번만 배정한다.
  - 규칙: 1→2→3순위 라운드로 배정한다. 같은 라운드에서 자리보다 지원자가 많으면 서버 RNG로 당첨자를 정하고, 3순위까지 못 받은 사람은 남은 자리 중 서버 RNG로 배정한다.
  - 중복 역할: 역할 수 < 인원일 때만 역할당 ceil(인원/역할) 자리를 둔다(fixture로만 시험). 구성원은 익명 slot(`s1`…)으로 구분된다.
  - RNG: 운영은 `random.SystemRandom()`(seed 없음), 테스트는 `create_app(rng_factory=...)`로 seeded `random.Random`을 주입한다. 모든 무작위 추첨은 `role_assignments.draw_audit`(서버 전용, 화면·로그에 안 나감)에 남는다.
  - 1회 보장: 같은 제출 재전송·새로고침·재시작·동시 마지막 제출로 다시 추첨하지 않는다. 부분 유일 인덱스 `uq_role_assignments_current`가 그룹당 CURRENT를 하나로 막는다.
- **ROLE_TODOS**: 배정과 같은 트랜잭션에서 cohort 전원이 `member_progress.status = ROLE_TODOS`, `assignment_revision = <rev>`가 된다. `submission_revision`은 배정 입력 추적용으로 유지한다. 집계 `N/M명 제출 완료`는 ROLE_TODOS도 제출로 센다.
- **coverage**(`src/moim/roles/coverage.py`): 배정 직후 `team_coverages`에 PENDING 행이 생기고, 응답 뒤 백그라운드에서 Timely를 1회 호출한다.
  - 입력: ProjectBrief(결과물에 서버 id `d1`…)와 이름 없는 slot 표뿐이다.
  - 서버 검사(ID 수준): 출력 `parts`[{name, source(dN), slot(sN)}]에서 모든 dN이 하나 이상의 part에 있고, 모든 slot이 1~4개 part를 가지며, 같은 이름 part 중복과 모르는 id가 없어야 한다. part id(`p1`…)는 서버가 붙인다.
  - 실패: FAILED로 두고 아무 cohort 구성원이나 명시적으로 재시도한다(`POST /api/groups/{id}/coverage/retry`, action_id 멱등). 서버 재시작 때 PENDING/RUNNING은 `SERVER_RESTARTED` FAILED가 된다. 대체 목록은 없다.
  - 브라우저에는 상태만 가고 parts는 보내지 않는다.
- **배정 안내**: 서버 문장 두 개(일반 문구 + "OO님은 이번 프로젝트에서 ○○ 역할을 맡게 됐어요(N순위로 배정 / 남은 역할로 배정)")를 AI 말풍선 스타일로 보이며 Timely를 부르지 않는다. 오른쪽 패널 제목은 `내 투두리스트`이고 원본과 같은 `rolebox`(`내 역할` · 역할명 · `N순위로 배정`)를 쓴다. 다른 구성원의 이름·역할·순위·추첨 결과는 보내지 않는다.
- **배정 후 인터뷰**(`src/moim/roles/interview_ai.py` 순수 로직, `src/moim/roles/interview.py` 서비스): 항목은 `experience`(역할 관련 경험, 필수)·`weekly_available_minutes`(필수)·`tools`·`preferred_times`·`constraints`다.
  - AI 주도: 매 턴 Timely가 다음 질문과 버튼 2~4개를 만든다(첫 질문도 AI).
  - 서버 검사: 이미 아는 항목·이미 한 질문은 금지(필수가 UNKNOWN일 때만 1회 재질문), 내부 표현 금지, 버튼 2개 이상, 시간 버튼은 라벨의 숫자 하나 = 저장 값. 남은 질문 수가 아직 묻지 않은 필수 수 이하이면 그중에서만 묻고, 필수를 다 묻기 전에는 끝낼 수 없다.
  - 쓸 수 없는 질문: 그 턴을 FAILED(오류 말풍선 + `다시 시도`)로 두고 고정 질문으로 대체하지 않는다.
  - 한도·모름: 최대 질문 5개(DB check `questions_asked between 0 and 5`). `잘 모르겠어요`를 항상 제공하고 UNKNOWN은 그대로 저장한다.
  - 값 저장: 자유 입력 값은 사용자 문장 인용이 있고 값이 사용자 말에서 만들어졌을 때만 저장한다. 의미 없는 입력은 422로 저장·질문 수·호출을 쓰지 않는다. 이해 못 한 답은 같은 질문을 유지하고(질문 수 미증가), 답변 AI 호출은 인터뷰당 최대 12회다.
  - 제약: 제약 질문의 자유 입력은 Timely에 보내지 않고(`answer_kind=withheld`) 원문을 private로 저장한다. 코드가 변환하는 것은 날짜(범위 포함, 프로젝트 기간 안), `이번/다음 주 X요일`(오늘 기준 하루), 부정 문맥의 요일(매주), 긍정 문맥의 시간대 단어(선호 시간 후보)뿐이다.
  - 확인 단계: 질문이 끝나면 `CONFIRMING`이 된다. 서버가 쓴 확인 말풍선과 버튼 `맞아요`/`OO 고치기`를 보이고, 고치기는 입력창에 적거나 `잘 모르겠어요`/`그대로 둘게요`로 끝낸다(코드만, AI 호출 없음). `맞아요` → `DONE` + snapshot.
  - 화면은 기존 말풍선·빠른 답변·입력창만 쓴다(새 카드·패널 없음). 인터뷰는 역할을 바꾸지 않는다(역할 변경 API 없음, 테스트로 확인).
- **새 테이블·마이그레이션** `migrations/versions/0008_role_assignment.py`:
  - 테이블: `role_assignments`, `role_assignment_members`, `team_coverages`, `role_interviews`(하위 상태 `ASKING|CONFIRMING|DONE`), `role_interview_turns`(`OPEN|ANSWER|EDIT`), `role_member_states`(불변 revision), `role_interview_snapshots`.
  - `member_progress`: `assignment_revision` 컬럼과 상태 `ROLE_TODOS`를 추가했다. 제약 `submission_iff_waiting`을 `submission_unless_interview` + `assignment_iff_role_todos`로 바꿨다.
  - downgrade는 ROLE_TODOS를 WAITING_ASSIGNMENT로 되돌리고 새 테이블만 지운다. 옛 `interview_turns`·`member_states`·순위 제출은 그대로 남는다(테스트로 down→up 확인). 개발 DB `moim_dev`도 0008로 올렸다.
- **새 API**(모두 cohort 구성원 본인만, 로그인·CSRF·action_id 필요):
  - `POST /api/groups/{id}/role-interview/start`
  - `POST /api/groups/{id}/role-interview/answer`(`message` 또는 `question_id`+`option_id`)
  - `POST /api/groups/{id}/role-interview/retry`(`seq`, `expected_attempt`)
  - `POST /api/groups/{id}/coverage/retry`(`assignment_revision`, `expected_attempt`)
  - 화면 데이터는 기존 `GET /api/groups/{id}/roles`의 `role` 키(ROLE_TODOS일 때만)로 간다: `role_name`, `role_description`, `rank_text`, `announcement`, `interview`(state·messages·question{question_id, kind, options[id,label]}·busy·failed·input_open), `coverage`(상태만).
- **세션 B가 읽을 연결 키(정확히)**: 현재 배정은 `role_assignments`에서 `group_id`이고 `state='CURRENT'`인 행의 `revision`이며, 이것이 **`source_assignment_revision`**이다. 이 키로 다음을 읽는다.
  - 내 역할: `role_assignment_members(group_id, assignment_revision, user_id)` → `slot_id`, `role_name`, `role_description`. `assigned_rank`는 AI 입력에 넣지 않는다.
  - 이름 없는 배정표: `AssignmentService.team_table(s, group_id, rev)` → [{slot_id, role, description}].
  - coverage: `team_coverages(group_id, source_assignment_revision)` 중 `state='SUCCEEDED'`이고 `stale_at is null`인 행의 `parts`. 내 결과물은 `parts` 중 `slot == 내 slot_id`인 것이다.
  - 인터뷰 입력: `role_interview_snapshots(group_id, user_id, source_assignment_revision)`. `role_interviews.state='DONE'`일 때만 있다.
    - `provider_input`={`current_level`, `weekly_available_minutes`(정수 분 또는 "UNKNOWN"), `tools`, `preferred_times`}: B3 허용 필드만 있고, UNKNOWN은 문자열 `"UNKNOWN"`이다.
    - `scope`는 인터뷰에서 묻지 않았다. coverage의 내 parts가 scope다.
    - `deadline_limits`={`blocked_dates`(ISO), `blocked_weekdays`(0=월)}는 서버 마감일 검증용이다. `private_constraints.raw`는 공급자에 보내지 않는다.
    - `member_state_revision`으로 원래 MemberState를 추적한다.
- **세션 C용 STALE 구조**: `role_assignments.state='STALE'`+`stale_at`·`stale_reason`, `team_coverages.stale_at`, `role_interviews.stale_at`, `role_interview_snapshots.stale_at`.
  - 인터뷰 서비스는 `stale_at`이 있으면 409(NOT_ASSIGNED)로 막고 진행 중 턴 결과를 버린다.
  - 재배정은 새 revision(`operation='REASSIGN'`)으로 만들면 된다(CURRENT 유일 인덱스가 하나만 허용).
  - **아직 하지 않은 것**: ProjectBrief를 수정해도 `GroupService.update_brief`는 `WAITING_ASSIGNMENT`만 INTERVIEW로 되돌린다. `ROLE_TODOS`·assignment·coverage·인터뷰는 STALE로 바꾸지 않는다(수행 11, 세션 C).
- **고친 3단계 경계 테스트**(수행 13의 일부를 앞당긴 것이며 검증을 약화하지 않았다), `tests/test_ranking.py`:
  - `test_nothing_is_assigned_drafted_or_hidden_before_or_after_everyone_submits` → `test_nothing_is_assigned_before_everyone_submits_and_nothing_is_drafted_after`. 이제 다음을 확인한다: 전원 제출 전 assignment·coverage·인터뷰 0개, 마지막 제출 직후 assignment 정확히 1개·coverage 호출 1회, 투두·숨겨진 기준 테이블·컬럼 없음, 인터뷰 AI 호출 없음. 이유: 4단계 계약은 마지막 제출 때 배정한다.
  - `test_progress_is_a_count_only_and_shown_to_every_cohort_member`: 마지막 제출 뒤 phase가 `WAITING_ASSIGNMENT`가 아니라 `ROLE_TODOS`다.
  - `test_duplicate_and_concurrent_submits_store_one_revision`: `submit_preferences`가 이제 `(stored, assignment_revision)`을 돌려줘서 `[0]`만 비교한다.
  - 4단계는 deprecated `interview/coach.py`·`turns.py`를 import하지 않으므로 해당 3단계 테스트는 그대로다. 의미 없는 입력 규칙만 같은 내용으로 `interview_ai.meaningful`에 옮겼다.
- **검증**:
  - 결정적: `tests/test_role_assignment.py` 45개. 범위는 배정(순수 알고리즘·고정 RNG·충돌·소진·중복 역할·SystemRandom), 1회 저장(재전송·재시작·동시 제출), 본인 역할만 노출, coverage(ID 검사 6종·실패·재시도·재시작), 인터뷰(AI 첫 질문·전체 흐름·5개 한도·필수 둘 다 모름·의미 없는 입력·이해 못 함·근거 없는 값·쓸 수 없는 질문 5종·공급자 실패·확인 단계 수정·오래된/위조 질문·타인 비차단·역할 불변), 비공개·로그·404·로그인·CSRF·XSS, 새로고침·재시작 복구, 제약 변환·시간 버튼, 마이그레이션 0008 down/up, DB 제약이다.
  - `test_ranking.py` 26개(위 3개 수정 포함).
  - **세션 A 검증 실행(2026-11-19)**: 전체 결정적 테스트 393개 통과(실패·건너뜀 0, 이전 348 + 4단계 45), 원본 HTML 해시 9/9, `prompts/validate-prompts.ps1` 14개 PASS, `git diff --check` 깨끗함. 교차 단계 전체 회귀는 세션 C가 단계 종료 때 다시 한 번 실행한다.
  - LIVE: 36회(호출 68~103), 모두 성공. 기록은 `docs/LIVE_TESTING.md`. 역할 5종(UX/UI 디자인·프론트엔드 개발·발표 자료·발표 자료 제작·서비스 기획)의 질문을 사람이 판정할 수 있게 남겼다. 1차에서 찾은 결함 2개(시간 범위 버튼, "다음 주 화요일"→매주)를 고치고 최종 코드로 2차를 다시 실행했다.
- **브라우저**: 개발 서버(포트 8001, 새 코드)와 Playwright Chromium으로 LIVE 2차 그룹의 두 구성원 chat을 1440×900으로 열었다.
  - 확인한 것: 패널 제목 `내 투두리스트`, rolebox `UX/UI 디자인 · 1순위로 배정`, 안내 말풍선, 질문·답·확인 말풍선, 콘솔 오류 0, localStorage·sessionStorage 비어 있음.
  - 캡처: `artifacts\screens\moim-chat.html\impl-role-todos-1.png`·`impl-role-todos-2.png`(DONE 상태).
  - 세션 C로 남긴 것: 인터뷰 진행 중 화면 캡처, 그리고 순위 제출 → 배정 → 인터뷰를 브라우저에서 처음부터 끝까지 클릭하는 흐름(디자인·브라우저 검증).
  - 포트 8000에는 이전 코드의 개발 서버가 여전히 떠 있다(건드리지 않음).
- **다음 세션이 알아야 할 사실**:
  - 오른쪽 패널의 투두 요소(`내 투두 N` pill, count, `+ 직접 추가하기`, `투두 확정하기`)는 여전히 `hidden`이다. 세션 B가 초안이 생기면 보이게 한다. 인터뷰 DONE 뒤 빈 상태 문구는 "인터뷰를 마쳤어요. 이 내용으로 투두 초안을 만들 거예요."이며 초안 생성은 아직 없다.
  - 확인 단계의 `맞아요`는 `role_interviews.state='DONE'`과 snapshot을 같은 트랜잭션에 쓴다. 세션 B는 DONE을 초안 시작 조건으로 쓰면 된다. coverage가 FAILED면 초안을 만들 수 없으므로 초안 쪽에서도 상태를 확인해야 한다.
  - `RoleInterviewService.role_view`가 화면 데이터의 단일 진입점이다. 투두 데이터도 같은 `role` 키 아래에 두면 GET 한 번으로 양쪽이 함께 그려진다(`chat.js`는 `d.role`만 본다).
  - LIVE용 개발 DB에 일회용 사용자 `live4…a/b/c`(그룹 2개)가 남아 있다.
- **남은 OPEN(세션 A 범위)**: LIVE에서 AI가 매번 다섯 항목을 모두 물었다. "도구·선호 시간·제약은 필요할 때만"이 실제로는 거의 줄지 않았다. 정책 위반은 아니지만 사용자 판정 대상으로 남긴다. 역할별 질문 품질 차이도 사람(사용자)의 판정이 필요하다.

## 다음 한 단계

**다음 한 단계: 4단계 세션 B(`prompts/04-split/04B-todos-confirm.md`, 수행 7~10: 투두 초안·수정·확정과 그룹 조회 API).** 입력 연결 키는 위 `4단계 진행 > 세션 A 완료 > 세션 B가 읽을 연결 키`에 있다. 아래 목록은 4단계 시작 전에 적어 둔 메모이며, 세션 A에서 처리된 항목(배정 입력 기준, `ROLE_TODOS` 추가, 배정 뒤 인터뷰)은 위 절이 현재 사실이다.

4단계 구현 전 메모(2026-11-19, 세션 A 이전):

- 배정은 `preference_submissions`의 최신 revision 중 `source_candidate_revision`이 현재 ProjectBrief revision인 것만 입력으로 쓰고, `member_progress.status = WAITING_ASSIGNMENT`인 cohort 전원이 모였을 때만 실행한다(`interview/ranking.py`의 집계 쿼리와 같은 기준). 후보 id(`r1`…)는 revision 안에서만 유일하다.
- **배정 뒤 인터뷰(D-B3)**: 배정이 끝난 각 사용자가 본인에게만 보이는 AI와 배정 역할에 맞는 짧은 인터뷰를 한 뒤 투두 초안을 만든다. 경험·주간 가능 시간·도구·일정·환경 제약·선호 시간만 묻고 배정 역할을 바꾸지 않으며 순위 원문은 AI 입력에 넣지 않는다. 항목·턴·종료 조건이 제품 의미를 바꾸면 사용자 승인이 필요하다. `interview/coach.py`·`turns.py`와 보존 테이블은 재사용할 수 있지만 D-B2의 옛 질문 항목(필수 4개)·턴 한도(10)·종료 버튼 규칙은 D-B3로 개정됐으므로 그대로 쓰지 말고 4단계에서 다시 정한다. 다시 연결할 때 `coach.py`·`turns.py` 테스트를 새로 쓴다.
- 투두 초안 공급자 입력은 D-B1 B3를 따른다(`constraints`·순위 원문·대화 원문 금지). `member_progress.status`는 지금 `INTERVIEW | WAITING_ASSIGNMENT` 두 값뿐이다(`ROLE_TODOS`와 배정 뒤 인터뷰 상태는 4단계가 추가).
- `/api/jobs`는 여전히 인증이 없고 핸들러도 없다. 4단계가 범용 작업 API를 쓰려면 `auth/deps.py`의 `require_user`·`verify_csrf`와 소유자 검사를 함께 넣는다(전용 상태 테이블을 쓰면 필요 없다).

## OPEN

`docs/PRODUCT.md` 5절, `docs/ROADMAP.md`의 GATE B·GATE D 목록을 본다. BLOCKED 항목은 없다.

3단계 관련 OPEN: 10턴 한도 막다른 길은 D-B3로 해소됐다. E.coach 지연 단축 항목은 배정 뒤 인터뷰를 4단계에서 정할 때 다시 본다. 배정 뒤 인터뷰의 항목·턴·종료 조건은 4단계에서 정한다(제품 의미가 바뀌면 승인).

이번 보정에서 범위 밖이라 하지 않은 것(요청에서 제외):

- PostgreSQL 역할 분리(migration·제한된 앱·테스트 역할), 새 환경 DB 준비 스크립트와 실행 문서. 지금은 `moim` superuser 하나다. 진행하다 중단해 되돌렸고 로컬 클러스터도 원래 상태(역할 `moim` 하나)로 복구했다.
- 의존성 constraints/lock 고정, 작업 API의 정책 guard·owner scope 코드, LIVE 회귀(반복·한국어·긴 구조화 출력·실제 Runtime 경로)와 토큰 상한 근거, 캡처 스크립트의 "구현 없음" 보고 강화.

달랐던 사실:

- 크리퍼 `test_unified_storage.py`의 DB 역할 권한 검사는 이 환경의 `moim` 역할이 superuser여서 실패한다. MOIM의 앱 DB 역할 분리는 위 범위 밖 항목과 `docs/SECURITY.md`에서 다룬다.
- `.env` 미로딩 동작은 의도된 설정 방침인지 1단계 전에 확인한다. 필요하면 실행 스크립트를 추가한다.
- 작업 API에는 인증·인가·CSRF가 없다(핸들러 미등록이라 지금은 작업을 만들 수 없음). `docs/SECURITY.md` 4-1. 코드 guard는 아직 없다.
- 서버가 둘 이상이면 시작할 때 서로의 진행 중 작업을 실패로 바꾼다. MVP는 프로세스 하나를 전제한다.
