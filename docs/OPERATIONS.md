# OPERATIONS

7단계(운영 준비, 2026-11-19)에서 만든 운영 절차서. 배포 대상(호스팅·도메인·인증서)은 아직 정해지지 않았다(BLOCKED, 아래 8절). 여기의 절차와 도구는 배포 대상과 무관하게 로컬 PostgreSQL 16에서 실제로 실행해 검증했다. 보안 원칙의 원본은 `AGENTS.md`, 위협·데이터 분류는 `docs/SECURITY.md`, AI 호출 기록은 `docs/LIVE_TESTING.md`다.

## 1. 운영 구성 (권장, PROPOSED)

```
브라우저 ──HTTPS──▶ 리버스 프록시(TLS 종료, HTTP→HTTPS, 본문 상한 60MB) ──HTTP(사설)──▶ uvicorn 1프로세스 ──▶ PostgreSQL 16
                                                                               └──▶ 첨부 디렉터리(MOIM_UPLOAD_DIR)
```

- **프로세스 하나.** 서버가 둘 이상이면 시작할 때 서로의 진행 중 AI 작업을 `SERVER_RESTARTED`로 바꾼다(`main.py` lifespan). 여러 프로세스는 작업 소유 표시를 만든 뒤에만 쓴다.
- **TLS는 프록시가 맡는다.** 앱은 `MOIM_ENV=production`에서 `Secure`·`__Host-` 쿠키와 HSTS(`max-age=31536000`)를 보낸다. 프록시는 `Host` 헤더를 그대로 넘겨야 한다(CSRF가 `Origin`과 `Host`를 비교한다. 바꾸면 모든 POST가 403).
- **실행 명령**(프록시가 같은 호스트면 `--forwarded-allow-ips` 기본값 `127.0.0.1,::1` 그대로):

```
uvicorn --factory moim.main:app_factory --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips <프록시 주소> --no-access-log
```

  `--no-access-log`: uvicorn 기본 접근 로그는 원래 경로와 쿼리(`next=`, id)와 클라이언트 주소를 남긴다. 앱의 `moim.access` 줄이 경로 템플릿만 남기므로 그것을 쓴다.
- **프록시 주소 신뢰(검증함, uvicorn 0.54.0):** 신뢰한 프록시에서 온 요청은 `X-Forwarded-For`의 가장 오른쪽 신뢰하지 않은 주소를 클라이언트로 쓴다. 앞쪽에 위조한 주소를 붙여도 무시되고, 신뢰하지 않은 곳에서 온 헤더는 쓰지 않는다. rate limit(`auth/routes.py` `client_ip`)은 이 주소를 쓴다. 프록시는 `X-Forwarded-For`를 덧붙이거나 덮어써야 한다.

## 2. 설정과 비밀

| 변수 | 운영 값 | 비고 |
|---|---|---|
| `MOIM_ENV` | `production` | 운영 시작 점검(3절)이 켜진다 |
| `MOIM_DATABASE_URL` | 앱 역할(`moim_app`)의 URL | superuser·테이블 소유자면 시작 거절 |
| `MOIM_UPLOAD_DIR` | 절대 경로, 패키지 밖 | 백업 대상 |
| `MOIM_DELETION_LEDGER_DIR` | 절대 경로, 쓰기 가능, 첨부 경로 밖 | 삭제 기록(D-G1). 백업·복원 대상이 **아니다**. DB·첨부와 다른 내구성 저장소(복원해도 되돌아가지 않는 곳) |
| `TIMELY_API_KEY` | 공급자 키 | 서버 환경에만. 로그·HTML·저장소에 없음 |
| `MOIM_LOG_FORMAT` | (운영은 자동 JSON) | 개발에서 `json`으로 켤 수 있다 |
| `MOIM_BACKUP_KEY` | 32바이트 base64 | 백업 도구만. 앱 서버에 두지 않는다 |
| `MOIM_OWNER_PASSWORD` | 마이그레이션 역할 비밀번호 | 마이그레이션·백업 작업에만 |
| `MOIM_SIGNING_KEY` | Ed25519 개인키 seed 32바이트 base64 | 확정 투두 서명(SECURITY 6). 앱 서버에만. **DB·백업·`MOIM_BACKUP_KEY`·owner 비밀번호와 다른 권한의 비밀로 둔다**(같은 사람이 다 가지면 서명의 의미가 줄어든다). 없거나 틀리면 시작 거절. 만들기 `python -m moim.signing new-key` |
| `MOIM_SIGNING_OLD_PUBLIC_KEYS` | 옛 공개키 base64, 쉼표 구분(선택) | 키 교체 뒤 옛 확정 기록 검증용. 공개키라 비밀이 아니다 |

