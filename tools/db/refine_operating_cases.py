"""Semantic-review corrections with explicit anonymous simulated decisions.

No overwritten history. Reuse unaffected design plans; regenerate only changed
tasks. This is a scenario review, never a real user's approval.
"""
import os,json
from pathlib import Path
from uuid import uuid4
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.m0 import Runtime,TimelyClient,AIError
from wemeet_ai.m0.runtime import code_result
from wemeet_ai.m1.c_goal_draft import GoalDraft,review_field,confirm_goal
from wemeet_ai.shared.contracts import Message,GoalContext,Member
from wemeet_ai.continuity import latest
ROOT=Path(__file__).resolve().parents[2];PATH=ROOT/'runtime/refined-live.json'

def main():
    os.environ['TIMELY_API_KEY']=user_get('TIMELY_API_KEY') or ''
    old=json.loads((ROOT/'runtime/unified-live.json').read_text(encoding='utf-8'))
    report=json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else {'http_reserved':0,'groups':{},'confirmation':'ANONYMOUS_SIMULATED_AFTER_AGENT_REVIEW'}
    def save():PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    class Budget:
        def reserve(self):
            if old['http_reserved']+1+report['http_reserved']>=120:raise AIError('BUDGET_LIMIT','통합 운영 검증 120회 제한')
            report['http_reserved']+=1;save()
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'));adapter=ExecutionAdapter(store,EncryptedRecoveryStore(ROOT/'runtime/recovery',user_get('WEMEET_RECOVERY_KEY')))
    runtime=Runtime(TimelyClient(budget=Budget()))
    for name in ('design','habit'):
        group=report['groups'].setdefault(name,{'stages':{}});rid=old['groups'][name]['record_id'];view=store.load(rid)
        if 'previous_plans' not in group:
            public=store.goals.public_group(rid);group['previous_plans']=public['heads']['current_plans'];save()
        def run(stage,module,payload,runner=None):
            entry=group['stages'].get(stage)
            if entry and entry['status']=='OK':return store.find_action(rid,entry['action_id']).response
            action=(entry or {}).get('action_id','semantic:'+stage+':'+str(uuid4()))
            if entry and entry['status']=='FAILED':action='semantic:'+stage+':corrected:'+str(uuid4())
            group['stages'][stage]={'status':'IN_PROGRESS','action_id':action};save()
            result=adapter.execute_and_store(module,rid,action,store.load(rid).revision,payload,runner=runner,runtime=runtime)
            group['stages'][stage].update(status=result['status'],persistence=result.get('persistence',{}),meta=result.get('meta',{}));save()
            print(name,stage,result['status'],flush=True)
            if result['status']!='OK' or result['persistence']['persistence_status']!='PERSISTED':raise RuntimeError('Inspect failed stage before retry')
            return result
        draft=GoalDraft.model_validate(latest(view,{'C','C.review'}).response['data'])
        if name=='design':
            roles=dict(draft.fields['roles'].value);roles['design-2']='상세 화면과 행사 일정 화면'
            updates={'roles':roles};text='모의 사용자 확인: 핵심 화면 3개는 메인·상세·행사 일정 화면으로 정하고, design-2가 상세와 행사 일정 화면을 담당합니다.'
        else:
            updates={'completion_criteria':['2026-11-06~11-12에 10분 걷기 2회, 11-13~11-19에 2회로 각자 총4회 완료한다.','2026-11-20은 추가 걷기 없이 기록만 점검한다.'],'frequency_per_week':2}
            text='모의 사용자 확인: 첫7일과 둘째7일에 각2회씩 총4회, 마지막11월20일은 추가 걷기 없이 기록만 점검합니다.'
        store.append_message(rid,Message(message_id='semantic-review:'+name,speaker_id=view.members[0].member_id,text=text,created_at='2026-11-05T12:00:00+09:00'),scope='PUBLIC')
        for key,value in updates.items():draft=review_field(draft,key,value=value,edit=True,actor_id='ANONYMOUS_SIMULATED_REVIEW').data
        run('review','C.review',{'draft':draft.model_dump(mode='json'),'updates':updates,'actor_member_id':view.members[0].member_id},lambda:code_result('C.review',draft))
        confirmed=run('confirm','C.confirm',{'draft':draft.model_dump(mode='json'),'members':[m.model_dump() for m in view.members]},lambda:confirm_goal(draft,members=view.members))
        goal=confirmed['data'];run('D','D',goal)
        for member in view.members:
            mid=member.member_id;private=store.goals.private_member(rid,mid)
            oldplan=next(p for p in private['plans'] if p['plan_version_id']==group['previous_plans'][mid])
            oldgoal=next(g for g in private['member_goals'] if g['member_goal_version_id']==oldplan['member_goal_version_id'])
            profile=dict(oldgoal['profile']['details'])
            contribution={'title':oldgoal['title'],'deliverable':oldgoal['deliverable'],'completion_criteria':oldgoal['completion_criteria'],'confirmed':True}
            context={k:v for k,v in goal.items() if k in GoalContext.model_fields};context['role']=goal['roles'][mid]
            if name=='habit':contribution.update(deliverable='서로 다른 날짜의 10분 걷기 4회 기록과 11월20일 최종 점검 기록',completion_criteria=updates['completion_criteria'])
            elif mid=='design-2':contribution.update(title='상세·행사 일정 화면',deliverable='같은 스타일의 상세 화면과 행사 일정 화면 초안',completion_criteria=['상세 화면과 행사 일정 화면 두 개를 팀이 검토한다.'])
            payload={'context':context,'owner_member_id':mid,'profile':profile,'contribution':contribution}
            run(mid+':confirm','E.contribution.confirm',payload,lambda p=payload:code_result('E.contribution.confirm',p))
            runner=None
            if name=='design' and mid!='design-2':
                # Only another member's role changed: reuse this person's content,
                # assigning NEW immutable version/task IDs linked to the new goal.
                from wemeet_ai.shared.contracts import Plan
                original=next(a for a in store.load(rid,owner_member_id=mid).artifacts if a.module=='E.plan' and (a.response.get('data') or {}).get('plan_version_id')==oldplan['plan_version_id'])
                data=json.loads(json.dumps(original.response['data']))
                data['previous_plan_version_id']=data['plan_version_id'];data['plan_version_id']=str(uuid4())
                ids={t['task_id']:str(uuid4()) for t in data['tasks']}
                for t in data['tasks']:
                    t['task_id']=ids[t['task_id']];t['dependencies']=[ids.get(v,v) for v in t['dependencies']]
                replay=code_result('E.plan',Plan.model_validate(data));replay.meta.execution_source='REPLAY'
                runner=lambda r=replay:r
            plan=run(mid+':plan','E.plan',payload,runner)
            # New baselines remain pending semantic review by the agent. They are
            # confirmed in a separate explicit validation step after inspection.
        group['plans_generated']=True;save()
    print(json.dumps({'additional_http':report['http_reserved']}),flush=True)
if __name__=='__main__':main()
