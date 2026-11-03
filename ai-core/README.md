# WE·MEET M0/M1 AI Core · 0.10.1

SW팀은 Python 패키지에 공개 대화·사용자 선택·본인 조건을 전달하고, 결과와 상태를 자체 DB에 저장합니다. AI 코어는 로그인·그룹 승인·알림·서비스 활성화를 처리하지 않습니다.

## 1. 설치와 첫 실행

Python 3.11 이상을 사용합니다. 전달 묶음을 푼 폴더에서 실행합니다.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install dist/wemeet_ai_core-0.10.1-py3-none-any.whl
.venv\Scripts\python examples/sw_m0_m1_end_to_end.py --output demo-output
```

macOS/Linux에서는 `.venv/bin/python`을 사용합니다. pip는 선언된 requests·Pydantic·Windows tzdata 의존성을 설치합니다. 저장소의 `.deps`나 소스 PYTHONPATH는 필요하지 않습니다.

기본 예제는 **작성된 fixture / REPLAY**이며 키·HTTP·유료 토큰0입니다. 사용자 수락은 이 예제에서만 모의 처리합니다. `demo-output/report.json`에서 validation_passed, m2_reference_valid, state_restored, successful_member_plan_preserved가 true인지 확인합니다.

## 2. 전체 흐름을 한 예제로 확인

**공동 코칭 → 분석·검토 → 사용자 수락 → 공동 명세 → 공동 계획 → 개인 버튼 문답 → 기여 목표 검토 → 개인 계획 → 검증 → M2 기준 → 저장·복원**

- `group/`: 공동 요청·응답과 복원할 공동 state.
- `private/{member_id}/`: 해당 사람의 문답·조건·기여 초안·개인 응답. 서비스에서는 본인 권한으로만 접근합니다.
- `private/_service/`: 여러 사람의 조건이 필요한 검증·전달 요청 진단. 서비스 내부 전용이며 그룹이나 개별 회원에게 공개하지 않습니다.
- `group-public.json`: 공개 가능한 공동 목표·과제·검증 결과.
- `group/m2-*.json`: plan_version_id/task_id와 활동 기록 참조 예시.
- `run-manifest.json`: 단계·실행 ID·신규 사용량. 개인 원문은 포함하지 않습니다.

예제는 미확인 초안 확정의 NEEDS_INPUT, 공동 계획 TIMEOUT, 두 번째 구성원 계획 실패를 의도적으로 만듭니다. 저장 상태를 읽고 실패 단계/구성원만 재실행하며, 성공한 구성원의 계획 ID가 유지되는지도 확인합니다.

## 3. 지원하는 Python API

```python
from wemeet_ai import GoalAI, __version__
from wemeet_ai.api import GoalSetupRequest, GoalSetupResponse, Result
```

[PUBLIC_API.md](PUBLIC_API.md)가 지원하는 함수·타입·입출력·저장 계약의 기준입니다. 기존 `wemeet_ai.contracts` 경로와 개별 코칭·분석 함수는 호환성을 유지합니다. 밑줄 메서드와 `adapters`는 SW 서비스 연동 대상이 아닙니다.

| 구간 | 진입점 | SW가 할 일 |
|---|---|---|
| 공동 목표 | `advance_goal_setup` | reply/검토표를 표시하고 action·사용자 선택·반환 state 저장 |
| 개인 목표 | `coach_member_goal`, `draft_member_goal` | 반환 state와 질문/선택지를 본인에게 표시, 개인 상태 저장 |
| 실행 계획 | `generate_group_plan`, `generate_member_plan` | 확인된 입력과 반환 plan_id/revision 저장 |
| 검증·전달 | `validate_plan`, `build_handoff` | valid/issues와 공개/개인 파일 구분 확인 |
| M2 참조 | `build_baseline`, `validate_activity_reference` | 불변 계획 버전·과제 ID·확인 이력 저장 |

A는 자연어 코칭입니다. REVIEW 요청에서만 B 분석과 C ASSISTED 검토표를 만듭니다. C에서 미정 내용은 추천이나 직접 답변으로 보완하며 A로 돌아가지 않습니다. AI 추천은 사용자 수락 전 PENDING입니다. 개인 조건 없이 공동 계획을 만들 수 있습니다.

## 4. 실제 DeepSeek 연결과 상태 분기

서버 환경변수 `DEEPSEEK_API_KEY`를 설정하고 `GoalAI()`를 사용합니다. 모델 기본값은 `deepseek-flash`, 주소 기본값은 `https://api.deepseek.com`입니다. 키를 브라우저·요청 JSON·저장 데이터에 넣지 않습니다. 키 누락이나 실패 시 자동 mock 전환을 하지 않습니다.