- 비밀은 배포 플랫폼의 비밀 저장소(환경변수 주입)로 넣는다. 앱은 `.env`를 읽지 않는다(1단계부터의 방침). 플랫폼이 정해지면 저장소 이름과 교체 주기를 여기 적는다(BLOCKED).
- 키 교체: Timely 키는 새 키 주입 → 재시작으로 끝난다(저장된 값 없음). 백업 키를 바꾸면 이전 키로 만든 백업은 이전 키로만 열 수 있으므로, 이전 키는 보관 기간(4절)이 끝날 때까지 별도로 보관한다.
- 서명 키 교체: 옛 키의 공개키(`MOIM_SIGNING_KEY=<옛 키> python -m moim.signing public-key`)를 `MOIM_SIGNING_OLD_PUBLIC_KEYS`에 넣고 새 `MOIM_SIGNING_KEY`로 재시작한다. 새 확정은 새 키로 서명되고 옛 확정은 옛 공개키로 계속 검증된다. 옛 개인키는 버린다(더는 서명할 일이 없다). **개인키를 잃고 공개키도 남기지 않으면 그 전 확정은 모두 `INVALID`가 되어 M2 확인이 멈춘다**(완료는 팀 투표로 계속 가능).

## 3. 운영 시작 점검 (`src/moim/preflight.py`)

`MOIM_ENV=production`이면 서버가 시작할 때 아래를 확인하고 하나라도 실패하면 **시작하지 않는다**(`ConfigError`, 실패한 항목 이름만 표시, 값은 표시하지 않음).

| 항목 | 실패 코드 |
|---|---|
| DB 역할이 superuser | `DB_ROLE_IS_SUPERUSER` |
| 역할·DB 생성 권한 | `DB_ROLE_CAN_CREATE_ROLES_OR_DATABASES` |
| RLS 우회 권한 | `DB_ROLE_BYPASSES_RLS` |
| 앱 역할이 테이블 소유 | `DB_ROLE_OWNS_TABLES` |
| 스키마가 최신 마이그레이션이 아님 | `DB_SCHEMA_NOT_AT_HEAD` |
| 첨부 경로가 절대 경로가 아님 / 패키지 안 | `UPLOAD_DIR_NOT_ABSOLUTE` / `UPLOAD_DIR_INSIDE_PACKAGE` |
| Timely 키 없음 | `TIMELY_API_KEY_MISSING` |
| AI 오프라인 스위치(`WEMEET_OFFLINE=1`) 켜짐 | `AI_OFFLINE_SWITCH_ON` |
| 삭제 기록 경로 없음·상대 경로 / 없거나 쓸 수 없음 / 첨부 경로와 겹침 | `DELETION_LEDGER_DIR_NOT_ABSOLUTE` / `DELETION_LEDGER_DIR_NOT_WRITABLE` / `DELETION_LEDGER_DIR_INSIDE_UPLOAD_DIR` |

개발·테스트 환경은 바뀌지 않는다(로컬 `moim` 역할 하나). 테스트: `tests/test_operations.py`.

## 4. DB 역할·마이그레이션·백업·복원 (`tools/ops_db.py`)

### 역할 (PROPOSED, 검증함)

