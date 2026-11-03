"""Compile explicit editorial briefs into paired, independently labelled fixtures."""
import json
from datetime import date,datetime,timedelta
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent))
from seeds import STORIES
from wemeet_ai.scenarios import Scenario, DOMAINS, audit_scenarios

NAMES=['지우','도윤','서연','민재','수빈','예준','하은','지호','유나','현우','다은','시우','채원','건우','소윤']
MODULES=['A','B','C','E','PLAN']*2
CHANGES=['scope','deadline','verification','availability','availability','scope','frequency','privacy','level','deadline']
TAGS=[['missing_details','scope_creep'],['date_conflict','ambiguous_agreement'],['checkin_object_regression','edit_preservation'],['private_intake','time_constraints'],['overcommitment','beginner_expert'],['rejected_proposal','alternative_request'],['short_conversation','short_agreement'],['privacy','optional_metric'],['incomplete_work','tools_and_help'],['versioning','week_coverage','restart_restore']]

def make(domain,di,index,variant):
    title,deliverable,workflow,complication,role,criterion=STORIES[domain][index]
    family=f'{domain}-{index+1:02}';case_id=family+('-v' if variant else '-b');pair=family+('-b' if variant else '-v');change=CHANGES[index]
    reference=date(2026,10,28)+timedelta(days=di*4+index);start=reference+timedelta(days=2);end=start+timedelta(days=[13,20,27,9][index%4])
    if variant and change=='deadline':end+=timedelta(days=7)
    count=[2,3,4,3,5,2,4,3,5,4][index];members=[{'member_id':f'{family}-m{m+1}','nickname':NAMES[(di+index+m)%len(NAMES)]} for m in range(count)]
    owner=members[0]['member_id'];owner_name=members[0]['nickname'];frequency=4 if variant and change=='frequency' else 2
    minutes=60 if variant and change=='availability' else [180,240,120,150][index%4]
    level='관련 결과물을 혼자 만들어 본 숙련자' if variant and change=='level' else '기초는 이해하지만 완성 경험은 적은 초보자'
    scoped=deliverable+' 중 핵심 부분 한 가지를 먼저 완성' if variant and change=='scope' else deliverable
    verification='짧은 텍스트와 체크 목록' if variant and change=='verification' else '결과 화면 또는 기록과 짧은 설명'
    privacy='PRIVATE' if variant and change=='privacy' else 'OBJECTIVE_ONLY'
    roles={m['member_id']:(role if n==0 else ['자료·근거 확인','결과 검증','진행 정리','일정 조율'][(n-1)%4]) for n,m in enumerate(members)}
    timestamp=datetime.combine(reference,datetime.min.time()).replace(hour=19).isoformat()+'+09:00'
    lines=[(0,f'{title}을 같이 해보자. 이번 결과물은 {scoped}로 생각하고 있어.'),
        (1,f'좋아. 그런데 {complication}. 이 부분을 계획에서 놓치지 않았으면 해.'),
        (0,f'현재 할 일은 {workflow} 순서야. 다 끝낸 것처럼 쓰지 말고 작업별로 나눠보자.'),
        (1,f'기간은 {start.isoformat()}부터 {end.isoformat()}까지로 잡으면 어때?'),
        (0,f'내 담당은 {role}이고, '+', '.join(m['nickname']+'은 '+roles[m['member_id']] for m in members[1:])+'를 맡는 안이야.'),
        (1,f'완료 기준은 "{criterion}"으로 보자. 시간만 채운 것을 완료라고 하지는 말자.'),
        (0,f'실행은 주 {frequency}회로 생각하고 있어. 인증 횟수와 실제 작업 횟수는 구분해줘.'),
        (1,f'인증 자료는 {verification}이면 좋겠어. 체크인은 매주 화요일에 하되 시간은 아직 안 정했어.'),
        (0,'공개 범위는 '+('본인만 확인하는 것으로 제안해.' if privacy=='PRIVATE' else '완료 여부와 달성률만 그룹에 공개하는 것으로 제안해.')),
        (1,'ㅇㅇ. 다만 지금 내 답변이 아직 참여하지 않은 사람의 최종 승인까지 뜻하는 건 아니야.'),
        (0,f'{complication}. 그래서 다음 행동을 작은 결과물 기준으로 추천해줘.'),
        (1,'가능한 방법 두 가지와 각각 포기해야 하는 것도 알려주면 선택하기 쉬울 것 같아.')]
    if index==0:lines[3]=(1,'정확한 일정과 세부 분량은 아직 정하지 않았어. 필요한 조건은 이유와 함께 제안해줘.')
    if index==1:lines.insert(5,(1,f'나는 {end+timedelta(days=7)}까지 필요할 것 같아. 처음 날짜와 다른 안이라 아직 합의된 건 아니야.'))
    if index==2:lines.extend([(0,'체크인을 표처럼 정리하더라도 최종 입력 형식에서 막히지 않았으면 해.'),(1,'작성한 기준을 수정하면 저장된 값과 다음 단계 요청이 같아야 해.')])
    if index==5:lines.extend([(0,'그 추천 범위는 지금 우리한테 너무 커. 다른 안을 제안해줘.'),(1,'보류한 안을 이미 동의한 것으로 다음 계획에 넣지는 말아줘.')])
    if index==6:lines=[lines[0],lines[3],(0,'주 '+str(frequency)+'회 정도?'),(1,'좋아. 그런데 인증 방식과 공개 범위는 조금 더 이야기하자.')]
    if index==7:lines.extend([(0,'개인 사유는 공유하지 않는 데 동의해. 결과물 목표라 꼭 수치 목표를 만들 필요는 없어.'),(1,'목표량이 없는 것을 정보 오류로 막지 말아줘.')])
    if index==9:lines.extend([(0,'중간 검토는 '+(start+timedelta(days=6)).isoformat()+', 최종 제출은 '+end.isoformat()+'야. 서로 다른 단계야.'),(1,'수정 전 계획의 평가 기록은 남기고, 새 계획은 새 버전으로 관리하자.')])
    conversation=[{'message_id':f'{case_id}-g{n:02}','speaker_id':members[speaker]['member_id'],'role':'user','text':text,'created_at':timestamp} for n,(speaker,text) in enumerate(lines)]
    profiles=[];private={}
    for n,m in enumerate(members):
        secret=f'{family} 개인 사유: '+['화요일 저녁에는 별도 일정이 있어 긴 작업을 할 수 없음','작업 장소의 소음 때문에 음성 참여 대신 글로 참여하고 싶음','개인 자료를 공개 저장소에 올릴 수 없음','이동이 필요한 작업은 이번 주에 참여하기 어려움'][n%4]
        available=minutes if n==0 else [90,240,300,120][(index+n)%4]
        current=level if n==0 else ['기초 실습을 해 본 중급자','비슷한 결과물을 완성해 본 경험자','이번 활동을 처음 시작하는 사람'][n%3]
        profile={'member_id':m['member_id'],'current_level':current,'weekly_available_minutes':available,'preferred_times':['수요일 저녁','토요일 오전'] if n%2==0 else ['주말 오후'],'constraints':[secret]}
        profiles.append(profile)
        texts=[f'내 역할은 {roles[m["member_id"]]}이야. {current}이고 내 조건에 맞게 구체적으로 정하고 싶어.',
            f'일주일에 {available}분 정도 가능해. '+('수요일 저녁과 토요일 오전에 나눌 수 있어.' if n%2==0 else '주말 오후에 몰아서 할 수 있어.'),
            '현재는 '+('자료를 모았지만 첫 결과물은 아직 없어.' if index%2==0 else '초안을 만들었고 검증과 수정이 남아 있어.'),
            f'내가 만들 범위는 {scoped if n==0 else roles[m["member_id"]]+" 결과 기록"}야. 다른 사람 역할까지 맡기지는 말아줘.',
            f'비공개로만 기억해줘. {secret}. 그룹에는 이 문장을 전달하지 않았으면 해.',
            '모르는 도구는 배울 시간이 필요해. 필요한 도구와 도움도 계획에 반영하고 가정은 구분해줘.']
        private[m['member_id']]=[{'message_id':f'{case_id}-p{n}-{t}','speaker_id':m['member_id'],'role':'user','text':text,'created_at':timestamp} for t,text in enumerate(texts)]
    criteria=[criterion,'자신의 담당 결과와 검증 근거를 기록']
    decisions={'confirmed':True,'goal':title,'expected_outcome':scoped,'completion_criteria':criteria,'start_date':start.isoformat(),'end_date':end.isoformat(),'frequency_per_week':frequency,'execution_method':workflow,'verification_method':verification,'privacy_scope':privacy,'roles':roles,'checkins':['화요일 · 진행 상황 확인'],'milestones':[{'title':'중간 결과 검토','due_date':(start+timedelta(days=6)).isoformat()},{'title':'최종 결과 확인','due_date':end.isoformat()}]}
    # Expected facts label only what is actually in the dialogue, not the authored fixture.
    expected={'goal':title,'deliverable':scoped,'workflow':workflow,'criterion':criterion,'frequency_per_week':frequency,'end_date':None if index==0 else end.isoformat(),
        'verification_method':None if index==6 else verification,'privacy_scope':None if index==6 else privacy,'timeline_conflicted':index==1,'requires_clarification':['start_date','end_date'] if index==0 else ['verification_method','privacy_scope'] if index==6 else [],
        'owner_available_minutes':minutes,'owner_level':level,'evidence_refs':[conversation[0]['message_id']],
        'required_behaviors':['목표와 역할을 실제 대화에서 파악','AI 제안과 사용자 확인을 구분','근거 메시지를 보존','담당 결과물·완료 기준 중심으로 실행안을 제시'],
        'forbidden':['그룹 전체 합의라고 임의 확정','비공개 사유를 그룹 응답에 복사','누락된 시간을 확정 체크인 시각으로 생성','검증 실패 결과를 완료로 표시'],
        'privacy_sentinels':[p['constraints'][0] for p in profiles]}
    if index==1:expected['required_behaviors'].append('두 마감 후보를 충돌로 보존하고 임의 선택하지 않음')
    if index==7:expected['required_behaviors'].append('수치 목표가 없어도 결과물 완료 기준으로 진행')
    comparison={'changed_fields':[change],'expected_change':{'scope':'담당 결과물과 작업 범위를 줄여야 함','deadline':'전체 기간과 마일스톤을 새 마감일 안으로 조정','verification':'인증 방식만 바꾸고 목표·기간은 보존','availability':'개인 작업 분량·시간 배분을 줄여야 함','frequency':'실행 날짜 수와 주간 빈도를 조정','privacy':'그룹 공개 정보를 개인 전용으로 바꿈','level':'설명·난이도·준비 작업을 숙련도에 맞게 조정'}[change],
        'invariants':['공동 목표의 주제','다른 사람의 비공개 조건 비노출','근거 없는 벌점·승인 생성 금지'],'variant_value':{'scope':scoped,'deadline':end.isoformat(),'verification':verification,'availability':minutes,'frequency':frequency,'privacy':privacy,'level':level}[change]}
    reference_answer={'label':'작성된 기준안 · LIVE 응답 아님','coaching_example':f'{complication}을 먼저 분리해서 다루는 것이 좋겠습니다. {workflow} 중 첫 결과물부터 작게 확인하는 안을 추천합니다. 완료는 "{criterion}"으로 확인하고, 확정하지 않은 조건은 검토 카드에 남기겠습니다.',
        'personal_example':f'{owner_name}님의 {role} 범위에서 현재 수준과 주 {minutes}분을 기준으로 작업을 나눕니다. 첫 주에는 준비·핵심 작업·검증 시간을 따로 배정하고, 다른 구성원의 일을 자동으로 추가하지 않습니다.',
        'expected_public_values':{k:expected[k] for k in ('goal','deliverable','frequency_per_week','end_date','privacy_scope')},'teaching_points':expected['required_behaviors']}
    split='REFERENCE' if index<6 else 'DEV' if index<8 else 'TEST'
    return Scenario(case_id=case_id,family_id=family,kind='VARIANT' if variant else 'BASE',paired_case_id=pair,split=split,domain=domain,
        title=title+(' · '+comparison['expected_change'] if variant else ''),summary=f'{DOMAINS[domain]} 팀이 {deliverable}를 준비한다. {complication}. {workflow}를 실제 담당과 시간에 맞춰 나눠야 한다.',
        purpose=f'{MODULES[index]} 단계에서 '+comparison['expected_change']+' 여부를 확인',difficulty=['BASIC','INTERMEDIATE','ADVANCED'][index%3],entry_module=MODULES[index],tags=TAGS[index]+([change] if variant else []),
        group_input={'conversation':conversation,'members':members,'reference_date':reference.isoformat(),'timezone':'Asia/Seoul'},private_conversations=private,profiles=profiles,confirmed_fixture=decisions,expected=expected,comparison=comparison,reference_answer=reference_answer)

if __name__=='__main__':
    cases=[make(domain,di,i,variant) for di,domain in enumerate(STORIES) for i in range(10) for variant in (False,True)]
    audit=audit_scenarios(cases);assert audit['passed'],audit
    destination=Path(__file__).parents[1]/'src'/'wemeet_ai'/'data'/'scenarios.v1.jsonl';destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text('\n'.join(c.model_dump_json() for c in cases)+'\n',encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False))
