"""M1-D: a confirmed SW-authored goal is enough. C IDs are never required."""
from uuid import uuid4
from ...shared.contracts import ConfirmedGoal, Plan
from ...m0 import Runtime, AIError
from ...m0.runtime import code_result
from ..f_validation import inspect_plan
from ...shared.plans import assign_ids

def generate_group_plan(request: ConfirmedGoal, *, runtime: Runtime | None = None, fresh=False):
    missing = [k for k in ("goal", "start_date", "end_date", "completion_criteria", "execution_method", "verification_method", "privacy_scope", "members") if not getattr(request, k)]
    if not request.confirmed: missing.append("confirmed: 실제 사용자 검토가 필요합니다.")
    if missing: return code_result("D", needed=missing)
    def check(plan):
        if plan.owner_member_id: raise AIError("PLAN_INVALID", "공동 계획에는 개인 소유자를 지정하지 마세요.")
        issues = inspect_plan(plan, request)
        member_ids = {m.member_id for m in request.members}
        if any(t.owner_member_id and t.owner_member_id not in member_ids for t in plan.tasks): raise AIError("PLAN_INVALID", "알 수 없는 담당자입니다.")
        if issues: raise AIError("PLAN_INVALID", str([i.model_dump() for i in issues]))
        assign_ids(plan)
    result = (runtime or Runtime()).run("D", request, Plan,
        """확인된 공동 목표로 실행 계획을 작성하세요. 날짜·목표·완료 기준은 바꾸지 마세요.
목표별 주차 성과, 마일스톤, 구체적 과제·결과물·완료 기준·예상 분량·의존 작업을 만드세요.
개인 조건은 입력되지 않았으므로 개인 가용 시간이나 수준을 추정하지 마세요. 담당 배정은 명시된 역할만 따르고 없으면 null입니다.
고정된 주3회/20시/일반 과제 복사를 하지 마세요. 미합의 일정은 suggestions로 분리하세요.
plan_version_id/task_id는 빈 문자열, dependencies는 빈 목록으로 두세요. 서버가 ID를 발급합니다.""", check=check, fresh=fresh)
    if result.status == "OK":
        if not result.data.plan_version_id: result.data.plan_version_id = str(uuid4())
        for task in result.data.tasks:
            if not task.task_id: task.task_id = str(uuid4())
    return result

__all__ = ["ConfirmedGoal", "generate_group_plan"]
