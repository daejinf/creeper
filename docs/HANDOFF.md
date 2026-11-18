# HANDOFF

날짜가 있는 환경 스냅샷, 현재 상태와 다음 한 단계. 단계 작업이 끝날 때마다 갱신한다. 우선순위가 가장 낮은 문서이므로 헌법·승인 결정과 충돌하면 그쪽을 따른다.

## 현재 상태 (2026-11-18)

- 0A-1·0A-2 완료, GATE A 승인 완료(D-A6 디자인 기준, D-A7 LIVE 허용·0B 12회). 기록은 `docs/PRODUCT.md`.
- 0B-1 구현·검증 완료(저장소·환경·테스트 기반). 이 단계에서 LIVE 호출은 하지 않았다(횟수 0, `docs/LIVE_TESTING.md`).
- 원본 HTML 9개는 변경하지 않았고 해시 9/9가 `docs/PRODUCT.md` 기준과 같다(`tools/check_design_hashes.py`).
- 0B-2 완료(DETERMINISTIC PASS·LIVE PASS), 0B-3 구현·검증 완료(작업 상태 저장, 화면 캡처 스크립트).
- 인증 UI, 그룹 기능, 제품 화면은 아직 없다(1단계 이후).

## 환경 스냅샷 (2026-11-18, 확인한 사실만)

| 항목 | 상태 |
|---|---|
| 작업 폴더 | `C:\Users\user\Desktop\moim-design`. Git 저장소 아님(`.git` 없음) |
| Python | 3.12.8, 가상환경 `.venv` |
| 앱 | `src/moim/`(`config.py`, `db.py`, `logging_utils.py`, `main.py`, `models.py`, `timeutil.py`, `jobs.py`, `job_routes.py`, `static/job-poller.js`). FastAPI 앱, `/healthz`와 `/api/jobs` |
| 마이그레이션 | Alembic `0001_foundation`, `0002_ai_jobs`. `upgrade → downgrade base → upgrade` 확인(테스트가 매번 실행) |
| PostgreSQL | 16, 포트 54329, 로컬 데이터 `.local/pgdata`. 개발 DB `moim_dev`, 테스트 DB `moim_test`(이름이 `_test`로 끝나야 하고 `MOIM_ENV=test`에서만 허용) |
| 설정 | `.env`(Git 제외)와 `.env.example`. **앱은 `.env`를 자동으로 읽지 않는다.** 환경변수로만 읽으며 `MOIM_ENV`, `MOIM_DATABASE_URL`이 없으면 `ConfigError`로 실패한다. `alembic`은 환경변수를 먼저 불러온 셸에서 실행한다. 테스트는 `tests/conftest.py`가 설정한다 |
| 테스트 | `.\.venv\Scripts\python.exe -m pytest -q`: 105개 통과(MOIM 기반 37 + 공급자 계약 37 + 작업 상태 31). 환경변수(`MOIM_*`)를 먼저 불러온 셸에서 실행한다 |
| 깨끗한 환경 검증 | 새 venv에서 `pip install -e ".[test]"`, `import moim.main`, `pytest` 105개 통과(0B 마감 시점에 `.[test,visual]`로 다시 확인), 마이그레이션 up/down/up은 테스트가 매번 실행. 해시 검사 9/9 일치, 프롬프트 검증 14개 통과 |
| 크리퍼 재확인 | 격리 복사본·격리 DB `creeper_scratch`로 225개 실행, 223 통과·2 실패(원인과 판정 영향 없음은 `docs/ARCHITECTURE.md`의 `0B-1 재확인 결과`) |
| 주의 | 크리퍼의 `tools/db/cutover.py`·`bootstrap.py`와 테스트 일부는 Windows 사용자 레지스트리(`HKCU\Environment`)로 실제 크리퍼 DB(55432)를 읽거나 쓴다. 원본에서 실행하지 않는다. MOIM에 이 방식을 가져오지 않는다 |
| 참고 원본 | `C:\Users\user\Desktop\크리퍼`. 읽기 전용 구조 참고용 |

## 0B-2 진행 상황 (2026-11-18)

