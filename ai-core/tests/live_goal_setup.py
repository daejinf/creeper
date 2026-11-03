"""Explicit paid LIVE checks; not collected by pytest. Saves every completed request."""
import json
from pathlib import Path
from uuid import uuid4
import requests
from wemeet_ai.contracts import AnalysisRequest, Message
from wemeet_ai.examples import design_conversation

def run():
    folder=Path('ai-core/runs/v10-live');folder.mkdir(parents=True,exist_ok=True)
    original=design_conversation()
    cases={'design':original,'sparse':original.model_copy(deep=True),'milestones':original.model_copy(deep=True)}
    for name,text in [('sparse','동아리 소개 홈페이지를 함께 만들고 싶어. 소개와 가입 안내가 있으면 좋겠어. 어떻게 진행하면 좋을까?'),
        ('milestones','11/1부터 동아리 소개 웹사이트를 만들자. 11/7 중간 목표, 11/14 개발 완료, 11/21 최종 제출이야. 소개와 가입 안내가 시연되면 완료로 하자. 매주 금요일 이미지로 인증하고 완료 여부만 공개하자.')]:
        cases[name].conversation=[Message(message_id=name+'-1',speaker_id='j',role='user',text=text,created_at=original.conversation[0].created_at)]
    summary=[]
    for name,source in cases.items():
        path=folder/(name+'.json')
        if path.exists() and json.loads(path.read_text(encoding='utf-8')).get('passed'):continue
        prior=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if prior:
            backup=path.with_name(name+'-initial.json.bak')
            if not backup.exists():backup.write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
        records=list(prior['steps']) if prior else [];headers={'X-Workspace-Id':'live-setup-v10-'+name,'X-Trace-Id':str(uuid4())}
        def save(**extra):path.write_text(json.dumps({'source':'LIVE','case':name,'steps':records,**extra},ensure_ascii=False,indent=2),encoding='utf-8')
        def call(op,payload):
            res=requests.post('http://127.0.0.1:8010/ai/v1/'+op,json=payload,headers=headers,timeout=300)
            out=res.json();records.append({'operation':op,'request':payload,'response':{k:v for k,v in out.items() if k!='traces'}});save()
            assert res.ok and out['status']=='OK',str(out.get('error') or out.get('questions') or out)
            print(name,op,out['status'],out['meta']['usage']['total_tokens'],flush=True);return out
        try:
            saved_coach=next((r['response'] for r in records if r['request'].get('action')=='COACH' and r['response']['status']=='OK'),None)
            coach=saved_coach or call('goal-setup',{'action':'COACH',**source.model_dump(mode='json')})
            assert coach['data']['reply'] and '필요한 가정' not in coach['data']['reply']
            usable=next((r['response']['data']['state'] for r in reversed(records) if r['request'].get('action')=='REVIEW' and r['response'].get('data',{}).get('state',{}).get('analysis')),None)
            if usable:usable['draft']=None;usable['draft_source_fingerprint']=''
            review=call('goal-setup',{'action':'REVIEW','state':usable or coach['data']['state']})
            state=review['data']['state'];fields=state['draft']['fields']
            required=['goal','expected_outcome','completion_criteria','start_date','end_date','execution_method','verification_method','privacy_scope']
            assert all(fields[k]['value'] for k in required),[k for k in required if not fields[k]['value']]
            assert all(v['review_status']=='PENDING' for v in fields.values())
            if name=='design':
                assert fields['start_date']['value']=='2026-10-28' and fields['end_date']['value']=='2026-11-15'
                dates={m['due_date'] for m in fields['milestones']['value']};assert {'2026-11-03','2026-11-10','2026-11-15'}<=dates
            if name=='sparse':
                for key in ['start_date','end_date','verification_method','privacy_scope']:assert fields[key]['origin']=='AI_SUGGESTED'
            if name=='milestones':assert fields['end_date']['value']=='2026-11-21'
            accepted=call('goal-setup',{'action':'RESPOND','state':state,'actor_member_id':'j','response':{'operation':'ACCEPT','fields':list(fields)}})
            assert accepted['meta']['usage']['total_tokens']==0
            confirmed=call('goal-setup',{'action':'CONFIRM','state':accepted['data']['state'],'actor_member_id':'j'})
            assert confirmed['data']['group_spec'] and confirmed['meta']['usage']['total_tokens']==0
            plan=call('group-plan',confirmed['data']['group_spec'])
            validation=call('validate',{'goal_spec':confirmed['data']['group_spec'],'group_plan':plan['data'],'member_plans':[],'require_all_members':False})
            assert validation['data']['valid'],validation['data']['issues']
            save(passed=True,http_attempts=sum(r['response']['meta']['transport_attempts'] for r in records),
                tokens=sum(r['response']['meta']['usage']['total_tokens'] for r in records),semantic_review='AGENT_REVIEW_PENDING_NOT_HUMAN_APPROVED')
        except Exception as exc:save(passed=False,failure=str(exc));print(name,'FAILED',str(exc),flush=True)
    for path in folder.glob('*.json'):
        data=json.loads(path.read_text(encoding='utf-8'));summary.append({k:v for k,v in data.items() if k!='steps'})
    print(json.dumps(summary,ensure_ascii=True))

if __name__=='__main__':run()
