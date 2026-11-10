# WE·MEET 공동·개인 계획 모듈 0.3.0

> **운영 인수 보류:** 5회 개선·실제 평가를 완료했지만, 새 제약 추가 시 기존 제약 대체와 반복 작업·완성 단계 누락이 남았습니다. [최종 보고서](docs/REVIEW_030.md)를 읽고 개발·검토용으로 사용하세요. 자동 운영 활성화에 연결하지 마세요.

**대화를 읽어 공동 계획을 제안하고, 본인의 조건을 확인해 개인 실행 계획을 만드는 Python 패키지입니다.**
SW팀은 필요한 함수 하나만 가져다 써도 됩니다. 웹 화면은 이 함수를 시험하는 도구입니다.
이 폴더는 이전 `wemeet_ai` 0.15.0과 분리된 프로젝트입니다. 기존 코드·8010 서버·`wemeet_goals` DB를 가져오거나 변경하지 않습니다.

## 1. 모듈부터 이해하기

| 필요한 기능 | 함수와 action | 입력 | 결과 |
|---|---|---|---|
| 모임 대화에 조언 | `group_coach`, `COACH` | 공개 대화·구성원 | 정해진 내용·결정할 내용·추천·다음 행동 |
| 공동 목표와 작업 제안 | `group_coach`, `PROPOSE` | 공개 대화·구성원 | 목표·기간·완료 기준·역할·일정·과제 초안 |
| 반대 이유로 수정 | `group_coach`, `REVISE` | 기존 제안·수정 이유 | 새 제안. 기존 찬성은 승계하지 않음 |
| 개인 버튼 상담 | `personal_coach`, `TALK` | 공개 목표·본인 역할·본인 대화·state | 답변·다음 질문·선택 버튼·개인 조건 |
| 개인 계획 작성 | `personal_coach`, `PLAN` | 공동 목표·확인된 본인 조건 | 결과물·수행일·기한·시간·완료 기준 |
| 코드로 검사 | `validate_plan` | 계획·비교 조건 | `valid`, `issues`, `not_checked` |

이전 A~F 화면의 분석·초안·계획을 사용자가 차례로 실행할 필요는 없습니다. 공동 제안은 한 번의 논리 작업에서 작성합니다.
공동 코칭과 개인 코칭은 독립 호출입니다. 개인 코칭에는 완성된 공동 계획 대신 목표와 본인 역할을 직접 전달해도 됩니다.
AI는 **사람이 할 일의 계획**을 만듭니다. PPT 파일이나 프로그램을 대신 제작하는 모듈이 아닙니다.

- **초안**: AI 추천 또는 사용자 수정본. 승인된 사실과 구별합니다.
- **state**: 개인 코치가 확인한 조건·현재 질문·답변 이력. 다음 요청에 그대로 전달하는 JSON 자료입니다.
- **JSON**: Python 사전과 비슷한 저장·전송 형식입니다. `model_dump(mode="json")`으로 만듭니다.
- **버전 ID**: 어느 시점의 목표·계획인지 구별하는 고정 식별자입니다. 제목이나 가장 최근 날짜로 추정하지 않습니다.
- **토큰**: 모델이 읽고 생성한 사용량입니다. 청구 금액과 같다고 표시하지 않습니다.

## 2. 설치 — 먼저 DB 없이 확인하기

Python 3.11 이상이 필요합니다. 아래 명령은 이 README가 있는 `group-coach-v2` 폴더에서 PowerShell로 실행합니다.

```powershell
python --version
python -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe examples\core.py --check
```

마지막 명령은 입력 형식만 검사합니다. **가짜 AI 응답을 반환하지 않으며 API·DB를 사용하지 않습니다.**
배포 wheel을 받았다면 설치 명령을 다음으로 바꿉니다.

```powershell
.venv\Scripts\python.exe -m pip install .\dist\wemeet_coach-0.3.0-py3-none-any.whl
```

기본 설치에는 `requests`와 `pydantic`만 필요합니다. PostgreSQL·FastAPI·Node·React는 코어 import에 필요하지 않습니다.

## 3. 실제 AI 호출하기

서버의 환경변수에 `TIMELY_API_KEY`를 등록합니다. 키를 Python 코드·JSON·브라우저·Git에 넣지 마세요.
기본 모델은 `openai/gpt-5.6-luna`, 주소는 `https://hello.timelyai.io/api/v2/chat/bridge/openai`입니다.
원하면 `TIMELY_MODEL`, `TIMELY_BASE_URL`로 서버에서 설정합니다. 키 누락·연결 실패 시 다른 모델이나 가짜 답변으로 바꾸지 않습니다.