- `moim_owner`: 로그인 가능, 모든 객체 소유, 마이그레이션·백업·복원만. superuser·역할 생성·DB 생성 권한 없음.
- `moim_app`: 로그인 가능, 행 읽기·쓰기(`SELECT/INSERT/UPDATE/DELETE`)와 시퀀스만. 기본 권한(`ALTER DEFAULT PRIVILEGES`)으로 나중에 만든 테이블에도 자동 적용. 앱이 쓰는 `set local moim.lifecycle_purge`(사용자 정의 설정)와 advisory lock은 이 권한으로 동작함을 리허설로 확인했다.
- `PUBLIC`의 `public` 스키마 `CREATE`와 DB 접속 권한을 회수한다.
- 리허설에서 앱 역할로 테이블 생성·삭제·변경, 불변 트리거 끄기(`disable trigger`), `truncate`, 역할·스키마 생성이 모두 거절됐다. 즉 앱 코드에 SQL 주입이 생겨도 불변 기록(확정 투두 트리거)을 끄거나 스키마를 바꿀 수 없다.

```
python tools/ops_db.py provision --admin-url <superuser URL> --database moim --owner moim_owner --app moim_app
MOIM_ENV=production MOIM_DATABASE_URL=<owner URL> alembic upgrade head
```

### 마이그레이션과 되돌리기

1. 배포 전에 **백업을 먼저** 만든다(아래).
2. 새 코드를 올리기 전에 owner 역할로 `alembic upgrade head`. 앱은 스스로 마이그레이션하지 않으며, 스키마가 최신이 아니면 시작하지 않는다(3절).
3. 되돌리기: 각 마이그레이션에 `downgrade`가 있고 테스트가 매번 `upgrade → downgrade → upgrade`를 검사한다. 단 `0012`는 탈퇴 tombstone이 있으면 downgrade를 거절한다(데이터 손실 방지, 6단계). 데이터가 걸린 되돌리기는 **downgrade 대신 직전 백업 복원**을 기본으로 한다.

### 백업 (APPROVED D-G1: 하루 1회 + 배포·마이그레이션 직전, 롤링 7일)

```
MOIM_BACKUP_KEY=<키> python tools/ops_db.py backup --url <owner URL> --upload-dir <첨부 경로> --out moim-YYYYMMDD.bak
```

- 파일 하나: `pg_dump`(custom) + 모든 첨부 + manifest(스키마 버전, 테이블별 행 수, 첨부별 sha256). AES-256-GCM 4MiB 조각 암호화, 조각마다 순번·마지막 표시를 인증해 잘림·순서 바꿈·변조를 모두 거절한다(`tests/test_ops_tools.py`).
- 백업 파일은 앱 서버와 다른 저장소에 둔다(저장소 자체는 배포 대상과 함께 BLOCKED).
- 보관(D-G1, 2026-11-20 승인): **7일이 지난 백업은 지운다.** 나가기·탈퇴로 지운 데이터는 최대 7일 동안 암호화된 백업 안에만 남고, 앱으로는 누구도 열람할 수 없다. 복원은 장애 복구에만 쓴다(사용자 요청으로 지운 데이터를 되살리지 않는다).

```
python tools/ops_db.py prune --backup-dir <백업 경로> --ledger-dir <삭제 기록 경로>            # 목록만
python tools/ops_db.py prune --backup-dir <백업 경로> --ledger-dir <삭제 기록 경로> --delete   # 매일 백업 뒤 실행
```

`prune`은 백업 머리표(`MOIMBK01`)로 시작하는 파일만 본다. 다른 파일은 건드리지 않는다.

### 삭제 기록 (D-G1, `src/moim/account/ledger.py`)

