"""Explicit optional PostgreSQL storage. No model calls and no implicit approval."""
import hashlib
import json
from contextlib import contextmanager
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from . import schema as db
from ..shared.contracts import ConfirmedGoal, Plan, GoalContext
from ..m1.f_validation import inspect_plan


class GoalConflict(ValueError): pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class GoalRepository:
    def __init__(self, url=None, *, connection=None):
        if connection is None:
            if not url or not url.startswith('postgresql+psycopg://'):
                raise ValueError('PostgreSQL URL is required; no SQLite fallback')
            self.engine = sa.create_engine(url, pool_pre_ping=True, connect_args={'connect_timeout':5}, hide_parameters=True)
        else:self.engine = None
        self.connection = connection

    @contextmanager
    def transaction(self):
        if self.connection is not None:
            with self.connection.begin_nested():yield self.connection
        else:
            with self.engine.begin() as connection:yield connection

    def _once(self, connection, table, key, values):
        connection.execute(insert(table).values(**values).on_conflict_do_nothing())
        row=connection.execute(sa.select(table).where(key)).mappings().first()
        if not row or row['content_hash']!=values['content_hash']:
            raise GoalConflict('같은 버전/근거 ID로 다른 내용을 저장할 수 없습니다.')
        return dict(row)

    def save_group(self, group_id, title, members):
        with self.transaction() as c:
            c.execute(insert(db.groups).values(group_id=group_id,title=title).on_conflict_do_update(
                index_elements=['group_id'],set_={'title':title}))
            for m in members:
                c.execute(insert(db.members).values(group_id=group_id,member_id=m['member_id'],nickname=m['nickname']).on_conflict_do_update(
                    index_elements=['group_id','member_id'],set_={'nickname':m['nickname']}))

    def save_shared_goal(self, group_id, version_id, goal, *, source_ref, created_at=None):
        goal=ConfirmedGoal.model_validate(goal)
        if not goal.confirmed or not all((goal.goal.strip(),goal.start_date,goal.end_date,goal.completion_criteria,goal.verification_method,goal.privacy_scope)):
            raise ValueError('확인된 공동 목표·기간·완료 기준·인증·공개 범위가 필요합니다.')
        payload=goal.model_dump(mode='json')
        values=dict(goal_version_id=version_id,group_id=group_id,source_ref=source_ref,title=goal.goal,
            start_date=goal.start_date,end_date=goal.end_date,completion_criteria=goal.completion_criteria,
            verification_method=goal.verification_method,privacy_scope=goal.privacy_scope,conditions=payload,
            content_hash=digest([group_id,payload]))
        if created_at:values['created_at']=created_at
        with self.transaction() as c:self._once(c,db.shared_goals,db.shared_goals.c.goal_version_id==version_id,values)
        return version_id

    def save_member_goal(self, group_id, member_id, version_id, shared_goal_version_id, contribution, profile, *, source_ref, created_at=None):
        from ..m1.e_member_plan import Contribution, MemberProfile
        contribution=Contribution.model_validate(contribution).model_dump(mode='json',exclude_unset=True)
        profile=MemberProfile.model_validate(profile).model_dump(mode='json',exclude_unset=True)
        if not contribution.get('confirmed') or not profile.get('confirmed'):
            raise ValueError('개인 기여 목표와 개인 조건의 사용자 확인이 필요합니다.')
        if not all((contribution.get('title'),contribution.get('deliverable'),contribution.get('completion_criteria'),profile.get('current_level'),profile.get('weekly_available_minutes'))):
            raise ValueError('기여 결과물·완료 기준·수준·시간이 필요합니다.')
        values=dict(member_goal_version_id=version_id,group_id=group_id,member_id=member_id,
            shared_goal_version_id=shared_goal_version_id,source_ref=source_ref,title=contribution['title'],
            deliverable=contribution['deliverable'],completion_criteria=contribution['completion_criteria'],
            content_hash=digest([group_id,member_id,shared_goal_version_id,contribution,profile]))
        if created_at:values['created_at']=created_at
        with self.transaction() as c:
            self._once(c,db.member_goals,db.member_goals.c.member_goal_version_id==version_id,values)
            c.execute(insert(db.private_profiles).values(member_goal_version_id=version_id,group_id=group_id,member_id=member_id,
                current_level=profile['current_level'],weekly_available_minutes=profile['weekly_available_minutes'],details=profile).on_conflict_do_nothing())
        return version_id

    def save_plan(self, group_id, shared_goal_version_id, plan, *, source_ref, member_goal_version_id=None, created_at=None):
        plan=Plan.model_validate(plan)
        if not plan.plan_version_id or any(not task.task_id for task in plan.tasks):
            raise ValueError('계획 버전 ID와 모든 과제 ID가 필요합니다.')
        with self.transaction() as c:
            goal=c.execute(sa.select(db.shared_goals).where(db.shared_goals.c.goal_version_id==shared_goal_version_id,db.shared_goals.c.group_id==group_id)).mappings().one()
            context=GoalContext.model_validate({k:v for k,v in goal['conditions'].items() if k in GoalContext.model_fields})
            budget={}
            if plan.owner_member_id:
                p=c.execute(sa.select(db.private_profiles).where(db.private_profiles.c.member_goal_version_id==member_goal_version_id,
                    db.private_profiles.c.member_id==plan.owner_member_id,db.private_profiles.c.group_id==group_id)).mappings().one()
                budget={plan.owner_member_id:p['weekly_available_minutes']}
            elif member_goal_version_id:raise ValueError('공동 계획에 개인 목표를 연결할 수 없습니다.')
            if plan.previous_plan_version_id:
                previous=c.execute(sa.select(db.plans).where(db.plans.c.plan_version_id==plan.previous_plan_version_id)).mappings().one()
                if previous['group_id']!=group_id or previous['owner_member_id']!=plan.owner_member_id:
                    raise GoalConflict('이전 계획의 그룹·담당자가 일치하지 않습니다.')
            issues=inspect_plan(plan,context,budget)
            if issues:raise ValueError('계획 검증 실패: '+','.join(sorted({i.code for i in issues})))
            values=dict(plan_version_id=plan.plan_version_id,group_id=group_id,shared_goal_version_id=shared_goal_version_id,
                member_goal_version_id=member_goal_version_id,owner_member_id=plan.owner_member_id,
                scope='MEMBER' if plan.owner_member_id else 'GROUP',previous_plan_version_id=plan.previous_plan_version_id,
                source_ref=source_ref,title=plan.goal,start_date=plan.start_date,end_date=plan.end_date,status='DRAFT',
                weekly_outcomes=plan.weekly_outcomes,adaptation_rules=plan.adaptation_rules,suggestions=plan.suggestions,
                validation={'valid':True,'validator':'m1-f','issues':[]},
                content_hash=digest([group_id,shared_goal_version_id,member_goal_version_id,plan.model_dump(mode='json')]))
            if created_at:values['created_at']=created_at
            existing=c.execute(sa.select(db.plans.c.plan_version_id).where(db.plans.c.plan_version_id==plan.plan_version_id)).first()
            self._once(c,db.plans,db.plans.c.plan_version_id==plan.plan_version_id,values)
            if existing:return plan.plan_version_id
            for task in plan.tasks:
                c.execute(db.tasks.insert().values(plan_version_id=plan.plan_version_id,
                    **task.model_dump(exclude={'dependencies'})))
            for task in plan.tasks:
                for dep in task.dependencies:c.execute(db.dependencies.insert().values(plan_version_id=plan.plan_version_id,task_id=task.task_id,depends_on_task_id=dep))
            for i,m in enumerate(plan.milestones):c.execute(db.milestones.insert().values(plan_version_id=plan.plan_version_id,position=i,**m.model_dump()))
        return plan.plan_version_id

    def set_heads(self, group_id, revision, shared_goal_id, member_goals, current_plans):
        with self.transaction() as c:
            if not shared_goal_id and (member_goals or current_plans):raise GoalConflict('미확인 공동 목표를 현재 계획으로 지정할 수 없습니다.')
            for owner,version in member_goals.items():
                match=c.execute(sa.select(db.member_goals.c.member_goal_version_id).where(db.member_goals.c.group_id==group_id,
                    db.member_goals.c.member_id==owner,db.member_goals.c.member_goal_version_id==version,
                    db.member_goals.c.shared_goal_version_id==shared_goal_id)).first()
                if not match:raise GoalConflict('현재 개인 목표의 그룹·담당자·공동 목표 연결을 확인해주세요.')
            for owner,version in current_plans.items():
                match=c.execute(sa.select(db.plans.c.plan_version_id).where(db.plans.c.group_id==group_id,
                    db.plans.c.owner_member_id.is_(None) if owner=='GROUP' else db.plans.c.owner_member_id==owner,
                    db.plans.c.plan_version_id==version,db.plans.c.shared_goal_version_id==shared_goal_id)).first()
                if not match:raise GoalConflict('현재 계획의 그룹·담당자·공동 목표 연결을 확인해주세요.')
            old=c.execute(sa.select(db.heads).where(db.heads.c.group_id==group_id).with_for_update()).mappings().first()
            if old and old['source_revision']>revision:raise GoalConflict('더 오래된 기록으로 현재 목표를 바꿀 수 없습니다.')
            values=dict(group_id=group_id,source_revision=revision,current_shared_goal_version_id=shared_goal_id,
                current_member_goals=member_goals,current_plans=current_plans)
            c.execute(insert(db.heads).values(**values).on_conflict_do_update(index_elements=['group_id'],set_=values))

    def _plan(self,c,row,private=False):
        p=dict(row);p.pop('content_hash',None);p.pop('source_ref',None)
        p['tasks']=[dict(t) for t in c.execute(sa.select(db.tasks).where(db.tasks.c.plan_version_id==p['plan_version_id']).order_by(db.tasks.c.due_date,db.tasks.c.task_id)).mappings()]
        if not private:
            for t in p['tasks']:
                t.pop('estimated_minutes',None);t.pop('difficulty',None)
            p.pop('suggestions',None);p.pop('adaptation_rules',None)
        baseline=c.execute(sa.select(db.baselines).where(db.baselines.c.plan_version_id==p['plan_version_id'])).mappings().first()
        p['baseline']=dict(baseline) if baseline else None
        return p

    def public_group(self,group_id):
        with self.transaction() as c:
            head=c.execute(sa.select(db.heads).where(db.heads.c.group_id==group_id)).mappings().first()
            goals=[{k:v for k,v in r.items() if k not in {'conditions','content_hash','source_ref'}} for r in c.execute(sa.select(db.shared_goals).where(db.shared_goals.c.group_id==group_id).order_by(db.shared_goals.c.created_at)).mappings()]
            versions=[]
            for row in c.execute(sa.select(db.plans).where(db.plans.c.group_id==group_id,db.plans.c.scope=='GROUP').order_by(db.plans.c.created_at)).mappings():versions.append(self._plan(c,row))
            # Shared queries never select private_profiles or private conversation.
            contributions=[]
            public_ids={g['goal_version_id'] for g in goals if g['privacy_scope']=='OBJECTIVE_ONLY'}
            for r in c.execute(sa.select(db.member_goals).where(db.member_goals.c.group_id==group_id)).mappings():
                if r['shared_goal_version_id'] in public_ids:contributions.append({k:r[k] for k in ('member_goal_version_id','shared_goal_version_id','member_id','title','deliverable','completion_criteria')})
            return {'backend':'POSTGRESQL','group_id':group_id,'heads':dict(head) if head else None,
                'shared_goals':goals,'member_goals':contributions,'group_plans':versions}

    def private_member(self,group_id,member_id):
        with self.transaction() as c:
            goals=[]
            for r in c.execute(sa.select(db.member_goals).where(db.member_goals.c.group_id==group_id,db.member_goals.c.member_id==member_id).order_by(db.member_goals.c.created_at)).mappings():
                p=c.execute(sa.select(db.private_profiles).where(db.private_profiles.c.member_goal_version_id==r['member_goal_version_id'])).mappings().one()
                goals.append({k:v for k,v in r.items() if k not in {'content_hash','source_ref'}}|{'profile':dict(p)})
            plans=[self._plan(c,r,True) for r in c.execute(sa.select(db.plans).where(db.plans.c.group_id==group_id,db.plans.c.owner_member_id==member_id).order_by(db.plans.c.created_at)).mappings()]
            return {'backend':'POSTGRESQL','group_id':group_id,'member_id':member_id,'member_goals':goals,'plans':plans}

    def confirm_baseline(self,group_id,plan_version_id,actor_member_id):
        with self.transaction() as c:
            c.execute(sa.select(db.groups).where(db.groups.c.group_id==group_id).with_for_update()).one()
            from .journal_schema import records
            if c.scalar(sa.select(records.c.quarantine_reason).where(records.c.record_id==group_id)):
                raise GoalConflict('이관 보류 기록은 평가 기준으로 확정할 수 없습니다.')
            plan=c.execute(sa.select(db.plans).where(db.plans.c.group_id==group_id,db.plans.c.plan_version_id==plan_version_id)).mappings().first()
            if not plan:raise ValueError('해당 그룹의 계획 버전이 필요합니다.')
            if plan['owner_member_id'] and actor_member_id!=plan['owner_member_id']:raise ValueError('본인의 개인 계획만 평가 기준으로 확인할 수 있습니다.')
            head=c.execute(sa.select(db.heads).where(db.heads.c.group_id==group_id)).mappings().one()
            if plan_version_id not in head['current_plans'].values() or head['current_shared_goal_version_id']!=plan['shared_goal_version_id']:
                raise GoalConflict('이전 조건의 계획입니다. 현재 목표와 계획을 먼저 검토해주세요.')
            if not plan['validation'].get('valid'):raise ValueError('검증을 통과한 계획이 필요합니다.')
            c.execute(insert(db.baselines).values(baseline_id=str(uuid4()),plan_version_id=plan_version_id,group_id=group_id,confirmed_by=actor_member_id).on_conflict_do_nothing(index_elements=['plan_version_id']))
            return dict(c.execute(sa.select(db.baselines).where(db.baselines.c.plan_version_id==plan_version_id)).mappings().one())

    def evaluation_context(self,group_id,plan_version_id,task_id,*,owner_member_id=None):
        """M2 boundary: an immutable confirmed baseline and one exact task. No evaluation."""
        with self.transaction() as c:
            plan=c.execute(sa.select(db.plans).where(db.plans.c.group_id==group_id,db.plans.c.plan_version_id==plan_version_id)).mappings().first()
            if not plan:raise ValueError('plan_version_id를 확인해주세요.')
            if plan['owner_member_id']!=owner_member_id:raise ValueError('개인 평가 기준은 해당 구성원 범위로만 조회합니다.')
            baseline=c.execute(sa.select(db.baselines).where(db.baselines.c.plan_version_id==plan_version_id)).mappings().first()
            if not baseline:raise ValueError('평가 기준으로 확인되지 않은 계획입니다.')
            task=c.execute(sa.select(db.tasks).where(db.tasks.c.plan_version_id==plan_version_id,db.tasks.c.task_id==task_id)).mappings().first()
            if not task:raise ValueError('task_id가 해당 계획 버전에 속하지 않습니다.')
            goal=c.execute(sa.select(db.shared_goals).where(db.shared_goals.c.goal_version_id==plan['shared_goal_version_id'])).mappings().one()
            return {'baseline_id':baseline['baseline_id'],'plan_version_id':plan_version_id,'task_id':task_id,
                'shared_goal_version_id':plan['shared_goal_version_id'],'member_goal_version_id':plan['member_goal_version_id'],
                'task':dict(task),'verification_method':goal['verification_method'],'privacy_scope':goal['privacy_scope']}