- DETERMINISTIC PASS: M0에서 자동 재시도·JSON 복구 호출·검증 재호출·예산 예약을 제거했고, 원문 로그(요청·응답·파싱 결과·오류 본문)를 크기·해시·ID 기록으로 바꿨다. 호출마다 로거 `moim.provider`에 한 줄이 남는다. 테스트 `tests/test_m0_provider.py` 37개(LIVE 뒤 1개 추가), 해시 9/9. 계약은 `docs/LIVE_TESTING.md` 4-1.
- LIVE PASS: 정상 text 1회·정상 structured output 1회 성공. 호출 6회 사용, 잔여 6회(`docs/LIVE_TESTING.md` 4-2, 6절). LIVE에서 옛 주소 종료(410), 모델명 접두, `max_tokens` 거절을 발견해 기본 주소·모델·토큰 파라미터를 고쳤다.
- 키는 사용자 환경변수 `TIMELY_API_KEY`에 있고 `.env`에는 없다. 앱은 환경변수만 읽는다.

## 0B-3 진행 상황 (2026-11-18)

- 작업 상태: `ai_jobs` 테이블과 FastAPI 백그라운드 실행. 상태 흐름·API·복구 규칙은 `docs/ARCHITECTURE.md`의 `AI 작업과 지연 대응`. 새로고침·서버 재시작 복구, 재시도, 중복 요청, revision 충돌(동시 claim 포함)을 `tests/test_jobs.py` 31개가 새 DB 연결로 다시 읽어 검증한다.
- 화면 캡처: 한 명령으로 원본 9개를 정확히 1440×900(뷰포트, 첫 화면)으로 저장한다. 스크롤되는 전체 페이지는 `--full-page`로 따로 저장하며 이때는 크기가 제각각이다.

```
$env:MOIM_ENV='development'; .\.venv\Scripts\python.exe tools\capture_screens.py
```

  결과는 `artifacts\screens\<원본 파일명>\original.png`와 `impl.png`, 나란히 보는 `artifacts\screens\index.html`. 구현 화면이 아직 없어 `tools/capture_screens.py`의 `SCREENS`가 전부 `None`이고 `impl.png`는 만들지 않는다(실패가 아니라 "구현 없음"으로 기록). 구현 화면을 만드는 단계에서 라우트를 채운다. 임시 확인은 `--route moim-login.html=/login --base-url http://127.0.0.1:8000`. 현재 산출물에는 원본 이미지만 있고 구현 이미지(`impl.png`)는 없다. 원본·구현 쌍 저장 경로는 임시 스텁 서버로만 확인했고 실제 구현 화면으로는 확인하지 않았다(서버가 꺼져 있을 때 종료 코드 1은 확인). 시드 훅(`apply_dev_seed`)은 있으나 제품 테이블이 없어 하는 일이 없다.
- 새 의존성: `playwright`(선택 extra `visual`, `pip install -e ".[test,visual]"`). 브라우저는 이 PC에 이미 있는 것을 썼고 새로 내려받지 않았다. 새 큐·DB·프런트엔드 프레임워크는 없다.

## 다음 한 단계

1단계 인증(`prompts/01-auth.md`). 작업 API를 실제로 쓰기 전에 `docs/SECURITY.md` 4-1의 인증·객체 단위 인가·CSRF를 반드시 함께 넣는다.

## OPEN

`docs/PRODUCT.md` 5절, `docs/ROADMAP.md`의 GATE B·GATE D 목록을 본다. BLOCKED 항목은 없다.

달랐던 사실:

- 크리퍼 `test_unified_storage.py`의 DB 역할 권한 검사는 이 환경의 `moim` 역할이 superuser여서 실패한다. MOIM의 앱 DB 역할 분리는 `docs/SECURITY.md` 기준으로 07 단계 전에 정한다.
- `.env` 미로딩 동작은 의도된 설정 방침인지 1단계 전에 확인한다. 필요하면 실행 스크립트를 추가한다.
- 0B-2에서 남긴 두 가지는 아직 정리하지 않았다: `Runtime`의 JSON `max_completion_tokens` 4096이 클라이언트 기본 1500을 덮어쓰는 점, `versions.py`의 `SOURCE_RELOAD_REQUIRED`. 첫 AI 기능을 쓰는 3단계 전에 정한다.
- 작업 API에는 인증·인가·CSRF가 없다(핸들러 미등록이라 지금은 작업을 만들 수 없음). `docs/SECURITY.md` 4-1.
- 서버가 둘 이상이면 시작할 때 서로의 진행 중 작업을 실패로 바꾼다. MVP는 프로세스 하나를 전제한다.