- 그룹 나가기(D6)와 계정 삭제(D7)는 같은 DB 트랜잭션 안에서, 커밋 **직전 마지막 단계로** `MOIM_DELETION_LEDGER_DIR/deletions-YYYYMMDD.jsonl`에 한 줄을 붙이고 디스크에 바로 쓴다(fsync). 한 줄에는 항목 id, 시각, 종류(`LEAVE`/`ACCOUNT_DELETED`), 내부 사용자 id, 나가기면 그룹 id만 있다. 이름·아이디·글·파일명은 없다.
- 8A(D-I1 I4)부터 본인의 포트폴리오 직접 삭제도 같은 방식으로 남긴다: 종류 `PORTFOLIO_ITEM_DELETED`/`PORTFOLIO_PROJECT_DELETED`, 내부 사용자 id와 대상 id(`target_id`)만. `RECORD_UNDONE` 재처리는 그 기록의 포트폴리오 사본도 다시 지우고, `ACCOUNT_DELETED` 재처리는 포트폴리오 전부를 지운다. `replay-deletions --dry-run` 출력에 `PORTFOLIO_DELETED` 개수가 붙는다.
- 하루 파일 하나, 7일이 지난 파일은 쓸 때마다 지운다(백업 보관 기간과 같다). DB·첨부 경로 밖에 두므로 복원해도 되돌아가지 않는다.
- **기록 없이 확정되는 삭제는 없다(fail closed).** 기록을 쓸 수 없으면 트랜잭션을 되돌리고 사용자에게 기존 오류 자리로 "지금은 처리할 수 없어요. 잠시 후 다시 시도해 주세요."(503 `DELETION_UNAVAILABLE`)를 보인다. `lifecycle_event kind=LEDGER_WRITE_FAILED` ERROR 로그로 알린다(7절 경보).
- 기록 뒤 DB 커밋이 실패하면 그 항목 id의 취소 줄을 붙이고 재처리는 그 항목을 건너뛴다. 남는 경우는 커밋 도중 프로세스가 죽는 것뿐이며, 그때는 사용자가 비밀번호·확인까지 마친 삭제가 복원 뒤 재처리로 실행될 수 있다.

### 복원

```
python tools/ops_db.py provision ...            # 빈 DB와 역할
python tools/ops_db.py restore --in moim-YYYYMMDD.bak --url <빈 DB owner URL> --upload-dir <빈 디렉터리>
python tools/ops_db.py grant --url <owner URL> --app moim_app
python tools/ops_db.py verify --url <owner URL> --upload-dir <디렉터리> --manifest-from moim-YYYYMMDD.bak
python tools/ops_db.py replay-deletions --url <앱 역할 URL> --upload-dir <디렉터리> --ledger-dir <삭제 기록 경로>
python tools/verify_signatures.py --url <앱 역할 URL> --public-keys <운영 공개키들>   # 확정 투두 서명(SECURITY 6). 0 모두 VALID, 1 INVALID, 3 UNSIGNED만
# 그 다음에만 앱 서버를 시작한다
```

- **삭제 재처리(D-G1)는 앱이 사용자를 받기 전에 반드시 실행한다.** 삭제 기록의 나가기·탈퇴를 오래된 순서로 앱의 생명주기 코드 그대로 다시 실행한다. 백업 전에 이미 반영된 항목은 `already`로 센다. 두 번 실행해도 안전하다. 실패하면 거기서 멈추고 코드만 표시한다(다시 실행하면 이어서 처리). `--dry-run`은 항목 수만 보인다.
- 재처리로 첫 역할 배정이 완료된 그룹(`first_assignments`)은 앱 시작 때 팀 분석 작업이 `SERVER_RESTARTED` 실패로 바뀌어 화면에서 다시 시도할 수 있다.

