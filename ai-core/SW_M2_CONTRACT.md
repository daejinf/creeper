# SW / M2 계획·평가 기준 계약 · 0.10.1

M2 평가 기능 자체는 포함하지 않습니다. 이번 패키지는 확인된 공동 목표·개인 기여 목표·과제·완료 기준을 M2가 정확한 버전으로 참조하도록 제공합니다.

## 1. 생성 순서

`advance_goal_setup`으로 공동 조건 확인 → 공동 계획 → 본인 조건·기여 목표 확인 → 개인 계획 → validate_plan → build_baseline 순서입니다. 전체 코드와 실제 JSON은 `examples/sw_m0_m1_end_to_end.py`의 group/m2-*.json 출력에서 확인합니다.

```python
from wemeet_ai.api import BaselineRequest, build_baseline, ActivityReference, validate_activity_reference

baseline = build_baseline(BaselineRequest(
    scope="PERSONAL", confirmed_by=member_id, owner_member_id=member_id,
    goal_spec=spec, group_plan=group, member_plans=plans,
    member_profiles=profiles))
# SW가 실제 사용자의 확인을 받은 뒤 호출하고 baseline을 새 레코드로 저장합니다.
```

build_baseline은 dict를 반환합니다. 잘못된 확인자·미확인 개인 기여 목표·검증 실패에는 AIError를 발생시킵니다. 개인 기준은 본인 확인만 허용하며 그룹 기준 생성이 전체 그룹 투표나 서비스 승인을 대신하지 않습니다.

## 2. 저장할 버전과 연결

- spec_id: 확인된 공동 조건의 식별자.
- group_plan.plan_id: 공동 계획 버전. revision과 previous_plan_id를 함께 저장합니다.
- personal.public_plan.plan_id: 개인 계획 버전. 과거 버전을 덮어쓰지 않습니다.
- baseline_id: 확인된 평가 기준의 ID. schema_version=2.0.
- baseline.plan_version_id: 개인 scope이면 개인 계획 ID, 그룹 scope이면 공동 계획 ID.
- baseline.group_plan_version_id: 연결된 공동 계획 ID.
- task_id: 해당 계획 버전의 과제 ID. 다른 버전 과제와 섞지 않습니다.

build_baseline 자체는 호출할 때 새 baseline_id를 생성합니다. 반복 확인의 멱등성은 SW가 `(scope, owner_member_id, plan_version_id)`의 기존 기준을 조회·저장하는 방식으로 보장합니다. 로컬 SQLite 어댑터의 중복 저장 처리와 순수 함수의 동작을 혼동하지 않습니다.

## 3. 공개·개인 분리

baseline.public_payload에는 공동 목표, 개인 기여 목표, 기간, 완료 기준, 인증 방식, 공개 범위, 마일스톤, 공개 과제가 있습니다. 과제는 task_id/owner_member_id/plan_version_id/scheduled_date/action/amount_or_deliverable/estimated_minutes/completion_criteria/verification_method를 포함합니다.

baseline.private_payloads에는 당사자 조건·비공개 조언이 있습니다. 이 객체를 공개 파일과 함께 저장하거나 그룹에 반환하지 않습니다. 전체 검증·전달 입력처럼 여러 사람의 개인 조건을 포함하는 진단은 서비스 내부 전용입니다.

확인자·확인 시각은 confirmed_by/confirmed_at입니다. activation=LOCAL_EVALUATION_REFERENCE_ONLY이며 실제 알림·벌점·운영 목표 활성화는 SW 승인 후 처리합니다.

## 4. M2 활동 기록

```python
from datetime import datetime, timezone
activity = ActivityReference(
    plan_version_id=baseline["plan_version_id"],
    task_id=baseline["public_payload"]["tasks"][0]["task_id"],
    recorded_at=datetime.now(timezone.utc), text="담당 작업 기록")
validate_activity_reference(baseline, activity)
```

버전이 다르면 ValueError(PLAN_VERSION_MISMATCH), 과제가 없으면 ValueError(UNKNOWN_TASK)입니다. 이것은 참조 검사이며 작성자의 권한·실제 완료 여부 판정은 SW와 후속 M2가 담당합니다. 완료율·수치 비교는 코드가 계산하고 AI가 임의 수치로 확정하지 않습니다.

## 5. 전달 검증과 수정

HandoffRequest.baseline_refs에 BaselineRef를 전달합니다. 현재 계획·과제 ID와 맞지 않으면 BASELINE_VERSION_MISMATCH로 integration_ready가 false입니다. 성공한 과거 기준을 수정하거나 삭제하지 말고 현재 전송에서 제외한 뒤 새 계획 기준을 생성합니다.

예제는 개인 계획 하나가 실패해도 성공 결과를 보존하며, 실패 구성원만 재생성한 뒤 전체 검증과 기준 참조를 확인합니다. 사용자 승인과 SW팀 직접 인수는 모의 fixture 통과와 별개입니다.