- `OK`: data를 표시하고 저장합니다. 검토표 OK는 모든 값의 확정이나 계획 활성화를 뜻하지 않습니다.
- `NEEDS_INPUT`: 반환된 state를 보존하고 needed_inputs를 같은 화면에서 보완합니다.
- `FAILED`: error.code/message/retryable을 확인합니다. 반환 state가 있으면 저장하고 실패한 동작만 재실행합니다.
- 입력 모델 생성 중 Pydantic ValidationError는 SW의 요청 형식 오류입니다. 모델 호출 전에 처리합니다.
- `validate_plan` 메서드의 Result가 OK여도 `data.valid`가 false면 검증 실패입니다.
- 독립 함수 `build_baseline`은 dict를 반환하며 오류 시 AIError를 발생시킵니다. 세부 예외 분기는 PUBLIC_API.md에 있습니다.

완전한 실제 입력·호출 순서는 [SW_INTEGRATION.md](SW_INTEGRATION.md)에 있습니다. 기본 fixture를 실제 AI 응답으로 오인하지 마세요.

## 5. 확인·저장·권한과 버전

반환 state를 작업별로 저장하고 다음 요청에 전달합니다. JSON 저장·복원은 `model_dump(mode="json")`와 해당 타입의 `model_validate()`를 사용합니다. 공동 state에 개인 조건을 합치지 않습니다. 개인 호출에는 본인 대화만 전달하고 owner_member_id를 SW의 인증 결과로 결정합니다.

SW는 그룹 승인과 동시 편집 트랜잭션을 구현해야 합니다. state.revision은 DB 잠금이 아닙니다. AI가 한 사람의 동의를 전체 그룹 승인으로 바꾸지 않습니다.

중복 CONFIRM은 동일 spec_id를 유지합니다. 실제 계획 수정은 새 plan_id/revision/previous_plan_id로 기록하며 M2의 이전 기준은 보존합니다. [SW_M2_CONTRACT.md](SW_M2_CONTRACT.md)를 참고하세요. M2 평가 기능 자체는 이번 패키지에 포함하지 않습니다.

## 6. 캐시·사용량·오류 복구

MemoryResultCache 또는 SW가 구현한 ResultCache를 주입합니다. cache_scope는 SW가 인증된 작업 기준으로 정하고, 개인 결과는 구성원별 범위를 사용합니다. 같은 입력 재실행은 성공 캐시를 재사용하며 실패는 성공 캐시에 넣지 않습니다. 운영 다중 프로세스 잠금은 SW 저장소 구현이 필요합니다.

수락·직접 수정·확정·코드 검증·화면 복원은 신규 토큰0입니다. 자연어 날짜 해석과 다른 추천 요청은 호출할 수 있습니다. force_refresh는 사용자가 새 안을 명시적으로 요청할 때만 사용합니다.

메타의 LIVE/CACHE/CODE/REPLAY를 구분합니다. CACHE의 신규 사용량은0이고 원본 사용량은 cached_usage입니다. 상위 workflow 토큰은 하위 합계이므로 runs와 다시 합산하지 않습니다. 비용을 외부 청구 금액과 동일하다고 계산하지 않습니다.

TIMEOUT·RATE_LIMITED·일시적 통신 오류는 허용된 재시도 후에도 실패할 수 있습니다. 성공한 상태·공동 계획·다른 구성원 결과를 보존하고 해당 단계만 재실행합니다. AUTH_FAILED는 키 설정을 확인한 뒤 재실행합니다. PLAN_INVALID/SCHEMA_INVALID는 오류 경로와 입력 계약을 확인합니다.

## 7. 보조 CLI·웹과 200개 시나리오

```powershell
wemeet-ai goal-setup --input request.json --output response.json
wemeet-ai schemas --output contracts.json
```

개발자 웹이 필요하면 `python -m pip install "dist/wemeet_ai_core-0.10.1-py3-none-any.whl[web]"`로 web extras를 설치하고 `python -m uvicorn wemeet_ai.adapters.web:app --host 127.0.0.1 --port 8010`으로 실행합니다. 웹은 로컬 SQLite와 테스트 UI를 포함하며 운영용 인증 서버가 아닙니다. Python 코어 설치에는 web extras가 필요 없습니다.

[200개 시나리오 안내](SCENARIOS.md)는 기준안·LIVE 결과·검토 상태를 구분합니다. 예제 조회나 불러오기만으로 API를 호출하지 않습니다.

## 8. 검증 범위와 인수

[VERIFICATION.md](VERIFICATION.md)에 현재 소스의 검사·설치·브라우저·LIVE 결과를 구분해 기록합니다. [HISTORY.md](HISTORY.md)는 과거 사용법 보관 문서입니다. SW팀의 직접 인수와 사람의 의미적 품질 검토는 실제 수행 전까지 완료로 표시하지 않습니다.