- 표가 이미 있는 DB, 비어 있지 않은 첨부 디렉터리에는 복원하지 않는다(덮어쓰기 방지). 첨부는 sha256이 manifest와 다르면 중단한다.
- `verify`는 첨부 행마다 파일 존재·크기·sha256, 고아 파일 수, manifest와 테이블별 행 수 차이, 스키마 버전 일치를 확인한다. 누락·불일치가 있으면 종료 코드 2.
- `verify_signatures.py`가 `INVALID`를 내면 백업 또는 복원 경로가 확정 기록을 바꾼 것이다. 앱을 열지 말고 다른 백업으로 다시 복원한다. `UNSIGNED`(종료 코드 3)는 0016 이전 확정이면 정상이다. 그 수가 0016 이전 확정 수보다 많으면 누군가 테이블 권한으로 서명 검사를 풀었다는 뜻이니 INVALID처럼 다룬다. 리허설(`tools/rehearse_restore.py`)은 원본·복원본 모두에서 이 도구와 앱 역할 변조 시도(스위치+UPDATE, 트리거 건너뛰기)를 확인한다.

### 고아 첨부 정리

```
python tools/ops_db.py sweep --url <URL> --upload-dir <경로>            # 목록만
python tools/ops_db.py sweep --url <URL> --upload-dir <경로> --delete   # 삭제
```

삭제는 커밋 뒤에 하므로 디스크 오류 때 행 없는 파일이 남을 수 있다(6단계 남은 제한). 이 파일은 어떤 경로로도 접근할 수 없고, 정책상 이미 삭제된 데이터다. 주 1회 `--delete`를 권장한다(PROPOSED).

## 5. 복원 리허설 결과 (2026-11-19, `tools/rehearse_restore.py`)

로컬 클러스터의 일회용 DB(`moim_rh`, `moim_rh_restore`)·역할(`moim_rh_owner`, `moim_rh_app`)로 실행했고 끝난 뒤 모두 지웠다. AI는 fixture이며 LIVE가 아니다. 증거: `artifacts\ops\rehearsal-2026-11-19.txt`(저장소 밖).

| 단계 | 결과 |
|---|---|
| 역할 분리 + 마이그레이션(owner) | 2.4초, 0012 |
| 앱 역할 권한 점검(원본·복원 모두) | 생성·삭제·변경·트리거 끄기·truncate·역할·스키마 7개 모두 거절, 행 읽기 허용 |
| 원본에서 운영 모드 앱(앱 역할, HTTPS, `MOIM_ENV=production`) | 시작 점검 통과, 실제 가입(Argon2id) 201, 쿠키 `__Host-`·`Secure`·`HttpOnly`·`SameSite=lax`, HSTS |
| 백업 | 0.7초, 143KB, 37표 173행, 첨부 3개(→4), 평문 `PGDMP` 서명 없음(암호화 확인) |
| 복원 + 권한 재부여 | 1.5초 |
| 검증 | 첨부 누락 0, 해시 불일치 0, 고아 0, 행 수 차이 없음, 스키마 일치 |
| 복원본에서 핵심 흐름(운영 모드, 앱 역할) | 실제 비밀번호 로그인 200, 대시보드 200, 팀 실행 화면 200, 첨부 다운로드 sha256 일치·`attachment`, 새 완료 기록+첨부 쓰기 201 |
| 복원본에서 private 경계 | 비구성원: 실행 API·그룹 화면·첨부 404. 한 구성원의 private 인터뷰 메시지·MemberState에 넣은 고유 canary 문자열이 다른 구성원의 실행·역할·투두 API와 그룹·chat 화면 어디에도 없음. 숨겨진 기준(`deliverable`) 문장 노출 0 |

리허설 중 고친 것(검사 쪽 결함, 제품 결함 아님): fixture 구성원이 모두 같은 인터뷰 문장을 써서 "다른 사람 문장"이 자기 화면에서 발견됐다. 고유 canary 방식으로 바꿨다. 완료 여부 필드 이름(`done_on`)을 잘못 읽어 이미 완료된 투두에 쓰려다 409(`ALREADY_DONE`)가 났다.

