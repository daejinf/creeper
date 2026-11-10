"""Three explicit evaluation rounds. No LIVE call unless --live is supplied.

Each round uses the same 3 synthetic scenarios (5 module calls). Shared new budget: 30 HTTP.
The normal app's LIVE/cache settings are never changed by this evaluator.
"""
import argparse
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from wemeet_ai.m0 import Runtime, TimelyClient, AIError, MemoryTraceSink
from wemeet_ai.cli import execute
from wemeet_ai.m0.client import Completion
from wemeet_ai.shared.contracts import Usage

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'examples'))
from _data import sample

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def digest():
    return hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'src').rglob('*.py')))).hexdigest()

class Budget:
    def __init__(self,path):self.path=path;self.lock=threading.Lock()
    def reserve(self):
        with self.lock:
            old=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'http':0,'limit':30}
            if old['http']>=old.get('limit',30):raise AIError('EVALUATION_BUDGET','평가의 실제 HTTP 한도에 도달했습니다.')
            old['http']+=1;write(self.path,old)

def run(round_number,output,live=False):
    if round_number not in {1,2,3}:raise ValueError('Exactly rounds 1,2,3 are supported')
    folder=output/f'round-{round_number}'
    base={'A':sample('A'),'B':sample('B'),'C':sample('C'),'E.coach':sample('E.coach'),'E.plan':sample('E.plan')}
    base['E.coach']['conversation']=[{'message_id':'eval-private-1','speaker_id':'u1','role':'user',
        'text':'모임 운영은 조금 해봤어요. 이번 주에는 30분 가능하고, 아직 문서는 시작하지 않았어요. 공유 문서를 사용할 수 있어요.',
        'created_at':'2026-11-20T11:00:00+09:00'}]
    manifest_path=output/'manifest.json'
    if not manifest_path.exists():
        write(manifest_path,{'version':'0.12-three-rounds-v1','rounds':[1,2,3],'new_http_limit':30,
            'previous_0_11_http_not_included':116,'inputs':base,'judge_only':{'A':'문맥 조언, 반복 질문/임의 확정 금지',
            'B_C':'대화 사실·근거 보존, 추천은 미확인','E':'본인 조건만 사용, 30분 예산의 구체적 과제'},
            'confirmation_label':'AUTHORED_SYNTHETIC_INPUT_NOT_REAL_SERVICE_APPROVAL'})
    else:
        fixed=json.loads(manifest_path.read_text(encoding='utf-8'))['inputs']
        if fixed!=base:raise ValueError('Evaluation inputs changed; do not mix manifests')
    if not live:print('Manifest ready. No AI call.');return
    if (folder/'summary.json').exists():print('Round already completed; reading saved results. No new call.');return
    budget=Budget(output/'budget.json')
    before=json.loads(budget.path.read_text(encoding='utf-8'))['http'] if budget.path.exists() else 0
    results=[];source=digest()
    repo=None;record_ids={}
    if round_number>=2:
        from tools.bench.store import SQLiteRecordStore
        from wemeet_ai.continuity import prepare_module_input, Artifact
        from wemeet_ai.shared.contracts import Message
        repo=SQLiteRecordStore(folder/'synthetic-records.sqlite3')
        pointers=folder/'record-ids.json'
        if pointers.exists():record_ids=json.loads(pointers.read_text(encoding='utf-8'))
        else:
            public=repo.create('평가 공개 대화',members=base['A']['members'],reference_date=base['A']['reference_date'])
            personal=repo.create('평가 개인 대화',members=base['A']['members'],reference_date=base['A']['reference_date'])
            record_ids={'public':public.record_id,'private':personal.record_id};write(pointers,record_ids)
            repo.save_input(public.record_id,'A',base['A'])
            # Authored confirmed context is an evaluation precondition, not model approval.
            repo.save_input(personal.record_id,'D',sample('D'))
            repo.save_input(personal.record_id,'E.coach',base['E.coach'])
            repo.save_input(personal.record_id,'E.plan',base['E.plan'])
    for module in ['A','B','C','E.coach','E.plan']:
        path=folder/(module+'.json')
        if path.exists():
            record=json.loads(path.read_text(encoding='utf-8'));results.append(record);continue
        payload=base[module]
        if module=='C':
            analysis=next((r['response'].get('data') for r in results if r['module']=='B' and r['response']['status']=='OK'),None)
            if not analysis:
                results.append({'module':module,'status':'NOT_RUN_UPSTREAM_FAILED'});continue
            payload={**payload,'facts':analysis['facts'],'conflicted_fields':analysis.get('conflicted_fields',[])}
        prepared=None
        if repo:
            rid=record_ids['private' if module.startswith('E.') else 'public'];owner='u1' if module.startswith('E.') else None
            view=repo.load(rid,owner_member_id=owner)
            prepared=prepare_module_input(module,view)
            payload=prepared.request
            repo.save_input(rid,module,payload,dependencies=prepared.dependencies)
        trace=MemoryTraceSink();runtime=Runtime(TimelyClient(budget=budget),trace_sink=trace,debug=True)
        result=execute(module,payload,runtime=runtime)
        record={'module':module,'request':payload,'response':result.model_dump(mode='json'),'source_sha256':source,
            'executed_at':datetime.now(timezone.utc).isoformat()}
        write(path,record);write(folder/(module+'-private-trace.json'),{'events':trace.events})
        if repo:
            scope='PRIVATE' if module.startswith('E.') else 'PUBLIC'
            repo.save_artifact(rid,Artifact(artifact_id=str(__import__('uuid').uuid4()),action_id='evaluation-'+module,module=module,scope=scope,
                owner_member_id=owner,request=payload,response=record['response'],dependencies=prepared.dependencies))
            if module=='A' and result.status=='OK':
                repo.append_message(rid,Message(message_id='eval-assistant-A',role='assistant',text=result.data.reply,
                    created_at='2026-11-20T10:02:00+09:00'),scope='PUBLIC')
        results.append(record)
        if result.error and result.error.code=='EVALUATION_BUDGET':break
    after=json.loads(budget.path.read_text(encoding='utf-8'))['http'] if budget.path.exists() else 0
    summary={'round':round_number,'scenario_count':3,'planned_module_calls':5,'record_backed_inputs':bool(repo),
        'executed_modules':sum('response'in r for r in results),'ok':sum(r.get('response',{}).get('status')=='OK' for r in results),
        'new_http':after-before,'global_new_http':after,'tokens':sum(r.get('response',{}).get('meta',{}).get('usage',{}).get('total_tokens',0) for r in results),
        'source_sha256':source,'semantic_review':'PENDING_AGENT_REVIEW','human_review':False}
    write(folder/'summary.json',summary);print(json.dumps(summary))

