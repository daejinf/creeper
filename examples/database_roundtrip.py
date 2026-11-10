"""Run from the project root. No API key or model call.

Uses explicit anonymous fixtures and simulated review, not real user approval.
By default ALL writes roll back. --save keeps this new example group.
"""
import argparse
import json
import os
from uuid import uuid4
import sqlalchemy as sa
from wemeet_ai.persistence import GoalRepository
from wemeet_ai.shared.contracts import ConfirmedGoal, Member, Plan, Task

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--save',action='store_true');args=parser.parse_args()
    engine=sa.create_engine(os.environ['WEMEET_GOAL_DATABASE_URL'],hide_parameters=True)
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            store=GoalRepository(connection=connection)
            group_id,shared_id,member_goal_id,plan_id,task_id=[str(uuid4()) for _ in range(5)]
            members=[Member(member_id='design',nickname='디자인 담당'),Member(member_id='research',nickname='조사 담당'),Member(member_id='presenter',nickname='발표 담당')]
            # In the real service, use actual user-reviewed values, not fixtures.
            goal=ConfirmedGoal(goal='수업 프로젝트 발표 준비',members=members,confirmed=True,
                start_date='2026-11-05',end_date='2026-11-19',completion_criteria=['핵심 화면 시연과 발표 연습 완료'],
                execution_method='역할별 초안 검토',verification_method='화면 시안과 검토 기록',privacy_scope='OBJECTIVE_ONLY')
            store.save_group(group_id,'익명 DB 연결 예제',[m.model_dump() for m in members])
            store.save_shared_goal(group_id,shared_id,goal,source_ref=shared_id)
            store.save_member_goal(group_id,'design',member_goal_id,shared_id,
                {'title':'메인 화면 시안','deliverable':'메인 화면 초안','completion_criteria':['화면 구성과 버튼 흐름을 설명할 수 있다'],'confirmed':True},
                {'current_level':'일부 경험','weekly_available_minutes':90,'confirmed':True},source_ref=member_goal_id)
            plan=Plan(plan_version_id=plan_id,owner_member_id='design',goal=goal.goal,start_date=goal.start_date,end_date=goal.end_date,
                tasks=[Task(task_id=task_id,owner_member_id='design',title='메인 화면 초안 정리',deliverable='메인 화면 시안 1개',
                    completion_criteria=['핵심 영역과 버튼 흐름이 포함됨'],due_date='2026-11-07',estimated_minutes=45)])
            store.save_plan(group_id,shared_id,plan,member_goal_version_id=member_goal_id,source_ref=plan_id)
            store.set_heads(group_id,1,shared_id,{'design':member_goal_id},{'design':plan_id})
            # Fixture-only simulated confirmation. Real SW must wait for user action.
            baseline=store.confirm_baseline(group_id,plan_id,'design')
            context=store.evaluation_context(group_id,plan_id,task_id,owner_member_id='design')
            assert context['baseline_id']==baseline['baseline_id']
            assert 'weekly_available_minutes' not in json.dumps(store.public_group(group_id),default=str)
            assert store.private_member(group_id,'design')['plans'][0]['tasks'][0]['estimated_minutes']==45
            if args.save:transaction.commit()
            else:transaction.rollback()
            print(json.dumps({'fixture':True,'paid_http':0,'saved':args.save,'group_id':group_id,
                'shared_goal_version_id':shared_id,'member_goal_version_id':member_goal_id,
                'plan_version_id':plan_id,'task_id':task_id,'baseline_id':baseline['baseline_id'],
                'roundtrip':'PASS','privacy_split':'PASS'},ensure_ascii=False,indent=2))
        finally:
            if transaction.is_active:transaction.rollback()
    engine.dispose()

if __name__=='__main__':main()
