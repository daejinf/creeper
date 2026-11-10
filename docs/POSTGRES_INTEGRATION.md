# SW팀: AI 실행과 PostgreSQL 저장을 연결하기

이 문서가 현재 저장 방식입니다. A~F 독립 함수는 DB 없이 사용할 수 있습니다. **저장이 필요한 프로그램은 `ExecutionAdapter`를 사용합니다.** SQLite는 이전 자료를 읽는 이관 도구·과거 회귀 검사에만 남아 있습니다. 서버는 PostgreSQL 연결 실패 시 SQLite로 바꾸지 않습니다.

## 1. 처음 실행하기

프로젝트 최상위 폴더에서 실행합니다. Python 3.11+, PostgreSQL 16을 사용합니다. Python 기본 설치에는 DB·FastAPI·Node가 필요하지 않습니다. 저장 어댑터만 DB 선택 의존성을 요구합니다.

```powershell
python -m pip install '.[database]'
$env:WEMEET_GOAL_DATABASE_URL = [Environment]::GetEnvironmentVariable('WEMEET_GOAL_DATABASE_URL','User')
$env:WEMEET_RECOVERY_KEY = [Environment]::GetEnvironmentVariable('WEMEET_RECOVERY_KEY','User')
python -m examples.stored_module
```

마지막 예제는 **실제 PostgreSQL 저장 + 작성된 fixture 응답**이며 유료 호출 0회입니다. `--live`를 붙이고 `TIMELY_API_KEY`를 서버 환경에 등록하면 실제 GPT를 호출합니다. 예제의 `confirmed=true`는 익명 모의 확인입니다. 서비스에서는 실제 사용자 확인 후 전달해야 합니다.

## 2. SW가 사용하는 최소 코드

```python
import os
from uuid import uuid4
from wemeet_ai.persistence import (
    PostgresRecordStore, ExecutionAdapter, EncryptedRecoveryStore,
)

store = PostgresRecordStore(os.environ['WEMEET_GOAL_DATABASE_URL'])
spool = EncryptedRecoveryStore('/private/recovery', os.environ['WEMEET_RECOVERY_KEY'])
adapter = ExecutionAdapter(store, spool)
record = store.create('팀 계획', members=[{'member_id': 'SW회원ID', 'nickname': '지민'}])
action_id = str(uuid4())  # 사용자가 실행 버튼을 한 번 누를 때 생성하고 저장
result = adapter.execute_and_store(
    module='D', record_id=record.record_id, action_id=action_id,
    expected_revision=record.revision, payload=confirmed_group_goal,
)
```

`payload`는 선택한 모듈의 기존 요청 JSON입니다. [모듈 계약](MODULES.md)과 `examples/requests/`를 참고하세요. D를 저장하려고 A/B/C를 먼저 실행할 필요가 없습니다. E는 `owner_member_id`와 **그 사람의 조건만** 받습니다. `owner_member_id`는 SW의 로그인 결과로 정해야 하며 브라우저가 보낸 값을 신뢰하면 안 됩니다.

## 3. 반환값을 어떻게 처리하나요?

AI 결과 `status`와 DB 결과 `persistence.persistence_status`는 별개입니다.

| 값 | 의미 | SW의 다음 동작 |
|---|---|---|
| `OK` | 모듈 결과 생성 성공 | 저장 상태도 확인하고 표시 |
| `NEEDS_INPUT` | 필수 입력 부족 | `needed_inputs`를 한 번에 표시 |
| `FAILED` | API·검증 실패 | `error.code`, `retryable` 확인 |
| `PERSISTED` | DB 커밋 완료 | 저장 완료 표시 |
| `STALE` | 결과는 보존했지만 입력이 바뀜 | 이전 입력 기준 표시, 현재 목표로 자동 승격 금지 |
| `RUNNING` | 다른 요청이 같은 동작을 실행 중 | `adapter.status(record_id,action_id)` 조회 |
| `PERSIST_FAILED` | 결과 수신 후 DB 커밋 실패 | **같은 action_id·payload로 저장만 재시도** |
| `UNKNOWN` | 프로세스 종료 등으로 수신 여부 불명 | 운영자가 확인. 자동 유료 재호출 금지 |

`operation_id`는 DB 실행 식별자입니다. `record_revision`은 저장 후 기록 버전입니다. `entity_ids`는 생성된 계획·목표 식별자이며 과제 ID는 `data.tasks[].task_id`에 있습니다. 읽기·수락·코드 검증은 AI를 호출하지 않습니다. 같은 action ID에 다른 입력은 `RevisionConflict`입니다. 바뀐 입력을 검토한 사용자가 새 실행을 요청할 때만 새 action ID를 생성합니다.

## 4. 저장 순서와 장애 복구

1. 기록 행을 잠그고 예상 버전을 검사합니다. 입력·메시지·실행 소유권을 커밋합니다.
2. DB 트랜잭션을 종료한 뒤 AI를 호출합니다. 네트워크 대기 중 행 잠금은 없습니다.
3. 응답을 암호화 복구 파일에 기록합니다. 키가 없으면 실행을 시작하지 않습니다.
4. 실행 결과·목표/개인 목표·계획·과제·현재 포인터를 같은 트랜잭션으로 저장합니다.
5. 커밋 이후 복구 파일을 제거합니다. 삭제만 실패한 경우 DB 성공을 실패로 바꾸지 않습니다.

