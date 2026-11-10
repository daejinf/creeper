"""Resumable anonymous LIVE acceptance: actual AI + atomic PostgreSQL storage.

No service approval: all confirmations are explicitly simulated scenario input.
"""
import os,json
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.m0 import Runtime,TimelyClient,AIError
from wemeet_ai.m0.runtime import code_result
from wemeet_ai.shared.contracts import Message,ConfirmedGoal
from wemeet_ai.m1.c_goal_draft import GoalDraft,review_field,confirm_goal
from wemeet_ai.cli import execute

ROOT=Path(__file__).resolve().parents[2]
PATH=ROOT/'runtime/unified-live.json'
SCENARIOS=[
 ('study',1,'파이썬 반복문 문제 풀이 습관 만들기','각자 반복문 문제 6개를 풀고 오답 이유를 설명한다',['문제 풀이']),
 ('project',3,'분실물 검색 봇의 시연 가능한 프로토타입 계획','검색·등록·조회 기능을 시연하고 테스트 결과를 남긴다',['검색 API','등록 화면','검색 테스트']),
 ('design',5,'학교 축제 안내 화면과 발표 준비','핵심 화면 3개와 발표자료 초안을 팀이 검토한다',['메인 화면','상세 화면','접근성 검토','발표 구성','시연 흐름']),
 ('habit',8,'무리하지 않는 걷기 습관 함께 만들기','각자 주 2회 10분 걷고 완료 여부를 기록한다',['본인 걷기 기록']*8),
]

def save(data):
    tmp=PATH.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8');os.replace(tmp,PATH)

class Budget:
    def __init__(self,report):self.report=report
    def reserve(self):
        if self.report['http_reserved']>=120:raise AIError('EVALUATION_BUDGET_EXCEEDED','운영 검증 HTTP 120회 한도')
        self.report['http_reserved']+=1;save(self.report)

