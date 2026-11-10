"""Explicit agent review of anonymous scenarios before simulated baselines."""
import json
from pathlib import Path
from uuid import uuid4
from datetime import date
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.continuity import latest
from wemeet_ai.shared.contracts import Plan
from wemeet_ai.m0.runtime import code_result
ROOT=Path(__file__).resolve().parents[2]

def main():
    old=json.loads((ROOT/'runtime/unified-live.json').read_text(encoding='utf-8'))
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'))
    adapter=ExecutionAdapter(store,EncryptedRecoveryStore(ROOT/'runtime/recovery',user_get('WEMEET_RECOVERY_KEY')))
    report={'reviewer':'AGENT_NOT_HUMAN','confirmation':'ANONYMOUS_SIMULATED','actual_http':0,'cases':{}}
    for name,group in old['groups'].items():
        rid=group['record_id'];view=store.load(rid);heads=store.goals.public_group(rid)['heads'];edits=[];baselines={}
        for member in view.members:
            mid=member.member_id;v=store.load(rid,owner_member_id=mid)
            artifact=next(a for a in reversed(v.artifacts) if a.module=='E.plan' and (a.response.get('data') or {}).get('plan_version_id')==heads['current_plans'][mid])
            data=json.loads(json.dumps(artifact.response['data']));changed=False
            if name=='habit':
                for task in data['tasks']:
                    if task['title'].startswith('셋째 주') and '2026-11-13'<=task['due_date']<='2026-11-19':
                        before=task['title'];task['title']=task['title'].replace('셋째 주','둘째 주',1);changed=True;edits.append({'member':mid,'field':'task.title','before':before,'after':task['title'],'reason':'날짜는 둘째 7일 구간에 속함'})
                    for i,text in enumerate(task['completion_criteria']):
                        if '공개 기록' in text:
                            after=text.replace('공개 기록','본인 기록(그룹에는 완료 여부·달성률만 공개)');task['completion_criteria'][i]=after;changed=True
                            edits.append({'member':mid,'field':'task.completion_criteria','before':text,'after':after,'reason':'확인된 OBJECTIVE_ONLY 범위를 정확히 표현'})
                walks=[t for t in data['tasks'] if '10분 걷기' in t['title'] and not any(w in t['title'] for w in ('점검','정리'))]
                assert len(walks)==4 and len({t['due_date'] for t in walks})==4
                assert sum('2026-11-06'<=t['due_date']<='2026-11-12' for t in walks)==2
                assert sum('2026-11-13'<=t['due_date']<='2026-11-19' for t in walks)==2
                assert not any(t['due_date']=='2026-11-20' for t in walks)
            if changed:
                previous=data['plan_version_id'];data['previous_plan_version_id']=previous;data['plan_version_id']=str(uuid4())
                ids={t['task_id']:str(uuid4()) for t in data['tasks']}
                for task in data['tasks']:task['task_id']=ids[task['task_id']];task['dependencies']=[ids.get(v,v) for v in task['dependencies']]
                result=code_result('E.plan',Plan.model_validate(data));result.meta.source_run_id=artifact.response['meta']['run_id'];result.warnings.append('에이전트가 날짜 주차 표기·공개 문구만 검토 수정한 새 버전입니다. 사람 검토나 실제 사용자 승인이 아닙니다. 신규 HTTP 0회.')
                out=adapter.execute_and_store('E.plan',rid,'semantic-reviewed-labels:'+mid,store.load(rid).revision,artifact.request,runner=lambda r=result:r)
                assert out['status']=='OK' and out['persistence']['persistence_status']=='PERSISTED';data=out['data']
            baseline=store.goals.confirm_baseline(rid,data['plan_version_id'],mid)
            for task in data['tasks']:store.goals.evaluation_context(rid,data['plan_version_id'],task['task_id'],owner_member_id=mid)
            baselines[mid]=str(baseline['baseline_id'])
        current=store.goals.public_group(rid);assert len(current['heads']['current_member_goals'])==group['members']
        assert len(current['heads']['current_plans'])==group['members']+1
        if name=='design':
            plan=next(p for p in current['group_plans'] if p['plan_version_id']==current['heads']['current_plans']['GROUP'])
            assert any('행사 일정 화면' in t['title'] and t['owner_member_id']=='design-2' for t in plan['tasks'])
        report['cases'][name]={'status':'REVIEWED_WITH_LIMITATIONS','members':group['members'],'current_baselines':baselines,'manual_label_edits':edits,
            'limitations':['A/B/C는 후보·제안이며 사람 확인을 대신하지 않음','주간 요약 문구와 과제별 일정의 완전한 의미 일치는 추가 검토 대상']}
    (ROOT/'runtime/semantic-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'reviewed_cases':len(report['cases']),'current_member_baselines':sum(len(g['current_baselines']) for g in report['cases'].values()),'manual_edits':sum(len(g['manual_label_edits']) for g in report['cases'].values()),'actual_http':0}))
if __name__=='__main__':main()
