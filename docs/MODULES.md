# SW 연결 안내 — 필요한 모듈만 선택

## 공통 사용법

입력 모델을 만들고 함수를 호출합니다. 모든 함수는 `Result`를 반환합니다. 모델 생성의 `ValidationError`와 실행 결과의 `FAILED`는 다릅니다. 모듈 요청의 필수 데이터가 부족하면 가능한 경우 `NEEDS_INPUT`으로 한 번에 안내합니다.

```python
from wemeet_ai.m0 import Runtime, MemoryResultCache
runtime = Runtime(cache=MemoryResultCache(), scope_id="SW가 인증한 작업 ID")
# result = function(request, runtime=runtime)
# F 코드 검증·C 검토/확정에는 runtime이 필요 없습니다.
```

메모리 캐시를 공유할 수 있지만 사용자·작업 식별은 SW가 결정해야 합니다. 소스가 다르면 캐시도 분리됩니다. 기본 보관 시간은 24시간입니다. 다중 프로세스에서는 SW가 ResultCache와 분산 잠금을 구현합니다.

PostgreSQL 저장을 함께 쓰는 경우 [ExecutionAdapter](POSTGRES_INTEGRATION.md)가 **같은 action_id**의 다중 프로세스 소유권·중복 반영·저장 재시도를 처리합니다. 이 기능과 서로 다른 action_id 사이의 선택적 결과 캐시는 구분하세요. 요청의 action_id는 버튼을 누른 동작 단위로 SW가 보존합니다.

## A — 공동 대화 코칭

```python
from wemeet_ai.m1.a_coaching import CoachRequest, coach_reply
request = CoachRequest.model_validate(payload)
result = coach_reply(request)
```

입력은 `members`, `conversation`, `reference_date`, `timezone`, 선택적 `context`입니다. 메시지는 `message_id/speaker_id/role/text/created_at`을 가집니다. user의 speaker_id는 구성원 목록에 있어야 합니다. 출력은 `reply` 자연어입니다.

SW는 반환 답변을 assistant 메시지로 저장하고 다음 호출의 conversation에 포함합니다. A는 B/C나 검토값을 변경하지 않습니다. 일반 메시지 추가만으로 호출할 필요는 없습니다.

## B — 전체 대화 분석

```python
from wemeet_ai.m1.b_analysis import AnalysisRequest, analyze_conversation
result = analyze_conversation(AnalysisRequest.model_validate(payload))
```

A와 같은 대화 자료를 직접 전달합니다. `summary/facts/coaching_notes/conflicts/missing`을 반환합니다. facts는 `value/origin/evidence/reason/review_status`를 가집니다. 사용자 발언은 USER_MESSAGE, 외부 확인값은 SW_CONFIRMED, AI 제안은 AI_SUGGESTED로 구분합니다.

분석 결과는 사용자 승인이나 합의가 아닙니다. evidence는 실제 사용자 메시지 ID인지 검사합니다. AI 메시지와 이에 대한 선택·거절·모호한 반응은 coaching_notes로 분리합니다. B의 facts를 C에 전달하는 것은 선택 사항입니다.

충돌한 항목은 `conflicted_fields`와 `fact.conflicted`로 반환합니다. B→C로 연결한다면 두 값도 그대로 전달하세요. 이전 확인값과 다른 새 사실이나 명시적 충돌은 C에서 재검토가 됩니다. 사용자 승인은 B가 만들지 않습니다.

## C — 목표 검토표

```python
from wemeet_ai.m1.c_goal_draft import DraftRequest, draft_goal_spec, review_field, confirm_goal
from wemeet_ai.shared.contracts import Member
result = draft_goal_spec(DraftRequest.model_validate({
    "facts": {"goal": {"value": "학교 안내 페이지 만들기", "origin": "EXTRACTED",
                       "evidence": [{"source": "SW_CONFIRMED", "source_id": "form-1"}]}},
    "members": [{"member_id": "u1", "nickname": "민지"}],
}))
draft = result.data
# 아래는 실제 사용자가 수락한 항목에만 호출합니다.
reviewed = review_field(draft, "goal", actor_id="확인한 사용자 ID")
# 수정: review_field(draft, "goal", value="새 목표", edit=True, actor_id="...")
# confirmed = confirm_goal(reviewed.data, members=[Member(member_id="u1",nickname="민지")])
```

