"""Public evidence projection; does not call AI or create facts absent from the analysis."""
from .contracts import ConfirmedDecisions,DraftField
from .draft_validation import normalize_decision_field,normalize_checkins

ALIASES={
 'goal':{'goal':['goal','title','name','description','objective','summary']},
 'expected_outcome':{'goal':['expected_outcome','deliverable','outcome'],'success':['deliverable','outcome']},
 'completion_criteria':{'success':['completion_criteria','criteria','criterion','description']},
 'execution_method':{'goal':['execution_method','workflow','steps'],'success':['execution_method','workflow','steps']},
 'start_date':{'timeline':['start_date']},'end_date':{'timeline':['end_date']},
 'frequency_per_week':{'frequency':['per_week']},'verification_method':{'verification':['method']},
 'privacy_scope':{'privacy':['scope']},'checkins':{'checkin':['schedule','schedule_text','text']},
}


def source_catalog(analysis):
    result={k:[] for k in ConfirmedDecisions.model_fields if k!='confirmed'}
    def add(key,value,refs,origin='EXTRACTED',reason='사용자 대화에서 확인된 값입니다.'):
        if value is None:return
        try:value=normalize_decision_field(key,value,[m.member_id for m in analysis.members])
        except (ValueError,TypeError):return
        if value is None:return
        found=next((o for o in result[key] if o['value']==value and o['origin']==origin),None)
        if found:found['evidence_refs']=sorted(set(found['evidence_refs']+refs))
        else:result[key].append({'value':value,'origin':origin,'evidence_refs':list(refs),'reason':reason})
    for key,dimensions in ALIASES.items():
        for dimension,names in dimensions.items():
            field=analysis.fields.get(dimension)
            if not field or field.status=='CONFLICTED':continue
            candidates=field.candidates
            if key=='goal':
                explicit=[c for c in candidates if any(c.value.get(n) for n in ('goal','title','name','description','objective'))]
                if explicit:candidates=explicit
            for c in candidates:
                if c.origin!='HUMAN':continue
                for name in names:
                    if name not in c.value:continue
                    value=c.value[name]
                    if key in {'completion_criteria','checkins'} and isinstance(value,str):value=[value]
                    if key=='execution_method' and isinstance(value,list) and all(isinstance(v,str) for v in value):value=' → '.join(value)
                    add(key,value,c.evidence_refs,'AI_SUGGESTED' if c.inferred else 'EXTRACTED','연도 등 정규화 추론이 포함되어 검토가 필요합니다.' if c.inferred else '사용자 대화에서 확인된 값입니다.')
                    break
    roles={};role_refs=[];conflict=False
    for c in getattr(analysis.fields.get('roles'),'candidates',[]):
        if c.origin!='HUMAN':continue
        mid=c.value.get('member_id');role=c.value.get('responsibility')
        if mid and isinstance(role,str):
            conflict|=mid in roles and roles[mid]!=role;roles[mid]=role;role_refs.extend(c.evidence_refs)
    if roles and not conflict:add('roles',roles,list(dict.fromkeys(role_refs)))
    milestones=[];milestone_refs=[];seen={};conflict=False
    for c in getattr(analysis.fields.get('milestones'),'candidates',[]):
        if c.origin!='HUMAN' or not c.value.get('title') or not c.value.get('due_date'):continue
        value={k:c.value[k] for k in ('title','due_date','owner_member_id') if k in c.value}
        identity=(value['title'],value.get('owner_member_id'));conflict|=identity in seen and seen[identity]!=value['due_date'];seen[identity]=value['due_date']
        if value not in milestones:milestones.append(value)
        milestone_refs.extend(c.evidence_refs)
    if milestones and not conflict:add('milestones',milestones,list(dict.fromkeys(milestone_refs)))
    field=analysis.fields.get('checkin')
    if field and field.status!='CONFLICTED':
        for c in field.candidates:
            if c.origin=='HUMAN' and set(c.value)<={'day_of_week','weekday','day','time','method','frequency_per_week'}:
                try:add('checkins',normalize_checkins(c.value),c.evidence_refs)
                except ValueError:pass
    field=analysis.fields.get('success')
    if field and field.status!='CONFLICTED':
        for c in field.candidates:
            v=c.value
            if c.origin=='HUMAN' and (v.get('target') or v.get('target_value')) and v.get('unit') and v.get('allocation') in {'PER_MEMBER','GROUP_TOTAL'}:
                add('metric',{'name':v.get('name','목표량'),'target':v.get('target',v.get('target_value')),'unit':v['unit'],'allocation':v['allocation']},c.evidence_refs)
    for note in analysis.coaching_notes:
        if note.disposition!='SUPPORTED' or not note.user_response_refs:continue
        for key,value in note.values.items():
            if key in result:add(key,value,note.user_response_refs,'AI_SUGGESTED','AI 제안에 대한 사용자 선택이 기록됐습니다. 그룹 전체 합의가 아니며 최종 검토가 필요합니다. AI 메시지: '+note.assistant_message_id)
    return result


def constrain_grounded(draft,analysis):
    catalog=source_catalog(analysis)
    for key,options in catalog.items():
        field=draft.fields.get(key)
        chosen=next((o for o in options if field and o['value']==field.value),None)
        if chosen is None and len(options)==1:chosen=options[0]
        if chosen:
            draft.fields[key]=DraftField(**chosen,review_status='PENDING')
        else:
            reason='대화에서 결정되지 않았습니다. A에서 논의하거나 이 항목의 AI 제안을 요청해주세요.' if not options else '서로 다른 후보가 있습니다. B의 근거를 확인하고 선택해주세요.'
            draft.fields[key]=DraftField(reason=reason)
    return draft


def constrain_assisted(draft, analysis, previous=None):
    """Keep unique grounded facts; new completion is always a pending recommendation."""
    catalog=source_catalog(analysis)
    reviewed_goal=previous.fields.get('goal') if previous else None
    has_goal=bool(catalog['goal']) or bool(reviewed_goal and reviewed_goal.review_status in {'ACCEPTED','EDITED'} and reviewed_goal.value)
    for key,options in catalog.items():
        field=draft.fields.get(key,DraftField())
        if len(options)==1:
            draft.fields[key]=DraftField(**options[0],review_status='PENDING')
        elif field.value is not None and has_goal:
            field.origin='AI_SUGGESTED';field.review_status='PENDING';field.evidence_refs=[]
            field.reason=('대화의 후보 중 추천하는 안입니다. 다른 후보는 B에서 확인할 수 있습니다. ' if len(options)>1 else '대화에서 정하지 않아 AI가 추천한 내용입니다. ')+field.reason
            draft.fields[key]=field
        else:
            draft.fields[key]=DraftField(reason='어떤 목표를 함께 달성할지 이 화면에서 알려주세요.' if not has_goal else '이 항목이 필요하면 직접 입력하거나 AI 추천을 요청할 수 있습니다.')
    return draft