```powershell
.venv\Scripts\python.exe examples\core.py --live
.venv\Scripts\python.exe examples\core.py --module personal --check
# 개인 코치를 실제로 호출하려면 --module personal --live
```

`--live`는 **유료 호출**입니다. 예제 불러오기·저장·조회·검증에는 AI 호출이 없습니다.
`examples/core.py`는 `examples/group-request.json`을 읽습니다. 같은 형태로 SW의 공개 대화를 넣으면 됩니다.

```python
from wemeet_coach import GroupRequest, group_coach

request = GroupRequest.model_validate({
    "action": "COACH",
    "reference_date": "2026-11-07",
    "members": [{"member_id": "member-a", "nickname": "테스트 구성원"}],
    "conversation": [{
        "message_id": "message-1", "speaker_id": "member-a",
        "text": "11월 10일부터 24일까지 발표를 준비하려고 해. 계획을 같이 정리해줘."
    }]
})
result = group_coach(request)
response = result.model_dump(mode="json")
```

구성원은 1명·2명·3명·5명·8명 등 입력 목록으로 정합니다. 2명으로 고정되지 않습니다.
`member_id`는 모임 구성원 ID, `speaker_id`는 해당 발언의 작성자 ID입니다. 닉네임으로 ID를 대신하거나 같은 이름을 합치지 마세요.
`external_user_id`는 SW가 인증한 고정 사용자 ID입니다. 모델에 보내는 공개 구성원 입력에서는 제거됩니다.

## 4. 결과를 읽고 다음 동작 연결하기

모든 AI 함수는 `Result`를 반환합니다.

```python
if result.status == "OK":
    # data를 표시하고 저장합니다. 자동 승인하지 않습니다.
    print(result.data.reply)
elif result.status == "NEEDS_INPUT":
    # 실패가 아닙니다. 같은 화면에서 필요한 답변·조건을 받습니다.
    print(result.needed_inputs)
else:
    # 인증·통신·응답 검증 오류입니다. 성공 결과와 사용자 답변은 보존합니다.
    print(result.error.code, result.error.message, result.error.retryable)
```

`data`, `needed_inputs`, `error`, `meta`를 함께 저장하세요.
`meta`에는 실행 ID·프롬프트 버전·모델·실제 요청 ID·사용량·전송 수가 있습니다.
`summary`는 짧은 진행 정리, `details`는 이유·대안·의존 작업을 담은 펼쳐보기 설명입니다. `reply`도 유지합니다.

`validate_plan`은 LLM 함수가 아닌 순수 검사 함수입니다. **Result.status 대신 `report["valid"]`를 확인합니다.**
`not_checked`에 있는 의미적 적절성·실제 권한까지 검증했다고 해석하면 안 됩니다.

| 상황 | 다음 동작 |
|---|---|
| `AUTH_FAILED` | 서버 키 설정 확인. 같은 키로 반복 호출하지 않음 |
| `TIMEOUT`, `RATE_LIMITED`, `UPSTREAM_ERROR` | 일시 오류 재시도 가능. 저장 어댑터의 동작 상태부터 확인 |
| `SCHEMA_INVALID`, `PLAN_INVALID` | 오류가 발생한 해당 단계·입력만 확인. 성공한 다른 개인 결과 보존 |
| `INPUT_INVALID` | `needed_inputs`의 필드 경로를 수정. AI 호출 전 입력 오류 |
| `STORAGE_FAILED` | `retry_save`로 **저장만 재시도**. AI를 다시 생성하지 않음 |
| `UNKNOWN` | 수신 여부 불명. 자동 유료 재호출하지 않고 실행·복구 상태 조사 |

연결·읽기 제한은 10초·60초이며 일시 통신 오류만 한 번 재시도합니다. JSON·계획 보정까지 실제 HTTP는 모듈 실행당 최대 4회입니다.
이는 무한 호출 방지이며 설명·분석 깊이를 짧게 제한하는 규칙이 아닙니다. 출력 잘림은 정상 성공이 아닙니다.

## 5. 공동 계획과 개인 버튼 상담

`PROPOSE` 결과는 투표 전 초안입니다. `approval_status`는 SW가 실제 상태로 전달합니다.
채팅의 “좋아/ㅇㅇ”는 의견의 근거이며 공식 전원 승인으로 바꾸지 않습니다.
제공 저장 어댑터는 제안 버전별 대상 구성원 **전원 찬성**일 때만 공동 목표·계획·기준을 확정합니다.
반대 이유를 `REVISE`에 전달하면 수정 제안을 만듭니다. 수정 버전은 다시 투표합니다.