def main():
    os.environ['TIMELY_API_KEY']=user_get('TIMELY_API_KEY') or ''
    report=json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else {'http_reserved':0,'groups':{},'confirmation_type':'ANONYMOUS_SIMULATED','reviewer':'AGENT_NOT_HUMAN'}
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'))
    adapter=ExecutionAdapter(store,EncryptedRecoveryStore(ROOT/'runtime/recovery',user_get('WEMEET_RECOVERY_KEY')))
    runtime=Runtime(TimelyClient(budget=Budget(report))) # no cache, no reference injection
    for sid,count,title,criteria,roles in SCENARIOS:
        group=report['groups'].setdefault(sid,{'members':count,'stages':{}})
        members=[{'member_id':sid+'-'+str(i+1),'nickname':f'검증{sid}{i+1}'} for i in range(count)]
        if 'record_id' not in group:
            group['record_id']=store.create('운영검증 · '+title,members=members,reference_date='2026-11-05').record_id;save(report)
        rid=group['record_id'];goal={'goal':title,'start_date':'2026-11-06','end_date':'2026-11-20','completion_criteria':[criteria],
            'execution_method':'각자 맡은 결과물을 작은 작업으로 나눠 수행하고 중간 결과를 검토한다.' if sid!='habit' else '편한 속도로 10분 걷기를 주 2회 수행하고 불편하면 중단한다.',
            'verification_method':'완료 여부와 간단한 결과 기록','privacy_scope':'OBJECTIVE_ONLY','roles':{m['member_id']:roles[i] for i,m in enumerate(members)},'members':members,'confirmed':True}
        texts=[f"우리 목표는 {title}야. 완료 기준은 {criteria}로 하자.",'2026-11-06 시작해서 2026-11-20까지 하자.',
            '인증은 완료 여부와 간단한 결과 기록으로 하고 그룹에는 완료 여부와 달성률만 공개하자.',
            '담당은 '+', '.join(m['nickname']+':'+roles[i] for i,m in enumerate(members)),
            '무리한 작업량 대신 각자 가능한 시간 안에서 단계별 실행 계획을 제안해줘.']
        for i,text in enumerate(texts):
            store.append_message(rid,Message(message_id=sid+':m'+str(i),speaker_id=members[i%count]['member_id'],text=text,created_at='2026-11-05T09:00:00+09:00'),scope='PUBLIC')
        def run(stage,module,payload,runner=None):
            old=group['stages'].get(stage)
            if old and old['status']=='OK':return store.find_action(rid,old['action_id']).response
            action=(old or {}).get('action_id',stage+':'+str(uuid4()))
            if old and old.get('persistence')=='PERSISTED':action=stage+':retry:'+str(uuid4())
            group['stages'][stage]={'action_id':action,'status':'IN_PROGRESS'};save(report)
            value=adapter.execute_and_store(module,rid,action,store.load(rid).revision,payload,runner=runner,runtime=runtime)
            group['stages'][stage]={'action_id':action,'status':value['status'],'persistence':value.get('persistence',{}).get('persistence_status'),'meta':value.get('meta',{}),'error':value.get('error')};save(report)
            print(sid,stage,value['status'],value.get('persistence',{}).get('persistence_status'),flush=True)
            if value['status']!='OK':raise RuntimeError('Stage requires inspection: '+sid+'/'+stage)
            return value
        try:
            view=store.load(rid);public={'conversation':[m.model_dump(mode='json') for m in view.conversation],'members':members,'reference_date':'2026-11-05'}
            run('A','A',public)
            public['conversation']=[m.model_dump(mode='json') for m in store.load(rid).conversation]
            analysis=run('B','B',public)
            draft=run('C','C',{'facts':analysis['data']['facts'],'members':members,'reference_date':'2026-11-05','mode':'ASSISTED'})
            reviewed=GoalDraft.model_validate(draft['data'])
            for key,value in goal.items():
                if key in {'members','confirmed'}:continue
                reviewed=review_field(reviewed,key,value=value,edit=True,actor_id='ANONYMOUS_SIMULATED:'+sid).data
            run('review','C.review',{'draft':draft['data'],'updates':goal,'actor_member_id':members[0]['member_id']},lambda:code_result('C.review',reviewed))
            run('confirm','C.confirm',{'draft':reviewed.model_dump(mode='json'),'members':members},lambda:confirm_goal(reviewed,members=ConfirmedGoal.model_validate(goal).members))
            run('D','D',goal)
            for index,member in enumerate(members):
                mid=member['member_id'];context={k:v for k,v in goal.items() if k in {'goal','start_date','end_date','completion_criteria','verification_method','privacy_scope'}};context['role']=roles[index]
                minutes=[60,180,90,120,240,75,150,100][index];level='처음 해봄' if index%2==0 else '혼자 진행한 경험 있음'
                if index<2:
                    payload={'context':context,'owner_member_id':mid,'reference_date':'2026-11-05','conversation':[]}
                    first=run(mid+':coach','E.coach',payload)
                    question=first['data']['state'].get('question');answertext=f'수준은 {level}, 주간 가능 시간은 {minutes}분입니다. 맡은 역할은 {roles[index]}이고 아직 시작 전입니다. 개인 사유는 그룹에 공개하지 마세요.'
                    if question and question.get('options'):
                        option=next((o for o in question['options'] if o.get('action')=='VALUE'),None)
                        if option:
                            message={'message_id':mid+':answer1','speaker_id':mid,'text':option['label'],'created_at':'2026-11-05T10:00:00+09:00','role':'user'}
                            nextpayload={**payload,'conversation':[message],'state':first['data']['state'],'answer':{'question_id':question['question_id'],'option_id':option['option_id'],'message_id':message['message_id']}}
                            run(mid+':button','E.coach',nextpayload)
                    conv=[m.model_dump(mode='json') for m in store.load(rid,owner_member_id=mid).private_conversation]
                    if not any(m['message_id']==mid+':conditions' for m in conv):conv.append({'message_id':mid+':conditions','speaker_id':mid,'text':answertext,'created_at':'2026-11-05T10:01:00+09:00','role':'user'})
                    prior=store.find_action(rid,group['stages'].get(mid+':button',group['stages'][mid+':coach'])['action_id']).response
                    # An interrupted old worker must never be silently called again.
                    import sqlalchemy as sa
                    from wemeet_ai.persistence import journal_schema as j
                    with store.connect(readonly=True) as c:
                        pending=[dict(o) for o in c.execute(sa.select(j.operations).where(j.operations.c.record_id==rid,j.operations.c.owner==mid,j.operations.c.module=='E.coach',j.operations.c.state.in_(['RUNNING','UNKNOWN']))).mappings()
                            if any(m.get('message_id')==mid+':conditions' for m in o['payload'].get('conversation',[]))]
                    if pending:
                        op=pending[0]
                        state=adapter.execute_and_store('E.coach',rid,op['action_id'],0,op['payload'],runner=lambda:(_ for _ in ()).throw(AssertionError('uncertain operation must not rerun')))
                        group.setdefault('uncertain_operations',{})[mid]={'operation_id':op['operation_id'],'state':state['persistence']['persistence_status'],'action':'NOT_RECALLED; explicit scenario conditions used for independent E.plan'};save(report)
                    else:run(mid+':conditions','E.coach',{**payload,'conversation':conv,'state':prior['data']['state']})
                contribution={'title':roles[index],'deliverable':criteria if sid in {'study','habit'} else roles[index]+' 담당 결과물 초안','completion_criteria':[criteria if sid in {'study','habit'} else roles[index]+' 결과물을 팀이 확인할 수 있다'],'confirmed':True}
                personal={'context':context,'owner_member_id':mid,'contribution':contribution,'profile':{'current_level':level,'weekly_available_minutes':minutes,'confirmed':True}}
                run(mid+':confirm','E.contribution.confirm',personal,lambda p=personal:code_result('E.contribution.confirm',p))
                plan=run(mid+':plan','E.plan',personal)
                baseline=store.goals.confirm_baseline(rid,plan['data']['plan_version_id'],mid)
                for task in plan['data']['tasks']:store.goals.evaluation_context(rid,plan['data']['plan_version_id'],task['task_id'],owner_member_id=mid)
                group.setdefault('baselines',{})[mid]=str(baseline['baseline_id']);save(report)
            public_data=json.dumps(store.goals.public_group(rid),ensure_ascii=False,default=str)
            assert 'weekly_available_minutes' not in public_data and '개인 사유' not in public_data
            group['database_checks']='PASS';group.pop('blocked',None);save(report)
        except Exception as exc:
            group['blocked']=type(exc).__name__;save(report);print(sid,'BLOCKED',type(exc).__name__,flush=True)
    print(json.dumps({'http_reserved':report['http_reserved'],'groups_complete':sum(g.get('database_checks')=='PASS' for g in report['groups'].values())}),flush=True)
if __name__=='__main__':main()