DB가 처음부터 끊겼으면 AI를 호출하지 않습니다. DB 저장이 실패하면 `PERSIST_FAILED`와 `SAVE_ONLY`를 반환합니다. 복구 파일과 키를 같은 서버 범위에서 유지한 채 같은 동작을 다시 보내세요. 파일에는 Fernet 암호문만 있습니다. 키를 잃으면 복호화할 수 없습니다. 서버가 여러 대면 복구 파일을 해당 실행 노드에서 처리하거나 접근 통제된 공유 저장소를 사용해야 합니다.

10분이 지난 소유권 미확인 RUNNING은 다음 조회/재전송에서 UNKNOWN으로 취급합니다. 외부 모델 API의 정확히 한 번 실행은 보장하지 않습니다. 이미 수신했는지 모르는 요청을 자동으로 다시 보내지 않는 정책입니다.

## 5. 데이터와 권한

`wemeet_goal` 스키마의 17개 테이블을 사용합니다. `records`, `conversation_messages`, `module_inputs`, `execution_artifacts`, `module_operations`가 기존 목표·계획 테이블과 같은 DB에 있습니다. 공동 목표 → 개인 기여 목표 → 계획 버전 → 과제 → 평가 기준은 명시적 외래 키로 연결됩니다.

- `store.load(record_id)`: 공개 대화·결과만.
- `store.load(record_id,owner_member_id=본인ID)`: 공개 자료 + 본인 개인 자료.
- `internal=True`: 서비스 내부 진단 전용. 그룹 화면에 반환하면 안 됩니다.
- `store.goals.public_group(record_id)`: 공개 가능한 목표·기여·공동 계획.
- `store.goals.private_member(record_id,본인ID)`: 본인 조건·계획.

이것은 로그인·테넌트 권한 시스템이 아닙니다. SW가 소속·본인 여부를 확인한 후 호출해야 합니다. 앱 계정은 NOSUPERUSER/NOCREATEDB/NOCREATEROLE이고 스냅샷 UPDATE/DELETE·DDL·권한 부여를 허용하지 않습니다. 앱 계정 자체에는 저장 기능 수행을 위한 테이블 조회 권한이 있으므로, 신뢰하지 않는 사용자에게 DB 자격증명을 제공하지 않습니다.

일반 대화 추가는 분석을 오래된 상태로 표시하지만 확정 목표를 삭제하지 않습니다. 검토 값이 실제 변경되면 기존 확인은 더 이상 준비 완료가 아닙니다. 개인 대화/조건 변경은 해당 개인 계획만 재검토 대상으로 만듭니다. 과거 버전과 평가 기준은 보존합니다.

## 6. 평가 기준을 다음 모듈에 전달하기

```python
# 사용자 본인이 현재 개인 계획을 명시적으로 확인한 시점에만 실행
baseline = store.goals.confirm_baseline(record_id, plan_version_id, authenticated_member_id)
context = store.goals.evaluation_context(
    record_id, plan_version_id, task_id, owner_member_id=authenticated_member_id,
)
```

`context`에는 baseline_id·공동/개인 목표 버전·plan_version_id·task_id·완료 기준·인증 방식이 있습니다. 제목으로 검색해 추측하지 마세요. 옛 계획의 신규 기준 확정은 거부합니다. 이미 확정했던 과거 기준은 과거 활동의 참조로 남습니다. M2 평가 기능은 이번 패키지에 포함하지 않습니다.

## 7. 관리·이관·백업

관리자 `WEMEET_GOAL_ADMIN_DATABASE_URL`은 마이그레이션·백업·복구에만 사용합니다. 앱 서버에는 전달하지 마세요. `start.ps1`은 계정 생성·권한 부여·마이그레이션을 실행하지 않습니다.

다른 SW 서버에서는 관리자가 `wemeet_goals` DB를 만든 뒤, 전달받은 migrations·alembic.ini가 있는 폴더에서 관리자 환경변수를 설정하고 `python -m alembic upgrade head`를 명시적으로 실행합니다. 그다음 [앱 권한 SQL](DB_APP_GRANTS.sql)을 관리자 psql에서 실행하고 `\password wemeet_app`으로 비밀번호를 숨김 입력합니다. 비밀번호를 SQL 파일에 적지 마세요. 앱에는 앱 URL과 복구 키만 전달합니다. 복구 키는 `cryptography.fernet.Fernet.generate_key()`로 한 번 생성해 비밀 저장소에 보관하는 별도 암호화 키이며, API 키나 DB 비밀번호를 대신 넣는 값이 아닙니다.

운영자가 저장만 복구할 때 이 PC에서는 `python -m tools.db.recover_saved`로 대상을 먼저 확인하고, `--apply`를 붙여 저장을 재시도합니다. 모델 호출은 하지 않습니다. 잘못된 확인값은 `RESULT_VALIDATION_FAILED` 실패 이력으로 보관하며 영구적인 저장 재시도 루프에 넣지 않습니다.

이 PC의 명시적인 최초 전환 명령은 `python -m tools.db.cutover` → `python -m tools.db.migrate_journal`입니다. 기존 데이터를 백업하고 해시를 검증한 후 적용했습니다. 재실행으로 과거 기록을 덮어쓰지 않습니다. 복원 연습은 `python -m tools.db.verify_restore`이며 별도 임시 DB를 만들어 테이블 수량을 대조하고 임시 DB만 제거합니다. 실제 운영 DB를 덮어쓰지 않습니다.

`runtime/journal-migration.json`은 이관 결과, `runtime/unified-live.json`은 익명 LIVE 실행 진행률입니다. runtime은 개인 자료를 포함할 수 있어 Git·배포 파일에 넣지 않습니다. 확인된 과거 불일치 2건은 원문과 실행 이력을 보존하고 보류합니다. 보류 기록은 새 AI 실행·현재 평가 기준에 사용하지 않습니다.