- ASSISTED: 없는 값도 AI 추천으로 채움. 기본 모드.
- GROUNDED: 근거 없는 값은 미정.
- SUGGEST: target_fields로 지정한 항목만 새 제안.

허용 필드는 goal, start_date, end_date, completion_criteria, execution_method, verification_method, privacy_scope, milestones, roles, frequency_per_week, checkins입니다. 날짜는 YYYY-MM-DD, 완료 기준·체크인은 문자열 목록입니다.

이전 검토표는 previous로 전달합니다. 새 사실과 이전 확인값이 다르면 REVIEW_REQUIRED가 되며 이전 승인을 그대로 사용할 수 없습니다. 수락·직접 수정·confirm_goal은 CODE이며 유료 호출이 없습니다. 최종 필수값과 날짜·구성원 조건을 통과하면 ConfirmedGoal을 반환합니다. 이 값은 SW의 그룹 투표를 대신하지 않습니다.

## D — 공동 계획

```python
from wemeet_ai.m1.d_group_plan import ConfirmedGoal, generate_group_plan
result = generate_group_plan(ConfirmedGoal.model_validate(payload))
```

입력은 `goal/start_date/end_date/completion_criteria/execution_method/verification_method/privacy_scope/members/confirmed`입니다. C에서 만들지 않고 SW DB에서 직접 작성해도 됩니다. 개인 조건은 요구하지 않습니다.

결과 Plan은 목표·기간·마일스톤·주차 성과·tasks·조정 규칙·제안을 포함합니다. 모든 결과는 DRAFT입니다. 서버가 plan_version_id와 task_id를 발급합니다. 확인된 목표·기간을 바꾸거나 기간 밖 과제가 있으면 반려합니다.

## E — 개인 코칭 / 기여 초안 / 개인 계획

세 함수는 각각 독립 호출할 수 있습니다.

```python
from wemeet_ai.m1.e_member_plan import (
    MemberCoachRequest, MemberState, ButtonAnswer, MemberPlanRequest,
    coach_member_goal, draft_member_goal, generate_member_plan,
)
```

코칭의 context에는 공동 목표·알려진 일정·본인 공개 역할만 전달합니다. 다른 구성원의 private 조건을 전달하지 않습니다. owner_member_id는 SW 인증 결과입니다. 본인 사용자 메시지 이외의 개인 대화는 요청 모델이 거부합니다.

`coach_member_goal`은 reply, state, remaining_core/remaining_optional, summary_checkpoint, can_draft/can_generate_plan을 반환합니다. 준비 표시는 진단의 보조 상태이며 실제 계획 입력의 확인 검사를 대신하지 않습니다.

SW는 질문의 question_id와 options를 표시합니다. 버튼을 누르면 본인 메시지를 먼저 저장하고 다음 호출에 아래 answer와 **반환 state 전체**를 보냅니다.

```json
{"question_id":"반환된 질문 ID", "option_id":"선택한 option_id", "message_id":"새 본인 메시지 ID"}
```

UNKNOWN은 모름, SKIP은 건너뜀, VALUE의 빈 목록은 명시적인 별도 제약 없음입니다. 서로 바꾸어 저장하지 않습니다. 두 질문 뒤 중간 정리만 하고 필요한 진단은 이어집니다. 이미 답한 질문의 중복 버튼은 CODE 응답으로 처리합니다.

자유 입력도 본인 메시지로 저장해 conversation과 state를 전달합니다. `draft_member_goal`은 공개할 기여 목표와 확인된 개인 조건을 정리합니다. 사용자에게 결과물·완료 기준과 개인 조건을 각각 확인받으세요.

