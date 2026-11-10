# PostgreSQL 저장·개인화·후속 모듈 연결

이 문서는 0.3.0 기준입니다. 코어 함수에는 DB가 필요하지 않습니다. 제공 `Store`를 사용할 때만 아래를 적용합니다.

## 누가 무엇을 하나요?

- AI 코어: 받은 입력으로 코칭·계획을 작성하고 계약·실행 조건을 검사합니다.
- Store: 입력 저장 → 트랜잭션 종료 → AI 호출 → 결과·목표·과제·포인터 동시 저장을 담당합니다.
- SW 서버: 로그인한 사람 확인, 모임 소속, 실제 투표자, 저장·조회 권한, 운영 활성화를 담당합니다.
- 테스트 웹의 발언자 선택은 인증이 아닙니다. `/api/`를 운영 API로 외부에 공개하면 안 됩니다.

## ID를 연결하는 방법

`external_user_id`는 SW가 가진 고정 사용자 ID입니다. `member_id`는 해당 모임의 구성원 ID입니다.
이름이 같아도 합치지 않습니다. 같은 사용자가 모임 두 개에 참여하면 external_user_id는 같고, 개인 조건·대화·계획은 각각의 group_id 아래에 따로 저장합니다.

```python
members = [{"member_id":"membership-123", "nickname":"테스트 사용자",
            "external_user_id":"authenticated-user-789"}]
view = store.create("익명 연결 예제", members)
# 기존 미연결 구성원은 SW의 검증된 매핑만 연결합니다.
store.link_user(group_id, member_id, authenticated_user_id, action_id)
```

기존 사용자 연결을 다른 ID로 자동 교체하지 않습니다. 과거 미연결 자료는 닉네임으로 추측해 연결하지 않습니다.
본인 자료 조회는 `store.reader(ActorContext(external_user_id=검증된_ID))`로 생성합니다.

## 환경 설정과 최소 권한

```powershell
.venv\Scripts\python.exe -m pip install ".[database]"
```

앱 서버에는 다음만 등록합니다.

| 환경변수 | 용도 |
|---|---|
| `WEMEET_COACH_DATABASE_URL` | DB 이름까지 포함한 명시적인 PostgreSQL 앱 접속 주소 |
| `WEMEET_COACH_RECOVERY_KEY` | 암호화된 미커밋 응답을 복구하는 Fernet 키 |
| `TIMELY_API_KEY` | 실제 AI 실행을 요청할 때만 필요한 서버 키 |

앱 접속 계정은 NOSUPERUSER·NOCREATEDB·NOCREATEROLE을 사용합니다.
조회·삽입과 rooms/private_states/operations의 상태 갱신, proposals.state, votes의 선택 변경, members.external_user_id 연결 권한만 부여합니다.
불변 대화·목표·개인 목표·계획·과제·기준은 UPDATE/DELETE하지 않습니다. 실제 변경은 새 버전입니다.
관리자 접속 정보 `WEMEET_COACH_ADMIN_DATABASE_URL`은 마이그레이션·백업 프로세스에만 전달합니다.
키·암호·전체 접속 URL을 로그나 전달 문서에 적지 않습니다. 복구 키는 배포 파일과 분리해 안전하게 보관해야 합니다.

로컬 새 프로젝트를 최초 준비하는 도구는 다음과 같습니다. 관리자만 명시적으로 실행합니다.

```powershell
.venv\Scripts\python.exe -m tools.provision --admin-env WEMEET_COACH_ADMIN_DATABASE_URL
```

이 편의 도구는 `wemeet_coach_v2`와 제한 역할 `wemeet_coach_app`을 준비합니다. 일반 `Store`는 이 DB 이름으로 고정되지 않습니다.
SW의 별도 개발·시험·운영 DB를 사용할 때는 해당 DB와 계정을 관리자가 먼저 준비한 후 다음처럼 예상 이름을 명시해 마이그레이션합니다.
기존 마이그레이션의 권한 부여 대상 역할은 `wemeet_coach_app`이므로 관리자는 먼저 해당 제한 역할을 준비해야 합니다.

```powershell
$env:WEMEET_COACH_EXPECTED_DATABASE = '관리자가_준비한_DB_이름'
.venv\Scripts\python.exe -m alembic upgrade head
```

관리자 URL의 DB 이름과 예상 이름이 다르면 거부합니다. 기존 `wemeet_goals`는 거부합니다.
일반 서버 실행은 마이그레이션·계정 생성·암호 변경을 실행하지 않습니다. SQLite로 자동 대체하지 않습니다.

## 저장·복원·중복 요청

1. SW가 `action_id`, 요청, 소유 범위를 먼저 보관합니다.
2. `store.message(...)` 또는 `store.execute(...)`로 사용자 입력을 커밋합니다.
3. DB 잠금을 풀고 AI를 호출합니다.
4. 응답을 검증하고 목표·계획·과제·현재 포인터와 함께 커밋합니다.
5. 커밋된 `persistence.state`를 보고 저장 상태를 표시합니다.

