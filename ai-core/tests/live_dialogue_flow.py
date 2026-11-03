"""Explicitly authorized small LIVE A->B->C regression; real calls and usage are saved."""
import json
from pathlib import Path
from uuid import uuid4
import requests


def run(base='http://127.0.0.1:8010'):
    headers={'X-Workspace-Id':'live-dialogue-check-'+str(uuid4()),'X-Trace-Id':str(uuid4())}
    conversation=[];steps=[];members=[{'member_id':'j','nickname':'지민'},{'member_id':'s','nickname':'서준'}]
    def message(role,text,speaker=None):
        return {'message_id':str(uuid4()),'speaker_id':speaker,'role':role,'text':text,'created_at':'2026-10-29T19:00:00+09:00'}
    def call(operation,payload):
        response=requests.post(base+'/ai/v1/'+operation,json=payload,headers=headers,timeout=300);response.raise_for_status();result=response.json()
        steps.append({'operation':operation,'request':payload,'response':{k:v for k,v in result.items() if k!='traces'}})
        Path('ai-core/runs/live-dialogue-flow-v9.json').write_text(json.dumps({'source':'LIVE','steps':steps},ensure_ascii=False,indent=2),encoding='utf-8')
        assert result['status']=='OK',str(result.get('error'))
        return result
    state={}
    for text in [
        '우리 둘이 동아리 소개 웹사이트를 만들고 싶어. 소개와 가입 안내가 필요해. 너무 크게 시작하지 않고 무엇부터 하면 좋을지 코칭해줘.',
        '소개와 가입 안내 두 화면부터 하자. 나는 디자인, 서준이는 개발을 맡는 안이야. 두 화면이 실제로 연결되고 가입 안내를 읽을 수 있으면 완료로 보자. 어떻게 나눠 진행하면 좋을까?',
        '일정은 2026-11-01부터 2026-11-15까지로 제안해. 주 2회 작업하고 결과 화면과 짧은 설명으로 인증하자. 그룹에는 완료 여부와 달성률만 공개하고, 금요일 저녁에 진행 상황을 공유하자. 작업은 화면 구성 정리, 디자인 시안, 구현, 연결 확인 순서로 하자. 지금 내용을 바탕으로 실행 초안을 알려줘.',
    ]:
        conversation.append(message('user',text,'j'))
        payload={'coaching_mode':'DIALOGUE','conversation':conversation,'members':members,'reference_date':'2026-10-29','timezone':'Asia/Seoul','coach_state':state}
        result=call('coach',payload);assert result['data']['proposals']==[] and result['data']['next_action']=='CONTINUE'
        assert result['data']['reply'].strip();state=result['data']['updated_coach_state'];conversation.append(message('assistant',result['data']['reply']))
    analysis=call('analyze',{'conversation':conversation,'members':members,'reference_date':'2026-10-29','timezone':'Asia/Seoul'})
    draft=call('goal-spec/draft',{'analysis':analysis['data'],'draft_mode':'GROUNDED','reference_date':'2026-10-29','timezone':'Asia/Seoul'})
    fields=draft['data']['fields'];assert all(f['review_status']=='PENDING' for f in fields.values())
    assert fields['start_date']['value']=='2026-11-01' and fields['end_date']['value']=='2026-11-15'
    assert fields['privacy_scope']['value']=='OBJECTIVE_ONLY'
    assert not fields['metric']['value']
    report={'source':'LIVE','passed':True,'coaching_turns':3,'coaching_notes':len(analysis['data'].get('coaching_notes',[])),
        'http_attempts':sum(s['response']['meta']['transport_attempts'] for s in steps),'total_tokens':sum(s['response']['meta']['usage']['total_tokens'] for s in steps),'steps':steps,
        'limitations':['기능 회귀 1건이며 의미적 품질 전체를 보증하지 않습니다.','사람 검토·그룹 승인을 생성하지 않습니다.']}
    Path('ai-core/runs/live-dialogue-flow-v9.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'steps','limitations'}},ensure_ascii=True))

def resume_bc(base='http://127.0.0.1:8010'):
    path=Path('ai-core/runs/live-dialogue-flow-v9.json');original=json.loads(path.read_text(encoding='utf-8'));steps=original['steps']
    backup=path.with_name('live-dialogue-flow-v9-first-attempt.json')
    if not backup.exists():backup.write_text(json.dumps(original,ensure_ascii=False,indent=2),encoding='utf-8')
    source=next(s for s in steps if s['operation']=='analyze')['request'];new=[]
    previous=[]
    recovered_analysis=Path('ai-core/runs/live-dialogue-bc-recovered-analysis.json');recovered_draft=Path('ai-core/runs/live-dialogue-bc-recovered-draft.json')
    if recovered_analysis.exists() and recovered_draft.exists():
        a=json.loads(recovered_analysis.read_text(encoding='utf-8'));c=json.loads(recovered_draft.read_text(encoding='utf-8'))
        previous=[{'operation':'analyze','request':source,'response':a},{'operation':'goal-spec/draft','request':{'analysis':a['data'],'draft_mode':'GROUNDED','reference_date':'2026-10-29','timezone':'Asia/Seoul'},'response':c}]
    attempt_path=Path('ai-core/runs/live-dialogue-bc-current-attempt.json')
    headers={'X-Workspace-Id':'live-dialogue-bc-v9-1-'+str(uuid4()),'X-Trace-Id':str(uuid4())}
    def request(operation,payload):
        response=requests.post(base+'/ai/v1/'+operation,json=payload,headers=headers,timeout=300);response.raise_for_status();out=response.json()
        new.append({'operation':operation,'request':payload,'response':{k:v for k,v in out.items() if k!='traces'}})
        attempt_path.write_text(json.dumps({'source':'LIVE','steps':new},ensure_ascii=False,indent=2),encoding='utf-8')
        assert out['status']=='OK',str(out.get('error'));return out
    analysis=request('analyze',source)
    assert analysis['data']['fields']['privacy']['status']!='CONFLICTED'
    draft=request('goal-spec/draft',{'analysis':analysis['data'],'draft_mode':'GROUNDED','reference_date':'2026-10-29','timezone':'Asia/Seoul'})
    f=draft['data']['fields'];assert f['privacy_scope']['value']=='OBJECTIVE_ONLY'
    assert f['start_date']['value']=='2026-11-01' and f['end_date']['value']=='2026-11-15'
    assert f['metric']['value'] is None and all(v['review_status']=='PENDING' for v in f.values())
    all_steps=steps+previous+new;successful=[s for s in steps if s['operation']=='coach']+new
    report={'source':'LIVE','passed':True,'coaching_turns':3,'extra_A_calls':0,'post_evaluation_fix':'canonical privacy and explicit dates v9.2','steps':successful,'superseded_steps':[s for s in steps if s['operation']!='coach']+previous,
        'http_attempts':sum(s['response']['meta']['transport_attempts'] for s in all_steps),'total_tokens':sum(s['response']['meta']['usage']['total_tokens'] for s in all_steps),
        'limitations':['실제 흐름 1건의 회귀 검증이며 전체 의미적 품질을 보증하지 않습니다.','사람 검토·승인은 생성하지 않았습니다.']}
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k not in {'steps','superseded_steps','limitations'}},ensure_ascii=True))

if __name__=='__main__':
    import sys
    resume_bc() if '--resume-bc' in sys.argv else run()
