"""Reference selection is deterministic and only consumes an approved pool."""
import json
import re
from .scenarios import DATA_VERSION

TASK_MODULE={'coach-proactive':'A','analyze':'B','draft':'C','member-goal-coach':'E','member-goal-draft':'E','group-plan':'PLAN','member-plan':'PLAN'}
TASK_MODULE['coach-dialogue']='A'

def select_references(pool,task,payload,excluded_family=None):
    module=TASK_MODULE.get(task);query=json.dumps(payload,ensure_ascii=False)
    scored=[]
    for case in pool:
        if case.split!='REFERENCE' or case.entry_module!=module or case.family_id==excluded_family:continue
        words=set(re.findall(r'[가-힣A-Za-z]{2,}',case.title+' '+case.summary))
        score=sum(word in query for word in words)
        if score:scored.append((score,case.case_id,case))
    chosen=[];families=set()
    for _,_,case in sorted(scored,key=lambda item:(-item[0],item[1])):
        if case.family_id in families:continue
        families.add(case.family_id)
        example=case.reference_answer['personal_example' if module=='E' else 'coaching_example']
        for member in case.group_input.members:example=example.replace(member.nickname,'예시 구성원')
        example=re.sub(r'\d{4}-\d{2}-\d{2}','[예시 날짜]',example)
        # Facts and answer keys are intentionally not supplied as teaching examples.
        chosen.append({'case_id':case.case_id,'data_version':DATA_VERSION,'source':'HUMAN_APPROVED_AUTHORED_EXAMPLE','context':case.domain+' / '+module,
            'example_response':example[:900],'principles':case.reference_answer['teaching_points'][:4]})
        if len(chosen)==2:break
    return chosen