개인 조건을 직접 수정할 때는 `update_member_condition(state, field, value=..., actor_id=...)`를 사용합니다. CODE 작업으로 새 state를 반환하며 기존 state는 수정하지 않습니다. 같은 필드의 추가 세부 질문은 topic_id와 refinement_reason을 가지며 이미 답한 주제를 반복하지 않습니다.

`generate_member_plan` 입력은 공개 context, owner_member_id, 확인된 contribution, 확인된 profile입니다. **코칭을 실행하지 않았어도 직접 구성할 수 있습니다.** README의 독립 E 예제를 참고하세요. 개인 사유·private_notes는 공개 계획 생성 모델에 전달하지 않습니다. 수준과 시간 예산은 작업량에 사용합니다. 공개된 기여 목표에 민감한 사유를 넣지 않도록 확인해야 합니다.

## F — 부분 검증 / 전체 검증 / 명시적 AI 수정

```python
from wemeet_ai.m1.f_validation import ValidationRequest, validate_plan, RevisionRequest, revise_plan
result = validate_plan(ValidationRequest.model_validate(payload))  # AI 호출 없음
```

scope=GROUP이면 group_plan만, MEMBER이면 member_plans만, BUNDLE이면 둘 다 검사합니다. context와 weekly_budgets를 주면 확인 조건과 비교합니다. BUNDLE의 expected_members로 전체 구성원 누락을 검사합니다. 외부 작성 계획도 동일하게 처리합니다.

기본 검사: 날짜·목표 불일치·과제 ID 중복·담당 불일치·주간 예산 초과·잘못된 의존 ID. 빠진 비교 입력은 not_checked로 표시합니다. `valid=true`는 요청 범위의 코드 검사 통과이며 의미적 품질이나 서비스 승인 완료가 아닙니다.

`revise_plan`만 실제 AI를 사용합니다. 기존 계획을 수정하지 않고 previous_plan_version_id를 연결한 새 버전을 반환합니다. 계획 버전과 과제 ID를 함께 저장하세요.

## 저장·복구와 M2 참조

- 공동: 공개 대화, 검토표, 실제 사용자 확인, 공동 Plan.
- 개인: 본인 대화, MemberState, 개인 조건, 기여 검토, 개인 Plan.
- 서비스 내부: 검사 입력·오류. 개인 자료를 그룹 응답에 그대로 포함하지 않음.

JSON 파일이나 DB에서 읽은 dict는 각 입력 타입의 model_validate로 복원합니다. 기존 성공 결과는 실패 때문에 버리지 않습니다. 실패한 개인 함수만 재호출하며 다른 사람의 계획은 재생성하지 않습니다.

M2는 `{plan_version_id, task_id, activity_text}`로 활동 기록을 연결할 수 있습니다. 해당 버전의 task_id인지 SW가 확인해야 합니다. M2 평가·진행률·알림·그룹 투표는 이 패키지에 없습니다.

## 선택적 기록 연결 — 0.12.0

`wemeet_ai.continuity.prepare_module_input(module, RecordView, overrides=None)`는 저장·AI 호출 없이 요청을 준비합니다. 독립 함수의 필수 선행 단계가 아닙니다. `request`, `ready`, `needed_inputs`, `source_revision`, `dependencies`, `stale`, `reasons`를 반환합니다. [저장·복원 계약](CONTINUITY.md)을 참고하세요.

## 0.12.1 개인 세부 답변

`MemberState.refinements`(topic ID→ReviewField), `refinement_states`(topic ID→KNOWN/UNKNOWN/SKIPPED)는 이미 아는 조건의 세부 답변입니다. 상위 `fields`·`answer_states`를 지우지 않습니다. 기존 상태에는 빈 객체 기본값을 사용합니다. 본인 데이터로만 저장하고 다음 E 요청에 전체 state를 다시 전달하세요.
