# HANDOFF

날짜가 있는 환경 스냅샷, 현재 상태와 다음 한 단계. 단계 작업이 끝날 때마다 갱신한다. 우선순위가 가장 낮은 문서이므로 헌법·승인 결정과 충돌하면 그쪽을 따른다.

## 현재 상태 (2026-11-18)

- 0A-1·0A-2 완료, GATE A 승인 완료(D-A6 디자인 기준, D-A7 LIVE 허용). LIVE 호출 예산은 D-A8(2026-11-18)이 개정했다: 고정 호출 상한 없음, 모든 호출 기록, 단일 작업 100회 이상 대량·부하 호출만 사전 승인, 자동 재시도·backoff·자동 JSON 복구·숨기는 폴백은 계속 금지, 같은 원인의 실패 3회 반복 시 중단하고 분석. 기록은 `docs/PRODUCT.md`.
- 0B-1 구현·검증 완료(저장소·환경·테스트 기반). 이 단계에서 LIVE 호출은 하지 않았다(횟수 0, `docs/LIVE_TESTING.md`).
- 원본 HTML 9개는 변경하지 않았고 해시 9/9가 `docs/PRODUCT.md` 기준과 같다(`tools/check_design_hashes.py`).
- 0B-2 완료(DETERMINISTIC PASS·LIVE PASS), 0B-3 구현·검증 완료(작업 상태 저장, 화면 캡처 스크립트).
- 0B 보정(2026-11-18): D-A8 기록, Git baseline, DROP 모듈·옛 CLI·오래된 egg-info 제거, 테스트의 `DROP SCHEMA` 가드. 아래 `0B 보정` 절.
- 1단계 인증 구현·검증 완료(2026-11-18, 아래 `1단계 인증` 절). 그룹·ProjectBrief·dashboard 실제 화면은 아직 없다(2단계). `/dashboard`는 로그인 확인용 임시 화면이다.

## 환경 스냅샷 (2026-11-18, 확인한 사실만)

| 항목 | 상태 |
|---|---|
| 작업 폴더 | `C:\Users\user\Desktop\moim-design`. Git 저장소(`main`). 첫 baseline 커밋 `a9a9721`(승인 문서, 원본 HTML 9개, 0A/0B 기반). 원격 저장소 없음, push 안 함. 그 뒤 0B root hardening 커밋은 아래 `0B 보정` 절에 기록 |
| Python | 3.12.8, 가상환경 `.venv` |
| 앱 | `src/moim/`(`config.py`, `db.py`, `logging_utils.py`, `main.py`, `models.py`, `timeutil.py`, `jobs.py`, `job_routes.py`, `auth/`, `templates/`, `static/`). FastAPI 앱, `/healthz`와 `/api/jobs`(핸들러 없음). AI 코드는 `src/wemeet_ai`의 KEEP/ADAPT 모듈만 남았다: `m0`(client·runtime·tracing·records), `m1`(e_member_plan·f_validation), `m2`, `shared` |
| 마이그레이션 | Alembic `0001_foundation`, `0002_ai_jobs`, `0003_auth`. `upgrade → downgrade base → upgrade` 확인(테스트가 매번 실행) |
| PostgreSQL | 16, 포트 54329, 로컬 데이터 `.local/pgdata`. 개발 DB `moim_dev`, 테스트 DB `moim_test`. 역할은 아직 `moim` 하나(superuser)다. 테스트의 `DROP SCHEMA public CASCADE`는 호스트 localhost·포트 54329·DB명 정확히 `moim_test`·`MOIM_ENV=test`를 모두 확인한 뒤에만 실행한다(`tests/dbsafety.py`) |
| 설정 | `.env`(Git 제외)와 `.env.example`. **앱은 `.env`를 자동으로 읽지 않는다.** 환경변수로만 읽으며 `MOIM_ENV`, `MOIM_DATABASE_URL`이 없으면 `ConfigError`로 실패한다. `alembic`은 환경변수를 먼저 불러온 셸에서 실행한다. 테스트는 `tests/conftest.py`가 설정한다 |
| 테스트 | `.\.venv\Scripts\python.exe -m pytest -q`: 248개 통과(2026-11-18 1단계 뒤. 기반·공급자 계약·작업 상태·패키징·Runtime 계약·DROP 가드 164 + 인증 84). 환경변수(`MOIM_*`)를 먼저 불러온 셸에서 실행한다 |
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

  결과는 `artifacts\screens\<원본 파일명>\original.png`와 `impl.png`, 나란히 보는 `artifacts\screens\index.html`. 1단계부터 landing·signup·login 경로가 `SCREENS`에 등록되어 이 3쌍은 `impl.png`까지 만든다. 나머지 6개는 아직 `None`이라 `impl.png` 없이 "구현 없음"으로 기록한다(실패 아님). 임시 확인은 `--route moim-login.html=/login --base-url http://127.0.0.1:8000`. 시드 훅(`apply_dev_seed`)은 제품 테이블이 없어 하는 일이 없다.
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

## 다음 한 단계

2단계 대시보드·그룹·ProjectBrief(`prompts/02-groups-brief.md`). 이전에 `/api/jobs`에 첫 핸들러를 붙일 때 `auth/deps.py`의 `require_user`·`verify_csrf`와 소유자 검사를 같이 넣는다.

## OPEN

`docs/PRODUCT.md` 5절, `docs/ROADMAP.md`의 GATE B·GATE D 목록을 본다. BLOCKED 항목은 없다.

이번 보정에서 범위 밖이라 하지 않은 것(요청에서 제외):

- PostgreSQL 역할 분리(migration·제한된 앱·테스트 역할), 새 환경 DB 준비 스크립트와 실행 문서. 지금은 `moim` superuser 하나다. 진행하다 중단해 되돌렸고 로컬 클러스터도 원래 상태(역할 `moim` 하나)로 복구했다.
- 의존성 constraints/lock 고정, 작업 API의 정책 guard·owner scope 코드, LIVE 회귀(반복·한국어·긴 구조화 출력·실제 Runtime 경로)와 토큰 상한 근거, 캡처 스크립트의 "구현 없음" 보고 강화.

달랐던 사실:

- 크리퍼 `test_unified_storage.py`의 DB 역할 권한 검사는 이 환경의 `moim` 역할이 superuser여서 실패한다. MOIM의 앱 DB 역할 분리는 위 범위 밖 항목과 `docs/SECURITY.md`에서 다룬다.
- `.env` 미로딩 동작은 의도된 설정 방침인지 1단계 전에 확인한다. 필요하면 실행 스크립트를 추가한다.
- 작업 API에는 인증·인가·CSRF가 없다(핸들러 미등록이라 지금은 작업을 만들 수 없음). `docs/SECURITY.md` 4-1. 코드 guard는 아직 없다.
- 서버가 둘 이상이면 시작할 때 서로의 진행 중 작업을 실패로 바꾼다. MVP는 프로세스 하나를 전제한다.
