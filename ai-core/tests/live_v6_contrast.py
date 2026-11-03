"""Explicit LIVE diagnostic contrast, using only anonymous fixtures."""
import json
from datetime import timedelta
from pathlib import Path
import requests
from wemeet_ai import GoalAI
from wemeet_ai.examples import example_input
from wemeet_ai.contracts import GroupPlan

report={'live':True,'scenarios':[]}
for kind in ('study','exercise'):
    spec=GoalAI().prepare_goal_spec(example_input(kind)).data
    weeks=(spec.end_date-spec.start_date).days//7+1
    group=GroupPlan(plan_id='contrast-'+kind,spec_id=spec.spec_id,**{key:getattr(spec,key) for key in ('goal','expected_outcome','completion_criteria','start_date','end_date','frequency_per_week','metric')},
        milestones=[{'title':spec.goal+' 결과 확인','due_date':spec.end_date,'completion_criteria':spec.completion_criteria[0],'evidence_refs':spec.evidence_ids}],
        weekly_outline=[{'week':i,'outcome':spec.goal,'tasks':[spec.execution_method]} for i in range(1,weeks+1)],evidence_refs=spec.evidence_ids)
    payload={'diagnostic_mode':'ADAPTIVE','goal_spec':spec.model_dump(mode='json'),'group_plan':group.model_dump(mode='json'),'owner_member_id':spec.member_profiles[0].member_id,'conversation':[],'reference_date':'2026-10-28'}
    result=requests.post('http://127.0.0.1:8011/ai/v1/member-goal/coach',json=payload,timeout=180).json()
    assert result['status']=='OK',result
    dimensions=result['data']['state']['diagnostic_dimensions']
    assert 4<=len(dimensions)<=6
    report['scenarios'].append({'kind':kind,'reply':result['data']['reply'],'question':result['data']['state']['current_question'],'dimensions':dimensions,'meta':result['meta']})
assert report['scenarios'][0]['reply']!=report['scenarios'][1]['reply']
assert any(word in json.dumps(report['scenarios'][0],ensure_ascii=False) for word in ('학습','문제','공부'))
assert any(word in json.dumps(report['scenarios'][1],ensure_ascii=False) for word in ('걷기','운동','달리기'))
report['passed']=True
Path('runs/live-v6-contrast.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'scenarios':[{ 'kind':s['kind'],'dimensions':[d['label'] for d in s['dimensions']],'tokens':s['meta']['usage']['total_tokens']} for s in report['scenarios']]},ensure_ascii=False))