마감만 바꾸려면 `revision_fields=["end_date"]`를 전달할 수 있습니다. 나머지 목표 조건은 보존됩니다.
명확한 “최종 마감만” 요청도 좁은 날짜 수정으로 처리합니다. 폭넓은 수정은 일반 수정 코치가 담당합니다.

개인 `TALK` 입력에는 본인의 자료만 넣으세요. 결과의 `state.question`에 버튼이 있으면 표시합니다.
선택 시 해당 문구를 본인 대화에 저장하고 다음 요청을 보냅니다.

```python
payload["state"] = previous_response["data"]["state"]
payload["conversation"].append({
    "message_id": "answer-2", "speaker_id": owner_member_id, "text": selected_label
})
payload["answer"] = {
    "question_id": current_question_id, "option_id": selected_option_id,
    "message_id": "answer-2"
}
# personal_coach(PersonalRequest.model_validate(payload))
```

질문 ID와 선택지 ID는 코어가 반환한 값을 씁니다. 버튼·자유 입력·조건 수정은 같은 정규화 함수를 사용합니다.
모름(`UNKNOWN`)·건너뜀(`SKIPPED`)·제약 없음(`KNOWN`, 빈 목록)은 서로 다릅니다.
질문 두 번 뒤에는 중간 정리를 하며 필요한 진단을 계속합니다. 현재 정보로 계획을 요청할 수도 있습니다.
필수 수준·시간·기여 범위가 없으면 같은 상담에서 보완하고, 도구·선호·제약 누락만으로 막지 않습니다.

`preferred_times`는 선호입니다. `available_weekdays`만 명시적으로 확인한 가능 요일(월0~일6)입니다.
과제의 `scheduled_date`는 수행 예정일, `due_date`는 완료 기한입니다. 예전 자료에 수행일이 없으면 기한을 수행일로 검사합니다.
주간 예산은 공동 시작일부터 7일씩 계산합니다. `time_allocation`은 코드가 계산한 실제 합계이며 가용 상한과 다릅니다.
새 조건으로 기존 확인값이 바뀌면 `NEEDS_INPUT`으로 재검토를 요청하고, 바뀐 조건·근거를 보존합니다.

## 6. 저장하고 다시 이어 쓰기

DB는 선택 어댑터입니다. 코어는 DB를 직접 읽지 않습니다. SW의 DB를 써도 되고 제공 PostgreSQL 어댑터를 써도 됩니다.

```powershell
.venv\Scripts\python.exe -m pip install ".[database]"
```

앱용 `WEMEET_COACH_DATABASE_URL`, 암호화 복구용 `WEMEET_COACH_RECOVERY_KEY`가 필요합니다.
DB 이름은 고정하지 않습니다. **명시적으로 전달한 PostgreSQL만 사용하며 다른 DB·SQLite로 대체하지 않습니다.**
관리자 URL은 `WEMEET_COACH_ADMIN_DATABASE_URL`로 분리하고 일반 웹 서버에 전달하지 않습니다.
새 로컬 개발 DB·제한 계정 준비는 관리자만 `tools/provision.py`를 명시적으로 실행합니다. 서버 시작이 계정·스키마를 만들지 않습니다.
상세 준비·권한·마이그레이션은 [저장 안내](docs/USER_STORAGE.md)를 따르세요.

```python
import os
from pathlib import Path
from wemeet_coach.persistence import Store
store = Store(os.environ["WEMEET_COACH_DATABASE_URL"],
              Path("private-runtime/recovery"), os.environ["WEMEET_COACH_RECOVERY_KEY"])

# SW는 호출 전에 action_id와 payload를 저장합니다.
result = store.execute(group_id, action_id, "group.PROPOSE", {}, expected_revision)
```

`action_id`는 사용자 동작 한 번의 ID입니다. 중복 클릭·네트워크 재전송에는 **같은 ID와 같은 입력**을 씁니다.
다른 입력을 같은 ID로 보내면 충돌입니다. 새 제안을 실제로 요청할 때만 새 ID를 만듭니다.
AI 호출 전 메시지와 입력 버전을 커밋합니다. 네트워크 대기 중 DB 잠금을 유지하지 않습니다.
응답·계획·과제·현재 포인터는 함께 커밋한 뒤 저장 완료를 반환합니다.

`persistence.state=COMPLETE`는 **결과 기록의 저장 완료**입니다. `status=FAILED`인 실패 기록도 저장될 수 있으므로 두 값을 함께 확인하세요.
`STALE`은 이전 입력 기준으로 보관한 응답입니다. 현재 확정값을 덮어쓰지 않습니다.