### 삭제 재처리 리허설 (2026-11-20, 같은 도구, 증거: `artifacts\ops
ehearsal-replay-2026-11-20.txt`, 저장소 밖)

위 리허설 전체를 다시 돌리고(결과 PASS, 백업 0.8초, 복원 1.3초, 검증 차이 없음) 단계를 하나 더했다. 백업 **뒤에** 원본 DB에서 한 구성원이 그룹을 나가고, canary 주인이 계정을 삭제했다(삭제 기록 2줄).

| 확인 | 결과 |
|---|---|
| 복원 직후(재처리 전) | 삭제한 계정 행 1, 그 id가 23개 표에 있음, 나간 사람이 다시 구성원, 그 사람 인터뷰 4턴, canary 7행 — **복원이 삭제를 되살린다는 것을 확인** |
| `replay-deletions`(앱 역할) | 2건 적용, 0건 already |
| 재처리 뒤 | 계정 행 0, 그 id를 가진 표 0, 나간 사람 구성원 아님, 인터뷰 0, canary 0 |
| 두 번째 실행 | 적용 0, already 2(변화 없음) |

## 6. 장애·AI 작업 실패 훈련 (2026-11-19, `tools/drill_ai_failure.py`)

실제 uvicorn 프로세스(JSON 로그), 일회용 DB `moim_drill`, 로컬 가짜 Timely 엔드포인트(실제 M0 `TimelyClient`가 HTTP로 호출. LIVE 아님). 증거: `artifacts\ops\drill-ai-failure-2026-11-19.txt`.

| 장면 | 결과 |
|---|---|
| 그룹 활성화 → AI 호출이 멈춘 상태에서 프로세스 강제 종료 | 활성화 201, 호출 1건 진행 중 |
| 재시작 | 시작 로그 2줄(`SERVER_RESTARTED` 표시), 역할 후보·팀 목표 모두 `FAILED`/`SERVER_RESTARTED`, 화면에 실패와 다시 시도. `RUNNING`으로 멈춘 것 없음 |
| 공급자 HTTP 500 상태에서 다시 시도 | 202, 공급자 호출 **정확히 1회**(자동 재시도·폴백 없음), `FAILED`/`UPSTREAM_ERROR`, attempt 2 |
| 로그 상관관계 | 재시도 요청의 `X-Request-ID`가 그 요청이 시작한 백그라운드 AI 호출의 `provider_call ok=false error=UPSTREAM_ERROR` 줄에 그대로 있음 |
| 로그 민감정보 | ProjectBrief 본문, 가짜 API 키, 세션 토큰 모두 로그에 없음 |

알게 된 사실: 활성화 한 번의 팀 목표·역할 후보 호출은 같은 요청의 백그라운드 작업이라 **차례로** 실행된다. 팀 목표가 느리면(최대 타임아웃 60초) 역할 후보 시작도 그만큼 늦다. 기능 결함은 아니며, 지연이 문제가 되면 서로 다른 작업으로 나누는 것이 가역적 개선이다(이번에 바꾸지 않음).

**운영 대응 절차(런북):**

1. 경보(7절)에서 `provider_call ok=false`가 몰리면 `error=` 코드를 본다: `AUTH_FAILED`(키) → 키 교체, `INSUFFICIENT_CREDITS` → Timely 결제, `RATE_LIMITED`/`UPSTREAM_ERROR`/`TIMEOUT` → 공급자 상태 확인 뒤 대기. 사용자는 화면의 다시 시도 버튼으로 복구한다. 서버가 자동으로 다시 부르지 않는다.
2. 서버 장애·재시작: 진행 중이던 AI 작업은 자동으로 `FAILED`(`SERVER_RESTARTED`)가 되고 사용자가 다시 시도한다. 순위 제출이 끝났는데 배정이 안 된 그룹은 시작 때 한 번 배정된다.
3. DB 장애: `/healthz`가 500. 복원은 4절 절차(복원 → 권한 재부여 → 검증 → **삭제 재처리** → 앱 시작). 복원 시점 이후의 변경은 잃는다(RPO = 마지막 백업). 7일보다 오래된 시점으로는 복원할 수 없다(D-G1).
4. 사용자 문의에는 응답 헤더 `X-Request-ID`로 로그를 찾는다.
5. 포트폴리오(8단계): 첨부 정리 작업·요약은 실패하면 `FAILED(단계, 코드)`로 남고 자동 재시도하지 않는다. 사용자가 상세 화면 `다시 시도`(작업)나 `다시 만들기`(요약)를 누른다. 서버 재시작 때 진행 중이던 작업·요약은 `SERVER_RESTARTED`로 바뀐다. 요약 `CITATION_INVALID`는 이전 정리를 그대로 두고 오류만 보인다(시험 세트에서 정리형 요청 약 3%가 이 경우). `이전 기록 가져오기`는 추정 호출 100회 이상이면 409 `IMPORT_TOO_LARGE`로 거절되며, 운영자가 사용자와 확인한 뒤 나눠 가져오는 절차는 아직 없다(OPEN).

## 7. 로그·메트릭·경보

- **형식**(`src/moim/observability.py`): 운영은 JSON 한 줄(`ts`, `level`, `logger`, `request_id`, `msg`, 예외면 `exc_type`과 `파일:줄:함수` 목록). 예외 메시지 본문은 넣지 않는다(사용자 내용이 섞일 수 있다). 비밀 가림 필터(`logging_utils.RedactingFilter`)가 먼저 돈다.
- **상관관계**: 요청마다 `request_id`(프록시가 보낸 `X-Request-ID`가 8~64자 영숫자·하이픈이면 유지, 아니면 새로 발급). 응답 헤더로 돌려준다. 응답 뒤 백그라운드 AI 작업과 그 `provider_call` 줄까지 같은 id를 갖는다. 작업 단위 id는 각 서비스 로그의 `group_id`·revision·attempt와 공급자 `call_id`·`request_id`다.
- **접근 로그** `moim.access`: `method`, 경로 **템플릿**(`/api/groups/{group_id}/execution`), `status`, `duration_ms`. 원래 경로·쿼리·클라이언트 주소·쿠키는 남기지 않는다(테스트로 확인).
- **메트릭**: 별도 메트릭 서버를 두지 않고 로그에서 센다(새 인프라 없음, PROPOSED). 수집기는 배포 플랫폼의 로그 서비스(BLOCKED).

| 경보 (PROPOSED) | 조건 |
|---|---|
| 5xx 비율 | `moim.access` status≥500이 5분 동안 요청의 2% 초과 |
| 지연 | `moim.access` `duration_ms` p95가 10분 동안 2초 초과(첨부 업로드 경로 제외) |
| 공급자 실패 | `provider_call ok=false`가 10분 동안 5건 이상, 또는 `error=AUTH_FAILED`/`INSUFFICIENT_CREDITS` 1건 |
| 시작 실패 | `production preflight failed` 로그 |
| 서버 재시작 | `SERVER_RESTARTED` 경고 |
| DB | `/healthz` 실패 2회 연속 |
| 백업 | 26시간 동안 성공한 `backup` 없음 |
| 삭제 기록 | `LEDGER_WRITE_FAILED` 또는 `LEDGER_CANCEL_FAILED` 1건 (삭제 기록 저장소 상태 확인. 나가기·탈퇴가 503으로 막혀 있다) |
| 확정 기록 무결성 | `integrity_event result=INVALID` 1건 (확정 투두가 서명과 다르다: 누군가 DB를 직접 바꿨다. `verify_signatures.py`로 범위를 확인하고 DB 접근 기록·비밀을 점검한다) |

## 8. 부하 표본과 비용

### 부하 표본 (2026-11-19, `tools/load_sample.py`, 개발 PC: 논리 CPU 12개, uvicorn 1프로세스, 로컬 PostgreSQL)

표본 한계: 같은 PC에서 부하 발생기·서버·DB가 함께 돌았고, fixture 팀 하나(4명, 기록 5건)라 데이터가 작으며, AI 호출은 없다(오프라인 스위치). 운영 호스트의 용량을 보장하지 않는다. 증거: `artifacts\ops\load-sample-*.txt`.

| 가상 사용자(0.5초 간격 반복) | 처리량 | 오류율 | 실행 API p50/p95 | 역할 API p50/p95 | 그룹 화면 p50/p95 | DB 연결 최대 |
|---|---|---|---|---|---|---|
| 10명, 30초 | 34.5 req/s | 0 | 74 / 267ms | 116 / 246ms | 53 / 157ms | 7 |
| 40명, 60초 | 46.4 req/s | 0 | 460 / 1,082ms (최대 14.5초) | 624 / 1,025ms | 319 / 603ms | 15 |

- 로그인(실제 Argon2id) 동시 20건: 모두 성공, p50 1.58초·p95 1.64초. 동시 4건은 0.26초.
- 판단: 이 PC에서 프로세스 하나는 **초당 약 45요청**에서 포화한다. 40명일 때 DB 연결이 SQLAlchemy 기본 풀 상한(5+10=15)에 닿았고 지연이 늘었지만 풀 대기 시간 초과·서버 오류는 0이었다. 실제 화면은 몇 초 간격으로 폴링하므로 가상 사용자 1명은 실제 동시 접속자 여러 명에 해당한다. 첫 출시(초대 기반 소규모 팀)에는 충분하다고 보고 성능 변경은 하지 않았다. 병목 후보: 역할 화면 API의 요청당 쿼리 수, 풀 상한, 단일 프로세스.

### Timely 비용 (기존 LIVE 기록 기준, 이번 단계 LIVE 0회)

`docs/LIVE_TESTING.md` 6절의 212회 기록에서 단계별 토큰(입력+출력)을 모았다. 금액은 Timely 요금표·계약에 따라 다르므로 여기서 계산하지 않는다(사용자 확인 필요).

| 작업 | 호출 | 토큰(대략) |
|---|---|---|
| 팀 목표 표시문 | ProjectBrief revision당 1 | ~300 |
| 역할 후보 | revision당 1 | ~750 |
| 팀 coverage | 배정 revision당 1 | ~880 |
| 배정 뒤 인터뷰 | 구성원당 몇 턴 | 턴당 ~1,800 |
| 투두 초안 | 구성원당 1 | ~3,000~3,600 |
| M2 검토 | 제출당 1 | 이미지 1.7k~5.2k, PDF ~1.6k~3.5k, DOCX ~0.9k~1.5k |

4명 팀 한 번(인터뷰 4~5턴 가정)은 약 **5만~6만 토큰**, M2 검토가 제출마다 1~5천 토큰 더해진다. 자연어 투두 수정·재배정·다시 시도는 호출마다 추가된다. 자동 재시도가 없어 실패가 비용을 늘리지 않는다.

### 포트폴리오 RAG 호출 수 (8단계, LIVE 호출 215~641 기준)

| 작업 | 호출 | 비고 |
|---|---|---|
| 첨부 정리(제출 1건) | 2 + 그림 수(최대 8) | 이해 1·임베딩 1·vision. 그림 3개 제출 약 12k 토큰, 그림 없는 제출 약 3.6k |
| 질문 1회 | 일반 3(근거 없음 2), 작은 코퍼스 1, 빈 범위 0 | 평균 7~9초, 재정렬이 대부분 |
| 프로젝트 요약 | 2(생성 1 + 요약 노드 임베딩 1) | 사용자가 누를 때만 |
| 포트폴리오 정리 | 1 | 사용자가 누를 때만 |
| 이전 기록 가져오기 | 기록당 2 + 그림(최대 8) | 추정 100회 이상이면 거절 |

질문은 사용자별 10분 20회·하루 200회, 요약은 시간당 12회로 묶여 있어 사용자 1명의 하루 최대 질문 호출은 약 600회다. 금액은 요금표 확인 뒤 계산한다(사용자 확인 필요).

## 9. 배포 전 남은 일 (BLOCKED/OPEN)

`docs/HANDOFF.md`의 7단계 절과 `docs/ROADMAP.md` 7단계 표가 원본이다.
