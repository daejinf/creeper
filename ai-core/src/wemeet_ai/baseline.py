"""Immutable evaluation contract for SW/M2, independent of persistence."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4, uuid5, NAMESPACE_URL

from .contracts import Contract, ValidationRequest, MemberPlanDraft
from .client import AIError
from .validation import validate_plan


class BaselineRequest(ValidationRequest):
    scope: Literal['GROUP','PERSONAL']
    confirmed_by: str
    owner_member_id: str | None = None


class ActivityReference(Contract):
    plan_version_id: str
    task_id: str
    recorded_at: datetime
    text: str


def build_baseline(request):
    spec,group=request.goal_spec,request.group_plan
    ids={m.member_id for m in getattr(spec,'members',getattr(spec,'member_profiles',[]))}
    if request.confirmed_by not in ids:raise AIError('INPUT_INVALID','확인자는 현재 구성원이어야 합니다.')
    if request.scope=='PERSONAL' and request.owner_member_id!=request.confirmed_by:raise AIError('INPUT_INVALID','개인 평가 기준은 본인이 확인해야 합니다.')
    plans=[p for p in request.member_plans if p.public_plan.member_id==request.owner_member_id] if request.scope=='PERSONAL' else []
    if request.scope=='PERSONAL' and len(plans)!=1:raise AIError('PLAN_INVALID','본인의 검증 가능한 개인 계획이 필요합니다.')
    report=validate_plan(spec,group,plans,require_all_members=False,member_profiles=request.member_profiles)
    if not report.valid:raise AIError('PLAN_INVALID',report.model_dump_json())
    if plans and (not plans[0].contribution_goal or plans[0].contribution_goal.review_status not in {'ACCEPTED','EDITED'}):raise AIError('PLAN_INVALID','개인 기여 목표 확인이 필요합니다.')
    version=plans[0].public_plan.plan_id if plans else group.plan_id
    if not version:raise AIError('PLAN_INVALID','평가 기준에는 저장 가능한 계획 버전 ID가 필요합니다.')
    tasks=[]
    for plan in plans:
        for index,todo in enumerate(plan.public_plan.first_week_todos):
            task=todo.model_dump(mode='json');task['task_id']=todo.task_id or str(uuid5(NAMESPACE_URL,version+':'+str(index)))
            task['owner_member_id']=plan.public_plan.member_id;task['plan_version_id']=version;task['verification_method']=spec.verification_method
            tasks.append(task)
    public={'goal_spec_id':spec.spec_id,'group_goal':spec.goal,'group_plan_version_id':group.plan_id,
        'personal_goal':plans[0].contribution_goal.model_dump(mode='json') if plans else None,
        'period':{'start_date':spec.start_date.isoformat(),'end_date':spec.end_date.isoformat()},'metric':spec.metric.model_dump() if spec.metric else None,
        'completion_criteria':spec.completion_criteria,'verification_method':spec.verification_method,'privacy_scope':spec.privacy_scope,
        'milestones':[m.model_dump(mode='json') for m in group.milestones],'tasks':tasks}
    private={}
    if plans:
        profiles=getattr(spec,'member_profiles',request.member_profiles)
        profile=next((p for p in profiles if p.member_id==request.owner_member_id),None)
        private[request.owner_member_id]={'profile':profile.model_dump(mode='json') if profile else None,'private_notes':plans[0].private_notes}
    return {'schema_version':'2.0','baseline_id':str(uuid4()),'scope':request.scope,'owner_member_id':request.owner_member_id,
        'plan_version_id':version,'group_plan_version_id':group.plan_id,'confirmed_by':request.confirmed_by,
        'confirmed_at':datetime.now(timezone.utc).isoformat(),'previous_version_id':plans[0].public_plan.previous_plan_id if plans else group.previous_plan_id,
        'activation':'LOCAL_EVALUATION_REFERENCE_ONLY','public_payload':public,'private_payloads':private}


def validate_activity_reference(baseline, activity):
    if baseline['plan_version_id']!=activity.plan_version_id:raise ValueError('PLAN_VERSION_MISMATCH')
    if activity.task_id not in {task['task_id'] for task in baseline['public_payload']['tasks']}:raise ValueError('UNKNOWN_TASK')
    return True