class StoredClient:
    is_fixture=True
    base_url='replay://stored-live'
    model='STORED LIVE REPLAY · 신규 HTTP 0'
    def __init__(self, values): self.values=values;self.index=0
    def complete_json(self,messages,output_schema,options=None,*,stats=None):
        if not self.values: raise AIError('RAW_NOT_RECORDED','원문을 보관하지 않은 실패는 재검사할 수 없습니다.')
        value=self.values[min(self.index,len(self.values)-1)];self.index+=1
        out=output_schema.model_validate(value)
        return Completion(out.model_dump_json(),self.model,None,'stop',Usage(),0,0,out)
    def complete_text(self,messages,options=None,*,stats=None):
        if not self.values:raise AIError('RAW_NOT_RECORDED','원문 없음')
        return Completion(self.values[-1]['reply'],self.model,None,'stop',Usage(),0,0)

def replay(path):
    stored=json.loads(path.read_text(encoding='utf-8'))
    trace=path.with_name(path.stem+'-private-trace.json')
    values=[]
    if trace.exists():
        events=json.loads(trace.read_text(encoding='utf-8'))['events']
        values=[e['sanitized_payload'] for e in events if e['event']=='json.parsed']
    if not values and stored['response'].get('data') and stored['response']['status']=='OK':values=[stored['response']['data']]
    client=StoredClient(values)
    result=execute(stored['module'],stored['request'],runtime=Runtime(client))
    result.meta.source_run_id=stored['response']['meta']['run_id']
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--round',type=int,choices=[1,2,3]);p.add_argument('--replay-path',type=Path);p.add_argument('--live',action='store_true');p.add_argument('--output',type=Path,default=ROOT/'runtime/evaluation-012')
    args=p.parse_args()
    if args.replay_path:
        rows=[]
        for path in sorted(args.replay_path.rglob('*.json')):
            try:
                doc=json.loads(path.read_text(encoding='utf-8'))
                if not all(k in doc for k in ['module','request','response']):continue
                result=replay(path)
                rows.append({'file':str(path),'status':result.status,'error':result.error.code if result.error else None})
            except Exception as exc:rows.append({'file':str(path),'status':'UNREPLAYABLE','error':type(exc).__name__})
        report={'label':'REPLAY_NOT_NEW_LIVE','records':len(rows),'rows':rows,'new_http':0}
        write(args.output/'replay.json',report);print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
    elif args.round:run(args.round,args.output,args.live)
    else:p.error('--round 1|2|3 또는 --replay-path가 필요합니다.')
