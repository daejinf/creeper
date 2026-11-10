# WE·MEET AI Core 0.15.0

M0/M1은 **대화를 분석해 공동 목표·개인 기여 목표·실행 과제를 만드는 모듈**입니다. 모델 호출·코칭·분석·명세·계획·검증의 역할을 분리합니다.

**0.15.0: 대화부터 평가 기준까지 PostgreSQL 하나로 통합했습니다.** 앱 전용 계정·동시 실행 소유권·암호화 저장 재시도를 제공합니다.

**정식 목표 DB는 PostgreSQL입니다.** 공동 목표 버전 → 개인 목표 버전 → 계획 버전 → 과제 → 확인된 평가 기준을 연결합니다. 대화·개인 문답·실행 기록·사용자 확인도 같은 PostgreSQL에 저장합니다. SQLite는 백업한 읽기 전용 과거 자료이며 정상 서버에서 사용하지 않습니다. **SW팀은 [실행·저장·복구 연결 안내](docs/POSTGRES_INTEGRATION.md)를 먼저 읽으세요.** 설치·테이블 관계·SW 저장 코드·M2 참조 방법은 [목표 DB 안내](docs/GOAL_DATABASE.md)를 먼저 읽으세요.

**새 3단계 데모:** [SW 화면 안의 킥오프 채팅](http://127.0.0.1:8010/kickoff-demo) · [연결·저장·검증 범위](docs/KICKOFF_EMBED.md). 구성원은 가변 목록이며 생성 후에도 추가할 수 있습니다. 현재는 로컬 공개 채팅＋목표 검토＋개인 코칭＋공동·개인 실행 계획까지 연결했습니다.

**AI팀이 만드는 Python 모듈입니다. 웹서비스 완제품이 아닙니다.** SW팀은 필요한 모듈 하나만 사용할 수 있습니다. A를 실행해야 B가 열리는 방식이 아닙니다.

공동 목표와 개인 조건을 이미 DB에 가지고 있다면 **E의 개인 계획 생성만 호출**하세요. 대화 분석이 필요하면 B만 호출하면 됩니다.

## 바로 실행하기 (Windows)

Python 3.11 이상을 설치한 뒤, 이 README가 있는 폴더의 PowerShell 또는 명령 프롬프트에서 다음 한 줄만 실행하세요. `.venv` 폴더는 Git에 없어도 정상이며 첫 실행 때 자동으로 생성됩니다.

```powershell
.\run.cmd
```

처음 실행할 때는 가상환경과 필요한 패키지를 자동으로 설치합니다. Timely API 키가 등록되지 않았다면 숨김 입력으로 키를 요청하며, 입력한 키는 Windows 사용자 환경변수에 저장되어 다음 실행에도 사용됩니다. 키 없이 Enter를 누르면 REPLAY만 사용할 수 있습니다. 설치 후 터미널에 표시되는 `킥오프:` 주소를 브라우저에서 열고, 종료할 때는 `Ctrl+C`를 누르세요. 8010 포트가 사용 중이거나 Windows에서 차단된 경우 다른 포트를 자동으로 선택합니다. 특정 포트를 쓰려면 `.\run.cmd -Port 9000`, 자동 재시작 없이 실행하려면 `.\run.cmd -NoReload`, 키 질문을 건너뛰려면 `.\run.cmd -SkipApiKeyPrompt`, 설치만 미리 끝내려면 `.\run.cmd -SetupOnly`를 사용합니다.

> 0.13.0은 기본 연결을 Timely GPT로 변경했습니다. M1-A~F 독립 함수와 기존 SQLite 기록은 유지합니다. M0 직접 사용자는 `DeepSeekClient`를 `TimelyClient`로 바꾸고 `TIMELY_*` 환경변수를 사용하세요. 0.10.1과는 API가 달라졌습니다. `GoalAI`와 순차 실행 화면은 제거했습니다. [변경 안내](docs/MIGRATION.md)와 [실제로 검증한 범위](docs/VERIFICATION.md)를 확인하세요.

과거 DeepSeek 실제 운영 결과(현재 GPT 품질의 증거가 아님)와 수정 내역은 [4회 운영 보고서](docs/OPERATIONS_4.md)에서 확인하세요. 성공한 동일 결과는 재사용하고 실패한 단계만 다시 실행합니다.

## 1. 용어부터 이해하기

| 용어 | 뜻 |
|---|---|
| M0 | Timely GPT 통신·오류·토큰·캐시를 처리하는 공통 실행 기반 |
| M1 | 목표 코칭·분석·계획·검증 기능 A~F |
| 모듈 | 정해진 입력을 받아 결과를 반환하는 Python 함수 묶음 |
| API | 다른 프로그램이 정해진 방식으로 기능을 호출하는 약속 |
| 환경변수 | 서버 실행 프로세스가 읽는 설정. 키는 이곳에서만 읽음 |
| JSON | 요청과 응답을 파일이나 HTTP로 전달하는 데이터 형식 |
| request / result | 함수에 전달할 입력 / 함수가 반환한 결과 |
| state | 코칭에서 확인한 조건과 현재 질문. SW가 저장하고 다음 호출에 다시 전달 |
| 초안 | AI의 제안. 사용자 합의나 서비스 활성화가 아님 |
| 토큰 | 모델이 처리한 입력·출력 분량. 청구 금액과 같은 값이 아님 |
| 캐시 | 검증된 동일 성공 결과를 재사용하는 저장소 |
| fixture / REPLAY | 작성된 테스트 응답. 실제 AI가 만든 답이 아님 |

## 2. 폴더에서 무엇을 보면 되나요?

```text
src/wemeet_ai/m0/        통신·오류·캐시·실행 기록
src/wemeet_ai/m1/        A~F 독립 기능
src/wemeet_ai/shared/    공통 데이터 형식·개인정보 규칙
examples/               모듈별 실행 예제·요청·응답 파일
tests/                  유료 API 없는 회귀 검사
evaluations/            200개 익명 시나리오와 평가 도구
tools/bench/            선택적인 최소 테스트 화면
docs/                   SW 연결·계약·오류·검증 설명
```

처음에는 **이 README → `examples/a_coaching.py` → [모듈별 안내](docs/MODULES.md)** 순서만 보세요. 내부 코드를 읽거나 테스트 화면을 복제해야 사용할 수 있는 패키지가 아닙니다.

## 3. 설치하기

Python 3.11 이상이 필요합니다. 기본 AI 모듈만 설치할 때 Node.js·Docker·PostgreSQL은 필요하지 않습니다. 저장 어댑터·테스트 웹은 PostgreSQL과 `.[database]`를 추가로 사용합니다. 아래 명령은 모두 **README가 있는 폴더의 터미널**에서 실행합니다.

```powershell
python --version
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\python -c "import wemeet_ai; print(wemeet_ai.__version__)"
```

마지막 출력이 `0.15.0`이면 설치된 것입니다. macOS/Linux에서는 `.venv\Scripts\python` 대신 `.venv/bin/python`을 사용하세요. wheel 배포본은 `python -m pip install 파일명.whl`로 설치합니다.

Windows에서 `./setup.ps1`은 개발·테스트 화면용 선택 의존성까지 설치합니다. 스크립트 실행 정책으로 막히면 정책을 바꾸지 않고 위 Python 명령을 직접 실행해도 됩니다.

## 4. API 키 없이 먼저 실행하기

```powershell
.venv\Scripts\python examples/a_coaching.py
.venv\Scripts\python examples/b_analysis.py
.venv\Scripts\python examples/c_goal_draft.py
.venv\Scripts\python examples/d_group_plan.py
.venv\Scripts\python examples/e_member_plan.py
.venv\Scripts\python examples/f_validation.py
```

어느 파일부터 실행해도 됩니다. 이전 명령의 출력 파일이 필요하지 않습니다.

- A~E: `REPLAY · 작성된 fixture · AI 호출 없음`.
- F: `CODE · AI 호출 없음`.
- `request`는 입력, `response`는 결과입니다.
- fixture 성공을 실제 Timely GPT 품질 통과로 해석하지 마세요.

`--output result.json`을 붙이면 결과를 저장합니다. 고정 요청·응답은 `examples/requests/`와 `examples/responses/`에도 있습니다. 예제의 모의 사용자 확인은 실제 서비스에서 사용자 동작으로 교체해야 합니다.

## 5. 실제 AI 호출하기

서버를 실행하는 **같은 터미널**에서 환경변수를 설정합니다. 키를 Python 코드·브라우저·JSON·Git에 넣지 마세요.

```powershell
.\tools\set-timely-key.ps1  # 화면에 키를 표시하지 않고 Windows 사용자 환경변수에 등록
$env:TIMELY_API_KEY = [Environment]::GetEnvironmentVariable("TIMELY_API_KEY", "User")
$env:TIMELY_MODEL = "openai/gpt-5.6-luna"
$env:TIMELY_BASE_URL = "https://hello.timelyai.io/api/v2/chat/bridge/openai"
.venv\Scripts\python examples/a_coaching.py --live
```

`--live`에서만 실제 유료 API를 호출합니다. 키가 없거나 실패하면 오류를 반환하며 가짜 응답으로 자동 전환하지 않습니다. F 코드 검증은 항상 AI 호출이 없습니다. `.env.example`은 설명용이고 자동 로드하지 않습니다.

## 6. 필요한 모듈 고르기

| 원하는 일 | 모듈과 함수 | 필요한 입력 |
|---|---|---|
| 대화에 조언받기 | A `coach_reply` | 공개 대화·구성원·기준 날짜 |
| 사실·충돌 정리 | B `analyze_conversation` | 공개 대화·구성원·기준 날짜 |
| 목표 검토표 작성 | C `draft_goal_spec` | SW가 만든 facts 또는 B의 facts |
| 공동 계획 | D `generate_group_plan` | 확인된 공동 목표·날짜·완료 기준·운영 조건 |
| 개인 조건 대화 | E `coach_member_goal` | 공개 목표·본인 역할·본인 대화/state |
| 개인 기여 목표 | E `draft_member_goal` | 본인 대화/state |
| 개인 계획 | E `generate_member_plan` | 공개 목표·확인된 본인 조건·기여 목표 |
| 계획 검사 | F `validate_plan` | 외부 작성/AI 계획·비교 조건 |
| AI 수정 요청 | F `revise_plan` | 이전 계획·확인된 조건·수정 설명 |

**데이터는 필요하지만 이전 단계 실행 이력은 필요하지 않습니다.** E에 공동 계획 객체나 D의 ID를 넣을 필요가 없습니다.

### Python에서 E만 사용하는 예

```python
from wemeet_ai.m1.e_member_plan import MemberPlanRequest, generate_member_plan

payload = {
    "context": {
        "goal": "선택형 온라인 교류회 진행안 만들기",
        "role": "참여 설계",
        "start_date": "2026-12-01", "end_date": "2026-12-21",
        "completion_criteria": ["참여 방식 선택 가능", "강제 지목 없음"],
    },
    "owner_member_id": "로그인한-본인-ID",
    "contribution": {
        "title": "참여 안내 작성", "deliverable": "안내 문서 한 장",
        "completion_criteria": ["익명/공개 선택 안내 포함"],
        "confirmed": True,  # 실제 사용자 검토 후 SW가 전달
    },
    "profile": {
        "current_level": "모임 운영 일부 경험",
        "weekly_available_minutes": 30,
        "confirmed": True,  # 실제 사용자 확인 후 전달
    },
}
result = generate_member_plan(MemberPlanRequest.model_validate(payload))
if result.status == "OK":
    print(result.data.model_dump_json(indent=2))
elif result.status == "NEEDS_INPUT":
    print("보완할 항목:", result.needed_inputs)
else:
    print(result.error.code, result.error.retryable)
```

이 코드는 실제 API를 호출합니다. A~D나 테스트 웹을 실행할 필요가 없습니다. 각 모듈의 최소 예제는 [모듈 상세](docs/MODULES.md)에 있습니다.

## 7. 결과 읽기·저장·재실행

| status | 의미 | SW 처리 |
|---|---|---|
| `OK` | 해당 작업 완료 | 결과 표시·저장. 그룹 승인으로 해석하지 않음 |
| `NEEDS_INPUT` | 필요한 입력/확인 부족 | `needed_inputs` 전체를 같은 기능에서 보완 |
| `FAILED` | 통신·형식·검증 실패 | 기존 성공 보존, 실패한 호출만 재시도 |

F는 `status=OK`여도 **`data.valid=false`이면 검증 실패**입니다. `not_checked`는 검사하지 않은 내용입니다. 코드 검사만으로 의미적 품질·최종 승인을 보장하지 않습니다.

```python
import json
from pathlib import Path
from wemeet_ai.shared.privacy import sanitize

Path("result.json").write_text(
    json.dumps(sanitize(result.model_dump(mode="json")), ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

위는 파일 저장 예제입니다. SW 서비스에서는 권한이 있는 DB에 저장하세요. 개인 결과 파일은 그룹에 공유하지 마세요.

E 코칭은 반환된 `result.data.state`와 본인 대화를 다음 요청에 전달합니다. AI 실패 시 반환된 state도 보존하세요. 버튼 답변은 모델 호출 전에 반영됩니다. 다른 사람의 대화/state를 섞지 마세요.

`ValidationError`는 함수 실행 전 요청 형식이 잘못된 경우입니다. [오류 처리](docs/ERRORS.md)에 예제가 있습니다.

## 8. 선택적인 테스트 화면

```powershell
.venv\Scripts\python -m pip install ".[web]"
.venv\Scripts\python -m uvicorn tools.bench.server:app --host 127.0.0.1 --port 8010
```

또는 `./start.ps1`을 실행하고 http://127.0.0.1:8010/ 을 엽니다. `Ctrl+C`로 종료합니다. 화면에서 LIVE/REPLAY를 명시적으로 선택합니다. 조회·예제 읽기·JSON 저장·코드 검증은 AI를 호출하지 않습니다.

화면 도구는 저장소의 `tools`를 사용합니다. wheel만 설치한 프로그램에서는 코어 함수만 바로 사용할 수 있으며, 화면을 열려면 저장소도 필요합니다.

대화·작성 중 입력·검토·결과는 `runtime/records.sqlite3`에 자동 저장됩니다. 새로고침·서버 재시작 후 같은 기록을 엽니다. 예제는 새 기록으로 열리며 기존 기록을 덮어쓰지 않습니다. 이전 0.10 서비스 DB는 자동 복원되지 않습니다. 이 도구는 운영 인증 서버가 아니며 외부에 공개 배포하지 않습니다.

## 9. 대화를 끝낸 뒤 다음 모듈에서 이어 쓰기

모듈을 하나만 쓰는 방법은 그대로입니다. 이어 쓰고 싶을 때만 **순수 함수** `prepare_module_input`을 사용하세요. 이 함수는 DB를 읽거나 AI를 호출하지 않습니다. SW가 권한에 맞게 읽은 `RecordView`를 전달하면 요청과 부족한 입력·오래된 근거를 돌려줍니다.

```python
from wemeet_ai.continuity import RecordView, prepare_module_input
from wemeet_ai.m1.b_analysis import AnalysisRequest, analyze_conversation

# saved_record: SW가 자기 DB에서 읽은 공개 기록 dict
view = RecordView.model_validate(saved_record)
prepared = prepare_module_input("B", view)
if prepared.ready:
    request = AnalysisRequest.model_validate(prepared.request)
    # 여기까지는 무료입니다. 사용자가 실행한 뒤에만 아래 함수를 호출합니다.
    result = analyze_conversation(request)
else:
    print(prepared.needed_inputs)
```

복사해서 바로 실행하는 **키 없는 저장·종료·복원 예제**:

```powershell
.venv\Scripts\python -m examples.stored_module
```

작성된 fixture임을 명시한 예제이며 신규 HTTP는 0입니다. 정상 저장은 PostgreSQL입니다. SQLite 클래스는 과거 이관 회귀 검사에만 남아 있습니다. Python 패키지 자체는 SQLite·FastAPI를 import하지 않습니다. SW 저장소 계약, 실제 요청·응답, 개인 범위, 충돌·실패 복구는 [기록 연결 안내](docs/CONTINUITY.md)를 보세요.

화면에서는 **새 기록 / 기록 열기 / 저장 상태 / 이 기록으로 입력 준비**를 사용합니다. 모듈 선택·입력 준비·복원은 AI를 호출하지 않습니다. 입력이 바뀌면 예전 결과를 지우지 않고 `이전 기록 기준`으로 표시합니다. 개인 대상은 닉네임 메뉴로 선택합니다. LIVE/FIXTURE 실행 모드도 기록과 함께 복원됩니다.

## 10. 캐시·추적·사용량

`Runtime(cache=MemoryResultCache(), scope_id="인증된-작업-ID")`를 함수의 `runtime=`에 전달하면 같은 성공 결과를 재사용합니다. 개인 요청 키에는 본인 ID도 포함됩니다. 키 원문은 캐시에 저장하지 않습니다.

- `LIVE`: 실제 호출. `transport_attempts`, `usage`, `provider_request_ids` 확인.
- `CACHE`: 신규 usage=0. 기존 사용량은 `cached_usage`.
- `CODE`: AI 호출 없는 검토·확정·검증.
- `REPLAY`: 명시적으로 주입한 작성된 fixture.

`fresh=True`는 새 안을 명시적으로 요청할 때만 사용하세요. 전송·JSON·결과 보정을 합쳐 모듈 호출당 HTTP 최대 4회입니다. 메모리 잠금은 한 프로세스 범위입니다. 다중 서버 중복 방지는 SW 저장소가 담당합니다.

기본 추적에는 메타데이터만 기록합니다. 원문은 `Runtime(trace_sink=..., debug=True)`로 명시한 경우에만 기록하며 개인 대화가 남을 수 있습니다. 본인 권한 범위에 저장하세요.

## 11. SW 책임과 현재 한계

SW는 로그인, 소속 확인, 본인 ID 결정, DB 저장, 동시 편집, 사용자 확인, 그룹 투표·승인, 알림과 서비스 활성화를 담당합니다. `confirmed=True`는 권한 검증 장치가 아닙니다. SW 서버가 실제 확인 이력으로 결정해야 합니다.

계획의 `plan_version_id`와 과제의 `task_id`를 저장하세요. M2 기록은 이 두 값을 참조합니다. 수정은 새 버전으로 저장하고 과거 기준을 덮어쓰지 않습니다. M2 평가 기능은 포함하지 않습니다.

비공개 사유는 공개 계획 생성 입력에서 제외합니다. 자유문장 제약을 자동 일정으로 변환하지는 않습니다. 특정 요일 가용성은 별도 확인해야 합니다. 외부 공개 문장에 비공개 사유가 우회 표현됐는지 코드만으로 완벽히 판별하지는 못합니다.

[모듈 계약](docs/MODULES.md) · [오류·복구](docs/ERRORS.md) · [변경 안내](docs/MIGRATION.md) · [검증 기록](docs/VERIFICATION.md)

## Timely 전환 검증

[0.13.0 전환 검사](docs/TIMELY_MIGRATION.md)를 확인하세요. 키 등록과 모델 호출 성공은 서로 다릅니다. 기본 주소에는 `/v1`을 붙이지 않습니다. API 경로 `/chat/completions`는 클라이언트가 붙입니다. 실패 시 DeepSeek·다른 GPT·mock으로 전환하지 않습니다.

실제 전환 검증은 `.venv\Scripts\python evaluations/timely_migration.py --live`로 명시 실행합니다. A/B/C/D/E 코칭·개인 계획을 최대 HTTP 12회 안에서 실행하고 성공 단계를 저장·재사용합니다. 키가 없으면 호출 없이 중단합니다. 저장 결과는 로컬 `runtime/timely-013/`에 있으며 배포본에는 포함하지 않습니다.

## 모듈 코드를 수정하면 화면에도 반영되나요?

`./start.ps1`은 작업 폴더의 `src`를 직접 사용하고 Python 수정 시 서버를 자동 재시작합니다. 다음 코칭·계획 생성부터 새 코드가 실행됩니다. `.venv`에 다른 버전의 wheel이 있어도 개발 시작 스크립트는 현재 작업 폴더를 우선합니다.

기존 계획은 생성 당시 기록으로 보존하며 자동으로 다시 생성하지 않습니다. 화면에 현재 코드/이전 코드/코드 정보가 없는 이전 기록을 구분합니다. 최신 코드의 결과가 필요하면 코칭 요청 또는 계획 생성 버튼을 누르세요. 코드·프롬프트 변경은 이전 성공 캐시와 분리됩니다. 코드 상태 조회에는 AI 호출이 없습니다.

HTML·JS·CSS를 수정하면 입력을 보관한 후 새로고침하는 버튼이 표시됩니다. API 키 등 환경변수를 바꿀 때는 시작 스크립트를 다시 실행하세요. 자동 재시작을 끄려면 `./start.ps1 -NoReload`를 사용합니다. 실행 중인 AI 요청은 결과 저장을 기다린 후 재시작합니다. 자세한 설명은 [코드 변경 반영](docs/CODE_UPDATES.md)을 보세요.