```python
# 저장 실패·종료 후 재개. 이 함수에는 AI 실행 경로가 없습니다.
result = store.retry_save(group_id, action_id, owner=owner_member_id_or_none)
```

전체 예제: `examples/stored_flow.py --live`. 모의 찬성은 익명 예제에만 사용하며 서비스 자동 승인 코드가 아닙니다.
중단 후 `--group-id <id>`로 조회하고, `--resume-action-id <id>`를 함께 주면 공개 실행의 저장만 재시도합니다.
예제는 `runtime/example-checkpoint.json`에 공개 실행 ID를 먼저 저장합니다.
개인 대화는 본인 확인 후 기여 목표·공개 작업·기한만 모임에 알립니다. 원문·수준·시간·제약은 공개하지 않습니다.

## 7. 다음 모듈은 필요한 문맥만 조회하기

```python
from wemeet_coach import ActorContext
reader = store.reader(ActorContext(external_user_id=authenticated_sw_user_id))
context = reader.context(group_id, scope="PERSONAL")
# scope="GROUP"이면 공동 자료만 반환합니다.
# 과거 버전은 plan_version_id="..."를 명시합니다.
```

`scope`는 필수입니다. PERSONAL은 인증된 본인만 조회하며 owner를 다른 사람으로 바꿀 수 없습니다.
`READY / NEEDS_INPUT / REVIEW_REQUIRED`와 `needed_inputs`를 확인하세요.
현재 계획은 저장된 포인터, 과거 계획은 명시적 ID로 찾습니다. 미확인 개인 초안에는 평가 기준 준비 완료를 표시하지 않습니다.
조건·계획·기준을 **하나의 읽기 전용 DB 스냅샷**에서 읽습니다.
대화 원문·개발자 요청·이전 실행 로그는 기본 문맥에 포함하지 않습니다.

M2 등 후속 모듈은 `baseline_id + plan_version_id + task_id`로 기준을 조회합니다.
`reader.baseline(group_id, baseline_id, plan_version_id, task_id)`는 ID 관계·소유 범위를 확인합니다. M2 평가 기능 자체는 제공하지 않습니다.
사용자 ID는 여러 모임에서 같아도, 개인 대화·조건·계획은 **같은 모임 안에서만** 재사용합니다.
명령 예제: `examples/read_goals.py --user-id <인증된 ID> --group-id <ID> --scope PERSONAL` (AI 호출 없음).

## 8. 테스트 화면과 SW 책임

```powershell
.venv\Scripts\python.exe -m pip install ".[database,web]"
.\start.ps1
```

브라우저에서 `http://127.0.0.1:8020/`를 엽니다. 공동 채팅·전원 투표·개인 버튼 상담·계획 검토를 시험합니다.
200개 예제는 **입력 대화**입니다. 불러오기만으로 AI를 호출하거나 작성된 답변을 LIVE로 표시하지 않습니다.
개발자 펼침에서 실제 요청·응답·토큰·실행 ID를 볼 수 있습니다. 키는 표시하지 않습니다.

`/api/`는 로컬 실험용으로 테스트 발언자를 선택합니다. **운영 인증 API가 아닙니다.** 외부에 그대로 공개하지 마세요.
`/integration/v1/` 조회는 SW가 검증한 identity resolver가 없으면 거부합니다. 사용자 입력·헤더 ID를 인증으로 믿지 않습니다.
SW는 로그인·소속·접근 권한·실제 투표자·최종 활성화·운영 보관 정책을 담당합니다.

## 9. 검사와 검증 범위

```powershell
.venv\Scripts\python.exe -m pip install ".[test,database,web]"
.venv\Scripts\python.exe -m pytest -q
```

DB 검사는 별도 `WEMEET_COACH_TEST_DATABASE_URL`을 설정해야 합니다. 없으면 해당 검사는 SKIPPED이며 전체 DB 통과로 집계하지 않습니다.
일반 단위 검사는 실제 HTTP를 막고 주입한 통신만 사용합니다. 이것은 LIVE 품질 증거가 아닙니다.
`evaluations/improvement.py`는 고정60·보호20의 실제 평가를 소스 해시별로 저장·재개합니다. `--live` 없이는 유료 호출하지 않습니다.
과거 `review_modules.py`는 중복 제거했습니다. 이전 검토 보고서는 당시 기록이며 현재 실행 명령이 아닙니다.

최신 결과·남은 결함·실제 호출 수는 [0.3.0 개선 보고서](docs/REVIEW_030.md)를 보세요.
이전 [0.2.0 검토](docs/REVIEW_020.md)는 과거 이력입니다. API 성공·코드 검사·모델 심사·에이전트 검토를 구분하며 사람 인수 완료로 표시하지 않습니다.
