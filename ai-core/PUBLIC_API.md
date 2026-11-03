# 지원 Python API · 0.10.1

표준 import는 `from wemeet_ai.api import ...`입니다. `from wemeet_ai import GoalAI`와 기존 contracts/client/cache/tracing/baseline 모듈의 공개 경로도 유지합니다. `PUBLIC_API_VERSION="1.0"`, `GoalSetupState.schema_version="1.0"`입니다. 아래에 없는 밑줄 메서드·어댑터·시나리오 작성 보조 함수에 서비스 코드를 의존시키지 않습니다.

## 진입점과 입출력

| 진입점 | 입력 | 반환 | SW 저장 대상 |
|---|---|---|---|
| `GoalAI.advance_goal_setup` | GoalSetupRequest | Result[GoalSetupOutput] | state, 선택·확인 이력, 분석/초안, group_spec |
| `GoalAI.coach_member_goal` | MemberGoalRequest | Result[MemberCoachReply] | 해당 사람의 state와 대화 |
| `GoalAI.draft_member_goal` | MemberGoalRequest | Result[MemberGoalDraft] | 기여 초안·profile_fields·사람 확인 |
| `GoalAI.generate_group_plan` | GroupGoalSpec 또는 기존 GoalSpec | Result[GroupPlan] | plan_id/spec_id/revision과 공동 결과 |
| `GoalAI.generate_member_plan` | MemberPlanRequest | Result[MemberPlanDraft] | public_plan과 private_notes를 분리 저장 |
| `GoalAI.validate_plan` | ValidationRequest | Result[ValidationReport] | data.valid와 issues |
| `GoalAI.revise_plan` | RevisionRequest | Result[GroupPlan 또는 MemberPlanDraft] | 새 버전과 이전 버전 참조 |
| `GoalAI.build_handoff` | HandoffRequest | Result[HandoffPackage] | group_file과 private_files를 분리 저장 |
| `build_baseline` | BaselineRequest | dict | baseline_id/plan_version_id/task_id/확인 시각 |
| `validate_activity_reference` | baseline dict, ActivityReference | True 또는 ValueError | M2 기록의 버전·과제 참조 확인 |

공통 Result는 status/data/questions/warnings/error/meta/traces를 갖습니다. 입력 생성의 ValidationError는 호출자가 처리합니다. 정상적인 정보 부족은 NEEDS_INPUT이고, API 장애는 FAILED입니다. 실패 시 data가 항상 있는 것은 아니므로 `if result.data is not None`으로 확인합니다.

## 공동 목표 action

- COACH: conversation·members·reference_date·timezone 또는 이전 state. reply를 채팅에 표시합니다. C를 자동 수정하지 않습니다.
- REVIEW: B 분석과 C ASSISTED 검토표. data.review_items, needed_inputs, allowed_actions를 표시합니다.
- RESPOND: actor_member_id와 response(operation, fields/values/text). ACCEPT는 선택 필드, EDIT는 형식이 맞는 값, ANSWER는 필드 하나와 자유 답변, ALTERNATIVE는 추천받을 필드를 전달합니다.
- CONFIRM: 검토 완료 조건을 코드로 검사합니다. OK와 group_spec이 모두 있어야 공동 생성 함수에 넘깁니다. 빈 값 수락·미확인 추천은 명세에 들어가지 않습니다.

state를 수정해 승인처럼 보이게 하는 것은 서비스 권한 검증을 대신하지 않습니다. SW가 실제 사용자 동작과 그룹 승인 정책을 확인해야 합니다. 반복 CONFIRM은 같은 spec_id를 유지하고 확인 이력을 불필요하게 중복 생성하지 않습니다.

## 개인 목표와 계획

MemberGoalRequest는 goal_spec/group_plan/owner_member_id/conversation/state를 받습니다. diagnostic_mode=ADAPTIVE를 권장합니다. 다른 사람의 개인 대화는 거부합니다.

`data.state.current_question`의 text/options/question_id/target_field를 표시합니다. 사용자의 실제 선택을 개인 Message로 저장한 뒤 ButtonAnswer(question_id, option_ids, message_id)를 다음 요청에 전달합니다. 자유 답변은 새 개인 Message와 함께 전달하고 모델이 해석합니다. 반환된 state 전체를 보존하세요.

draft_member_goal의 contribution과 profile_fields를 검토합니다. 기여 목표·수준·시간 예산의 확인이 필요하며, 선호 시간이나 선택 제약 누락만으로 진행을 막지 않습니다. 확인된 값으로 MemberProfile과 MemberPlanRequest를 구성합니다. 사용자가 모른다고 한 시간이나 건너뛴 제약을 임의 값으로 확정하지 않습니다.

공동 계획이 바뀌면 개인 초안/기여 목표는 새 group_plan_id 기준으로 재검토합니다. 성공한 다른 구성원의 결과는 해당 변경이 영향을 주지 않는 한 보존합니다. 전체 검증은 require_all_members=True, 일부 구성원 검증은 False를 명시합니다.

## M0 확장 지점

- DeepSeekClient.complete_text(messages, options) / complete_json(messages, output_schema, options): requests 기반 동기 통신. 키는 DEEPSEEK_API_KEY에서만 읽습니다.
- AIError: code/message/retryable. build_baseline은 이 예외를 직접 발생시킬 수 있습니다.
- ResultCache: get(key), put(key,value,ttl), lock(key). MemoryResultCache는 한 프로세스 범위의24시간 성공 재사용과 동시 실행 병합을 제공합니다. 다중 프로세스 캐시는 SW가 구현합니다.
- TraceSink.emit(event): run_id/trace_id/parent_run_id와 정리된 payload. 기본 로그는 민감 원문을 저장하지 않습니다. 상세 개인 추적은 본인 범위로 별도 보관합니다.

모든 호출은 동기 함수입니다. 비동기 SW 서버에서는 작업 스레드/작업 큐로 실행하고 외부 API 대기 동안 DB 트랜잭션을 유지하지 않습니다. 전송/JSON/계획 보정을 합친 실제 HTTP는 AI 하위 모듈당 최대4회입니다. 여러 하위 모듈을 수행하는 workflow의 합계는 다를 수 있습니다.

## 상태·오류 분기

| 결과 | 호출자 동작 |
|---|---|
| OK + 검토표 | 표시·저장 후 실제 사용자 확인 대기 |
| OK + ValidationReport.valid=false | issues를 보완, 완료 처리 금지 |
| NEEDS_INPUT | state 보존, 필요한 필드만 같은 화면에서 입력 |
| TIMEOUT / RATE_LIMITED / NETWORK_ERROR / UPSTREAM_ERROR | retryable을 확인하고 성공 부분을 보존한 채 재시도 |
| AUTH_FAILED | 환경변수와 키를 확인, mock 자동 전환 금지 |
| EMPTY_RESPONSE / TRUNCATED_OUTPUT / INCOMPLETE_OUTPUT / INVALID_JSON / SCHEMA_INVALID | 실행 ID로 진단, 잘못된 결과를 활성 계획으로 사용하지 않음 |
| PLAN_INVALID | issues/오류 경로·확인 조건·기간·시간 예산·근거를 보완 |

최종 전달의 integration_ready는 코드 검증 준비 상태입니다. 그룹 투표·운영 승인·목표 활성화는 SW 책임입니다. 평가 기준 activation=LOCAL_EVALUATION_REFERENCE_ONLY도 서비스 승인이 아닙니다.
