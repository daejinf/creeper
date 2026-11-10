import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from _data import sample, plan, MEMBERS, CONTEXT
from _fixture import FixtureClient
from wemeet_ai.cli import execute
from wemeet_ai.m0 import Runtime, MemoryResultCache
from wemeet_ai.m1.a_coaching import CoachRequest, coach_reply
from wemeet_ai.m1.b_analysis import AnalysisRequest, analyze_conversation
from wemeet_ai.m1.c_goal_draft import DraftRequest, GoalDraft, draft_goal_spec, review_field, confirm_goal
from wemeet_ai.m1.e_member_plan import MemberCoachRequest, ButtonAnswer, MemberPlanRequest, coach_member_goal, generate_member_plan
from wemeet_ai.m1.f_validation import ValidationRequest, validate_plan
from wemeet_ai.shared.contracts import Member, Message, ReviewField

@pytest.mark.parametrize("module", ["A","B","C","D","E.coach","E.draft","E.plan","F"])
def test_each_module_independent(module):
    result=execute(module,sample(module),runtime=Runtime(FixtureClient()))
    assert result.status=="OK",result.model_dump()

@pytest.mark.parametrize("module", ["a_coaching","b_analysis","c_goal_draft","d_group_plan","e_member_plan","f_validation"])
def test_fresh_process_without_framework_imports(module):
    code=f"import sys; import wemeet_ai.m1.{module}; assert not any(x in sys.modules for x in ['fastapi','uvicorn','sqlite3']); print('OK')"
    completed=subprocess.run([sys.executable,"-I","-c",code],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr

def test_missing_data_lists_fields_no_model():
    payload=sample("D");payload.update(start_date=None,end_date=None,confirmed=False,members=[])
    client=FixtureClient();result=execute("D",payload,runtime=Runtime(client))
    assert result.status=="NEEDS_INPUT" and len(result.needed_inputs)>=4 and not client.calls
    assert not any("먼저" in item for item in result.needed_inputs)

def test_A_does_not_create_other_stage_data():
    client=FixtureClient();result=coach_reply(CoachRequest.model_validate(sample("A")),runtime=Runtime(client))
    assert list(result.data.model_dump())==["reply"] and len(client.calls)==1


def test_empty_private_history_keeps_question_without_invented_profile():
    from wemeet_ai.shared.contracts import Evidence
    client=FixtureClient();original=client.complete_json
    def complete(*args,**kwargs):
        result=original(*args,**kwargs)
        result.parsed_output.extracted_fields['current_level']=ReviewField(
            value='experienced',origin='EXTRACTED',evidence=[Evidence(source='USER_MESSAGE',source_id='nonexistent')])
        result.parsed_output.answer_states['current_level']='KNOWN'
        return result
    client.complete_json=complete
    result=coach_member_goal(MemberCoachRequest.model_validate(sample('E.coach')),runtime=Runtime(client))
    assert result.status=='OK' and len(client.calls)==1
    assert result.data.state.fields=={} and result.data.state.answer_states=={}
    assert result.data.state.question.field=='current_level'

def test_C_external_facts_no_B():
    result=draft_goal_spec(DraftRequest.model_validate(sample("C")),runtime=Runtime(FixtureClient()))
    assert result.data.fields["start_date"].value=="2026-12-01"
    assert result.data.fields["start_date"].review_status=="PENDING"

def test_C_missing_opens_and_no_fake_goal():
    result=draft_goal_spec(DraftRequest())
    assert result.status=="NEEDS_INPUT" and result.data.fields["goal"].value is None

def test_new_deadline_invalidates_old_acceptance():
    payload=sample("C")
    previous=GoalDraft.model_validate({"fields":payload["facts"]})
    for f in previous.fields.values(): f.review_status="ACCEPTED"
    payload["facts"]["end_date"]["value"]="2026-12-28";payload["previous"]=previous.model_dump(mode="json")
    result=draft_goal_spec(DraftRequest.model_validate(payload),runtime=Runtime(FixtureClient()))
    assert result.data.fields["end_date"].review_status=="REVIEW_REQUIRED"
    confirmed=confirm_goal(result.data,members=[Member.model_validate(m) for m in MEMBERS])
    assert confirmed.status=="NEEDS_INPUT" and "end_date" in confirmed.needed_inputs

def test_review_edit_invalid_preserves_and_confirm():
    draft=GoalDraft.model_validate({"fields":sample("C")["facts"]})
    invalid=review_field(draft,"start_date",value="bad",edit=True,actor_id="tester")
    assert invalid.status=="NEEDS_INPUT" and draft.fields["start_date"].value=="2026-12-01"
    for key in draft.fields: draft=review_field(draft,key,actor_id="tester").data
    result=confirm_goal(draft,members=[Member.model_validate(m) for m in MEMBERS])
    assert result.status=="OK"
    assert confirm_goal(draft,members=[Member.model_validate(m) for m in MEMBERS]).data==result.data

def test_alternative_replaces_only_requested_field_pending():
    payload=sample("C");old=GoalDraft.model_validate({"fields":payload["facts"]})
    for f in old.fields.values(): f.review_status="ACCEPTED"
    payload.update(previous=old.model_dump(),mode="SUGGEST",target_fields=["end_date"])
    result=draft_goal_spec(DraftRequest.model_validate(payload),runtime=Runtime(FixtureClient()))
    assert result.data.fields["end_date"].review_status=="PENDING"
    assert result.data.fields["goal"].review_status=="ACCEPTED"

def test_E_continues_past_two_questions_and_duplicate_answer():
    runtime=Runtime(FixtureClient());request=MemberCoachRequest.model_validate(sample("E.coach"))
    result=coach_member_goal(request,runtime=runtime)
    for i in range(4):
        q=result.data.state.question
        assert q is not None
        message=Message(message_id=f"p{i}",speaker_id="u1",role="user",text=q.options[0].label,created_at="2026-11-20T10:00:00+09:00")
        answer=ButtonAnswer(question_id=q.question_id,option_id=q.options[0].option_id,message_id=message.message_id)
        request=request.model_copy(update={"state":result.data.state,"conversation":[*request.conversation,message],"answer":answer})
        result=coach_member_goal(request,runtime=runtime)
        assert result.status=="OK"
        if i==1:
            assert result.data.summary_checkpoint and result.data.state.question.field=="current_progress"
    assert result.data.state.question is None
    before=len(runtime.client.calls)
    duplicate=coach_member_goal(request.model_copy(update={"state":result.data.state}),runtime=runtime)
    assert duplicate.meta.execution_source=="CODE" and len(runtime.client.calls)==before

def test_E_unknown_not_zero_or_none_constraint():
    runtime=Runtime(FixtureClient());request=MemberCoachRequest.model_validate(sample("E.coach"));out=coach_member_goal(request,runtime=runtime)
    q=out.data.state.question
    message=Message(message_id="p1",speaker_id="u1",text="모르겠어요",created_at="2026-11-20T10:00:00+09:00")
    req=request.model_copy(update={"state":out.data.state,"conversation":[message],"answer":ButtonAnswer(question_id=q.question_id,option_id="unknown",message_id="p1")})
    out=coach_member_goal(req,runtime=runtime)
    assert out.data.state.answer_states["current_level"]=="UNKNOWN"
    assert out.data.state.fields["current_level"].value is None
    assert out.data.state.question.field != "current_level"

def test_E_other_member_input_rejected():
    payload=sample("E.coach");payload["conversation"]=[{"message_id":"private","role":"user","speaker_id":"u2","text":"PRIVATE","created_at":"2026-11-20T10:00:00+09:00"}]
    with pytest.raises(ValidationError): MemberCoachRequest.model_validate(payload)

def test_private_reason_not_in_public_model_request_or_response():
    secret="매주 화요일 가족 돌봄으로 참여 불가"
    payload=sample("E.plan");payload["profile"].update(constraints=[secret],private_notes=["합성 비공개 사유"])
    runtime=Runtime(FixtureClient())
    out=generate_member_plan(MemberPlanRequest.model_validate(payload),runtime=runtime)
    assert out.status=="OK"
    assert secret not in json.dumps(runtime.client.calls,ensure_ascii=False)
    assert secret not in out.model_dump_json()
    assert "private_notes" not in out.data.model_dump()

def test_plan_cache_preserves_version_and_owner_scope():
    runtime=Runtime(FixtureClient(),cache=MemoryResultCache(),scope_id="work")
    one=execute("E.plan",sample("E.plan"),runtime=runtime)
    two=execute("E.plan",sample("E.plan"),runtime=runtime)
    assert two.meta.cache_hit and one.data.plan_version_id==two.data.plan_version_id
    other=sample("E.plan");other["owner_member_id"]="u2"
    three=execute("E.plan",other,runtime=runtime)
    assert not three.meta.cache_hit and three.data.owner_member_id=="u2"

@pytest.mark.parametrize("failure,code", [("date","DATE_OUTSIDE_PERIOD"),("budget","TIME_BUDGET_EXCEEDED"),("owner","OWNER_MISMATCH")])
def test_F_rejects_external_bad_plan(failure,code):
    value=plan("u1");budgets={"u1":30}
    if failure=="date": value["tasks"][0]["due_date"]="2027-01-01"
    if failure=="budget": value["tasks"][0]["estimated_minutes"]=120
    if failure=="owner": value["tasks"][0]["owner_member_id"]="u2"
    out=validate_plan(ValidationRequest(scope="MEMBER",member_plans=[value],context=CONTEXT,weekly_budgets=budgets))
    assert not out.data.valid and code in [i.code for i in out.data.issues]

def test_F_partial_scope_not_claim_complete():
    out=execute("F",sample("F"))
    assert out.status=="OK" and out.data.valid and not out.data.integration_ready
    assert "전체 구성원 계획 완성 여부" in out.data.not_checked

def test_cannot_accept_unreviewed_draft_or_unknown_time():
    draft=GoalDraft.model_validate({"fields":sample("C")["facts"]})
    assert confirm_goal(draft,members=[Member.model_validate(m) for m in MEMBERS]).status=="NEEDS_INPUT"
    payload=sample("E.plan");payload["profile"]["weekly_available_minutes"]=None
    assert execute("E.plan",payload).status=="NEEDS_INPUT"

def test_private_rationale_not_a_public_plan_field():
    payload=plan();payload['public_rationale']='화요일마다 가족을 돌보느라 시간을 낼 수 없어 분량을 줄였습니다.'
    from wemeet_ai.shared.contracts import Plan
    with pytest.raises(ValidationError): Plan.model_validate(payload)

def test_condition_edit_is_pure_code_and_preserves_other_fields():
    from wemeet_ai.m1.e_member_plan import MemberState, update_member_condition
    state=MemberState(owner_member_id='u1',fields={'current_level':ReviewField(value='일부 경험')})
    result=update_member_condition(state,'weekly_available_minutes',value=60,actor_id='user-checked')
    assert result.status=='OK' and result.meta.execution_source=='CODE'
    assert 'weekly_available_minutes' not in state.fields
    assert result.data.fields['current_level'].value=='일부 경험'

def test_dependency_cycle_detected():
    p=plan();p['tasks'].append(deepcopy(p['tasks'][0]));p['tasks'][1]['task_id']='second'
    p['tasks'][0]['dependencies']=['second'];p['tasks'][1]['dependencies']=['external-task-1']
    out=validate_plan(ValidationRequest(scope='GROUP',group_plan=p))
    assert not out.data.valid and 'DEPENDENCY_CYCLE' in [x.code for x in out.data.issues]

def test_optional_pending_milestone_does_not_block_confirmation():
    d=GoalDraft.model_validate({'fields':sample('C')['facts']})
    for key in d.fields:d=review_field(d,key,actor_id='reviewer').data
    d.fields['milestones']=ReviewField(value=['작업 순서 메모'],review_status='PENDING')
    out=confirm_goal(d,members=[Member.model_validate(x) for x in MEMBERS])
    assert out.status=='OK' and out.data.milestones==[]

def test_milestone_string_normalization_and_roles_preserved():
    d=GoalDraft.model_validate({'fields':sample('C')['facts']})
    for key in d.fields:d=review_field(d,key,actor_id='reviewer').data
    d=review_field(d,'milestones',value=['초안 검토'],edit=True,actor_id='reviewer').data
    d=review_field(d,'roles',value={'u1':'참여 설계'},edit=True,actor_id='reviewer').data
    out=confirm_goal(d,members=[Member.model_validate(x) for x in MEMBERS])
    assert out.status=='OK' and out.data.milestones==[{'title':'초안 검토'}]
    assert out.data.roles=={'u1':'참여 설계'}

def test_wrong_weekday_removed_without_changing_date():
    from datetime import date
    from wemeet_ai.shared.calendar import remove_wrong_weekdays
    text,changes=remove_wrong_weekdays('2026-11-12(수)와 2026-11-13(금)',date(2026,11,3))
    assert changes and text=='2026-11-12와 2026-11-13(금)'

def test_unknown_privacy_object_is_inline_error():
    d=GoalDraft(fields={'privacy_scope':ReviewField(value={'scope':'unknown'})})
    out=review_field(d,'privacy_scope',actor_id='reviewer')
    assert out.status=='NEEDS_INPUT'

def test_live_discovered_invalid_coaching_notes_do_not_destroy_facts():
    client=FixtureClient()
    original=client.complete_json
    def complete(*args,**kwargs):
        out=original(*args,**kwargs)
        from wemeet_ai.m1.b_analysis import CoachingNote
        out.parsed_output.coaching_notes=[CoachingNote(ai_message_id='m1',proposal='quoted AI',status='UNSELECTED')]
        return out
    client.complete_json=complete
    out=analyze_conversation(AnalysisRequest.model_validate(sample('B')),runtime=Runtime(client))
    assert out.status=='OK' and out.data.facts and not out.data.coaching_notes
    assert out.data.warnings

def test_live_discovered_repeated_question_keeps_extracted_facts():
    client=FixtureClient();original=client.complete_json
    def complete(*args,**kwargs):
        out=original(*args,**kwargs)
        from wemeet_ai.shared.contracts import Evidence
        out.parsed_output.extracted_fields['current_level']=ReviewField(value='일부 경험',origin='EXTRACTED',evidence=[Evidence(source='USER_MESSAGE',source_id='p0')])
        return out
    client.complete_json=complete
    req=sample('E.coach');req['conversation']=[{'message_id':'p0','speaker_id':'u1','role':'user','text':'일부 경험이 있어요','created_at':'2026-11-20T10:00:00+09:00'}]
    out=coach_member_goal(MemberCoachRequest.model_validate(req),runtime=Runtime(client))
    assert out.status=='OK' and out.data.state.question is None
    assert out.data.state.fields['current_level'].value=='일부 경험'

def test_A_week_range_check_repairs_once():
    from test_m0 import Session, Response
    from wemeet_ai.m0 import TimelyClient
    import os
    old=os.environ.get('TIMELY_API_KEY');os.environ['TIMELY_API_KEY']='sk-SYNTHETIC_ONLY_NOT_REAL_123456'
    try:
        session=Session([Response(content='4주 정리'),Response(content='3주 정리')])
        out=coach_reply(CoachRequest.model_validate(sample('A')),runtime=Runtime(TimelyClient(session)))
        assert out.status=='OK' and out.data.reply=='3주 정리' and len(session.calls)==2
    finally:
        if old is None:os.environ.pop('TIMELY_API_KEY',None)
        else:os.environ['TIMELY_API_KEY']=old

def test_F_weekly_outline_outside_range_rejected():
    p=plan();p['weekly_outcomes']=['one','two','three','four']
    out=validate_plan(ValidationRequest(scope='GROUP',group_plan=p))
    assert not out.data.valid and 'WEEK_OUTSIDE_PERIOD' in [x.code for x in out.data.issues]

def test_invalid_milestone_edit_preserves_old_value():
    d=GoalDraft(fields={'milestones':ReviewField(value=[{'title':'검토','due_date':'2026-12-10'}])})
    out=review_field(d,'milestones',value=[{'title':'검토','due_date':'invalid'}],edit=True,actor_id='reviewer')
    assert out.status=='NEEDS_INPUT' and d.fields['milestones'].value[0]['due_date']=='2026-12-10'

def test_plan_ids_keep_dependency_links():
    from wemeet_ai.shared.contracts import Plan
    from wemeet_ai.shared.plans import assign_ids
    p=plan();second=deepcopy(p['tasks'][0]);second.update(task_id='second',dependencies=['external-task-1']);p['tasks'].append(second)
    typed=Plan.model_validate(p);assign_ids(typed)
    assert typed.tasks[1].dependencies==[typed.tasks[0].task_id]
    assert typed.tasks[0].task_id!='external-task-1'

def test_known_milestone_alias_checked_not_ignored():
    from wemeet_ai.shared.contracts import Plan
    p=plan();p['milestones']=[{'title':'검토','target_date':'2027-01-01','outcome':'초안 확인'}]
    value=Plan.model_validate(p)
    out=validate_plan(ValidationRequest(scope='GROUP',group_plan=value))
    assert not out.data.valid and any(x.code=='INVALID_MILESTONE' for x in out.data.issues)

def test_null_conflict_cannot_reuse_previous_acceptance():
    payload=sample('C');old=GoalDraft.model_validate({'fields':payload['facts']})
    for f in old.fields.values():f.review_status='ACCEPTED'
    payload.update(previous=old.model_dump(),conflicted_fields=['end_date'])
    payload['facts']['end_date'].update(value=None,conflicted=True)
    out=draft_goal_spec(DraftRequest.model_validate(payload),runtime=Runtime(FixtureClient()))
    assert out.data.fields['end_date'].review_status=='REVIEW_REQUIRED'
    assert confirm_goal(out.data,members=[Member.model_validate(x) for x in MEMBERS]).status=='NEEDS_INPUT'

def test_unanswered_field_is_not_a_user_unknown_choice():
    from wemeet_ai.shared.contracts import Evidence
    client=FixtureClient();complete=client.complete_json
    def response(*args,**kwargs):
        out=complete(*args,**kwargs)
        out.parsed_output.extracted_fields['scope']=ReviewField(value=None,evidence=[Evidence(source='USER_MESSAGE',source_id='p1')])
        out.parsed_output.answer_states['scope']='UNKNOWN'
        return out
    client.complete_json=response
    payload=sample('E.coach');payload['conversation']=[{'message_id':'p1','speaker_id':'u1','role':'user','text':'이번 주 30분 가능해요.','created_at':'2026-11-20T10:00:00+09:00'}]
    result=coach_member_goal(MemberCoachRequest.model_validate(payload),runtime=Runtime(client))
    assert result.status=='OK' and 'scope' not in result.data.state.answer_states
    assert result.warnings


def test_B_added_AI_criterion_is_not_extracted_user_fact():
    from wemeet_ai.m1.b_analysis import analyze_conversation,AnalysisRequest
    from examples._data import sample
    from examples._fixture import FixtureClient
    from wemeet_ai.m0 import Runtime
    class AddedCriterion(FixtureClient):
        def complete_json(self,messages,output_schema,options=None,**kwargs):
            result=super().complete_json(messages,output_schema,options,**kwargs)
            result.parsed_output.facts['completion_criteria'].value=['참여 방식 선택 가능 + 일정표 추가']
            return result
    out=analyze_conversation(AnalysisRequest.model_validate(sample('B')),runtime=Runtime(AddedCriterion()))
    assert out.status=='OK'
    assert out.data.facts['completion_criteria'].origin=='AI_SUGGESTED'


def test_C_partial_AI_response_keeps_all_existing_facts():
    from wemeet_ai.m1.c_goal_draft import DraftRequest,draft_goal_spec
    from examples._data import sample
    from examples._fixture import FixtureClient
    from wemeet_ai.m0 import Runtime
    class Partial(FixtureClient):
        def complete_json(self,messages,output_schema,options=None,**kwargs):
            value=super().complete_json(messages,output_schema,options,**kwargs)
            value.parsed_output.fields={}
            return value
    req=DraftRequest.model_validate(sample('C'))
    out=draft_goal_spec(req,runtime=Runtime(Partial()))
    assert out.status=='OK'
    for key,field in req.facts.items():assert out.data.fields[key].value==field.value


def test_live_role_array_can_feed_C_without_schema_deadlock():
    from wemeet_ai.shared.normalization import normalize_fact
    payload=sample('C');payload['facts']['roles']={'value':[{'member_id':'u1','role':'UI'},{'member_id':'u2','role':'API'}],'origin':'EXTRACTED','evidence':[{'source':'SW_CONFIRMED','source_id':'roles-review'}]}
    result=draft_goal_spec(DraftRequest.model_validate(payload),runtime=Runtime(FixtureClient()))
    assert result.status=='OK'
    assert result.data.fields['roles'].value=={'u1':'UI','u2':'API'}
    conflict=[{'member_id':'u1','role':'UI'},{'member_id':'u1','role':'API'}]
    assert normalize_fact('roles',conflict)==conflict


def test_calendar_removes_wrong_Korean_date_labels():
    from datetime import date
    from wemeet_ai.shared.calendar import remove_wrong_weekdays
    text,removed=remove_wrong_weekdays('11월 9일(일): 인증. 2026년 11월 8일(일)은 맞음.',date(2026,11,4))
    assert '9일(일)' not in text and '8일(일)' in text and len(removed)==1


def test_personal_prose_only_understanding_must_not_silently_pass():
    req=MemberCoachRequest.model_validate(sample('E.coach'))
    req.conversation=[Message(message_id='free',speaker_id='u1',text='피그마 경험은 조금 있고 개발 협업은 처음이야. 주당120분 가능해.',created_at='2026-11-04T10:00:00+09:00')]
    result=coach_member_goal(req,runtime=Runtime(FixtureClient()))
    assert result.status=='FAILED' and result.error.code=='MISSING_EXTRACTED_FACT'


def test_explicit_weekly_time_is_saved_and_known_level_not_reasked():
    class WithLevel(FixtureClient):
        def complete_json(self,messages,output_schema,options=None,**kw):
            out=super().complete_json(messages,output_schema,options,**kw)
            out.parsed_output.extracted_fields={'current_level':ReviewField(value='협업 초보',origin='EXTRACTED',evidence=[{'source':'USER_MESSAGE','source_id':'free'}])}
            return out
    req=MemberCoachRequest.model_validate(sample('E.coach'))
    req.conversation=[Message(message_id='free',speaker_id='u1',text='개발 협업은 처음이고 주당120분 가능해.',created_at='2026-11-04T10:00:00+09:00')]
    client=WithLevel();result=coach_member_goal(req,runtime=Runtime(client))
    assert result.status=='OK' and len(client.calls)==1
    assert result.data.state.fields['weekly_available_minutes'].value==120
    assert result.data.state.fields['current_level'].value=='협업 초보'
    assert result.data.state.question is None or result.data.state.question.field!='current_level'


def test_single_checkin_object_preserves_meaning_and_bad_optional_card_is_local():
    payload=sample('C');payload['facts']['checkins']={'value':{'day':'일요일','content':'시연 영상과 짧은 설명'},'origin':'EXTRACTED','evidence':[{'source':'SW_CONFIRMED','source_id':'review'}]}
    out=draft_goal_spec(DraftRequest.model_validate(payload),runtime=Runtime(FixtureClient()))
    assert out.status=='OK' and out.data.fields['checkins'].value==['일요일 · 시연 영상과 짧은 설명']
    payload['facts']['checkins']['value']={'unsupported':123}
    out=draft_goal_spec(DraftRequest.model_validate(payload),runtime=Runtime(FixtureClient()))
    assert out.status=='OK' and out.data.fields['checkins'].origin=='UNRESOLVED'
    assert out.data.fields['checkins'].previous_value==[{'unsupported':123}]
    assert out.data.fields['goal'].value==payload['facts']['goal']['value']


def test_unknown_refinement_does_not_erase_known_scope():
    from wemeet_ai.m1.e_member_plan import MemberState,Question,AnswerOption
    req=MemberCoachRequest.model_validate(sample('E.coach'))
    req.state=MemberState(owner_member_id='u1',fields={'scope':ReviewField(value='신청 폼과 관리자 목록 두 화면')},answer_states={'scope':'KNOWN'},question=Question(question_id='detail',field='scope',text='어느 수준의 결과물인가요?',reason='분량 조정',topic_id='deliverable_level',refinement_reason='세부 결과물 수준',options=[AnswerOption(option_id='unknown',label='모르겠어요',action='UNKNOWN')]))
    # Let the first call initialize the context hash; do not test context-change reset here.
    import hashlib,json
    req.state.context_key=hashlib.sha256(req.context.model_dump_json().encode()).hexdigest()
    req.conversation=[Message(message_id='detail-answer',speaker_id='u1',text='모르겠어요',created_at='2026-11-04T10:00:00+09:00')]
    req.answer=ButtonAnswer(question_id='detail',option_id='unknown',message_id='detail-answer')
    out=coach_member_goal(req,runtime=Runtime(FixtureClient()))
    assert out.status=='OK'
    assert out.data.state.fields['scope'].value=='신청 폼과 관리자 목록 두 화면'
    assert out.data.state.answer_states['scope']=='KNOWN'
    assert out.data.state.refinement_states['deliverable_level']=='UNKNOWN'


def test_unresolved_placeholder_is_not_a_fact_value():
    class Placeholder(FixtureClient):
        def complete_json(self,messages,output_schema,options=None,**kw):
            out=super().complete_json(messages,output_schema,options,**kw)
            out.parsed_output.facts['roles']=ReviewField(value={'u1':'담당 역할 미정'},origin='UNRESOLVED')
            return out
    result=analyze_conversation(AnalysisRequest.model_validate(sample('B')),runtime=Runtime(Placeholder()))
    assert result.status=='OK' and result.data.facts['roles'].value is None
    assert result.data.facts['roles'].previous_value=={'u1':'담당 역할 미정'}