동일 ID·동일 입력은 기존 실행을 반환하며 신규 HTTP는 0회입니다. 다른 입력을 같은 ID로 보내면 Conflict입니다.
revision이 바뀌었다면 최신 자료를 다시 읽고 사용자의 새 동작으로 처리합니다.
`COMPLETE`는 결과 기록이 저장됐다는 뜻입니다. 결과 자체가 FAILED일 수도 있습니다.
`STALE`은 실행 중 입력이 바뀐 응답을 보존한 것이며 현재 승인값을 바꾸지 않습니다.

AI 응답 후 커밋 실패 시 응답은 암호화된 복구 파일에 보관합니다.
`store.retry_save(group_id, action_id, owner=본인_ID_또는_None)`는 저장만 재시도합니다. 모델 runner를 받지 않습니다.
응답을 받았는지 알 수 없는 UNKNOWN은 자동 재호출하지 않습니다. 외부 API의 정확히 한 번 실행을 보장하지 않습니다.
서버가 다른 경우 같은 암호화 복구 저장소·키에 안전하게 접근할 수 있어야 저장만 복구할 수 있습니다. 복구 파일이 없으면 재생성했다고 속이지 않고 확인 필요로 남깁니다.

## 최소 문맥 조회

```python
reader = store.reader(ActorContext(external_user_id=authenticated_user_id))
context = reader.context(group_id, scope="PERSONAL")
```

- `scope`: 필수 GROUP/PERSONAL. 본인 소유자는 인증 문맥에서 결정합니다.
- `goal_id`, `personal_goal_id`, `plan_version_id`, `baseline_id`: 정확한 저장 버전입니다.
- `confirmed_conditions`: 같은 모임의 확인된 조건만 제공합니다. 현재 버전이 아닌 과거 계획은 당시의 조건을 제공합니다.
- `tasks`: 저장된 계획과 관계가 검증된 과제입니다.
- `input_versions`, `record_revision`, `is_current`, `requires_review`: 어떤 입력 기준인지 나타냅니다.
- `readiness`: READY / NEEDS_INPUT / REVIEW_REQUIRED. 필요한 입력은 needed_inputs에 있습니다.

전체 조회는 한 번의 REPEATABLE READ·READ ONLY 트랜잭션에서 구성합니다. FOR UPDATE 잠금을 걸지 않습니다.
대화 원문·개발자 요청·개인 비공개 조언은 기본 결과에 없습니다. 필요한 다음 모듈에 이 작은 문맥을 전달하세요.
`reader.personal(...)`은 기존 상세 조회로 유지하지만, 다음 모듈 입력으로 무조건 통째로 전달하지 마세요.

PERSONAL의 확인 전 초안은 NEEDS_INPUT입니다. 조건 변경·옛 계획·관계 오류는 REVIEW_REQUIRED입니다.
READY는 다음 모듈에 전달할 저장 조건이 갖춰졌다는 뜻이며 AI의 의미적 품질·서비스 권한·활성화까지 자동 승인한 뜻이 아닙니다.

## 평가 기준과 과거 버전

공동 전원 찬성 시 공동 평가 기준, 본인의 공개 요약 확인 시 개인 기준을 생성합니다.
`baseline_id + plan_version_id + task_id`를 함께 보관하세요.

```python
criterion = reader.baseline(group_id, baseline_id, plan_version_id, task_id)
```

서로 다른 계획의 task_id나 다른 모임의 기준을 섞으면 거부합니다.
계획 변경은 새 버전·새 과제·새 기준으로 저장하며 과거 기준을 덮어쓰지 않습니다.
과거 버전은 `reader.context(..., plan_version_id=...)`로 명시합니다. 생성 시각의 최신순 추정으로 사용하지 않습니다.

## 백업·복원·장애 시험

```powershell
.venv\Scripts\python.exe -m tools.db_maintenance audit --expected-database wemeet_coach_v2
.venv\Scripts\python.exe -m tools.db_maintenance restore-test --expected-database wemeet_coach_v2
.venv\Scripts\python.exe -m tools.db_maintenance install-test --expected-database wemeet_coach_v2
```

관리자 환경에서 실행합니다. 읽기 전용 감사, 일관된 pg_dump 스냅샷, 별도 임시 DB 복원·해시 비교를 합니다.
기존 DB나 PostgreSQL 클러스터를 중단하지 않습니다. 임시 DB는 검증용 접두사·정확한 이름을 확인한 뒤 제거합니다.
원본 DB·대화·불변 기록·복구 파일·실제 평가 원본은 코드 정리 대상으로 삭제하지 않습니다.
결과와 백업은 Git에서 제외된 runtime에 저장합니다. 공개 전달 묶음에는 넣지 않습니다.
