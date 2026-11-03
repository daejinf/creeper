import json

import pytest

from test_core import client, Response, fake_key, group_fixture, member_fixture
from test_personal import request as personal_request, model as personal_model
from wemeet_ai import GoalAI
from wemeet_ai.baseline import BaselineRequest, ActivityReference, build_baseline, validate_activity_reference
from wemeet_ai.contracts import CoachRequest, ProposalReviewRequest, Member, IntakeDimension, PersonalQuestion, AnswerOption
from wemeet_ai.examples import regression_conversation, example_input
from wemeet_ai.adapters.repository import SQLiteRepository, RevisionConflict
from wemeet_ai.adapters.telemetry import PersistentTraceSink, provider_identity


def proactive_output():
    return {'reply':'프로젝트 결과물을 먼저 작게 나누는 것을 추천합니다.','observations':['제출할 결과물이 필요합니다.'],
        'missing_or_conflicting':['완료 기준을 확인해야 합니다.'],'proposals':[{'title':'작동하는 데모로 확인','rationale':'실제 동작으로 완료 여부를 확인할 수 있습니다.',
        'field_updates':{'completion_criteria':['핵심 흐름 한 번 시연']},'evidence_refs':['m01']}],'next_actions':['완료 기준을 선택해주세요.']}


def test_proactive_coaching_produces_unapproved_typed_recommendation():
    request=CoachRequest(**regression_conversation().model_dump(),coaching_mode='PROACTIVE')
    output=proactive_output();output['proposals'][0]['evidence_refs']=[request.conversation[0].message_id]
    c=client(Response(json.dumps(output,ensure_ascii=False)))
    result=GoalAI(c).coach_reply(request)
    assert result.status=='OK',result.model_dump_json()
    assert result.data.proposals[0].status=='PENDING'
    assert result.data.proposals[0].proposal_id
    review=ProposalReviewRequest(proposal=result.data.proposals[0],decision='ACCEPTED',reviewer=request.members[0])
    accepted=GoalAI(client()).review_coach_proposal(review)
    repeated=GoalAI(client()).review_coach_proposal(review)
    assert accepted.data['event_id']==repeated.data['event_id']
    assert accepted.data['group_consensus'] is False


def dimensions():
    return [IntakeDimension(field_id=key,label=label,reason='공동 결과물과 개인 작업 범위를 구체화하기 위해 필요') for key,label in [('current_level','경험'),('weekly_available_minutes','시간'),('current_progress','진척'),('scope','담당 범위')]]


def test_adaptive_diagnosis_pauses_then_can_continue_beyond_two_questions():
    req=personal_request();req.diagnostic_mode='ADAPTIVE';req.state.group_plan_id=req.group_plan.plan_id;req.state.consecutive_questions=2
    summary=personal_model(False);summary.diagnostic_dimensions=dimensions()
    first=GoalAI(client(Response(summary.model_dump_json()))).coach_member_goal(req)
    assert first.status=='OK' and first.data.state.awaiting_continue
    req.state=first.data.state;req.continue_diagnosis=True
    next_model=personal_model(False);next_model.diagnostic_dimensions=dimensions()
    next_model.question=PersonalQuestion(target_field='current_progress',text='어디까지 진행했나요?',options=[AnswerOption(label='아직 시작 전',value='시작 전')])
    second=GoalAI(client(Response(next_model.model_dump_json()))).coach_member_goal(req)
    assert second.status=='OK',second.model_dump_json()
    assert second.data.state.current_question.target_field=='current_progress'
    assert len(second.data.state.diagnostic_dimensions)==4


def test_adaptive_dimensions_cannot_be_invented_ui_commands():
    req=personal_request();req.diagnostic_mode='ADAPTIVE'
    invalid=personal_model();invalid.diagnostic_dimensions=[]
    correct=personal_model();correct.diagnostic_dimensions=dimensions()
    result=GoalAI(client(Response(invalid.model_dump_json()),Response(correct.model_dump_json()))).coach_member_goal(req)
    assert result.status=='OK' and result.meta.plan_repair_attempts==1


def test_repository_restores_private_dialogue_versions_and_optimistic_revision(tmp_path):
    path=tmp_path/'workspace.sqlite3';repo=SQLiteRepository(path)
    workspace=repo.create({'groupChat':[{'text':'공동 목표'}]})
    snapshot={'groupChat':[{'text':'공동 목표'}],'privateChats':{'alice':[{'text':'개인 조건'}]},'profiles':{'alice':{'weekly_available_minutes':120}},'runs':[{'traces':[{'raw':'do not persist provider payload'}]}]}
    saved=repo.save(workspace['id'],snapshot,0)
    restored=SQLiteRepository(path).get(workspace['id'])
    assert restored['snapshot']['privateChats']['alice'][0]['text']=='개인 조건'
    assert restored['snapshot']['runs'][0]['traces']==[]
    with pytest.raises(RevisionConflict):repo.save(workspace['id'],snapshot,0)
    assert repo.get(workspace['id'])['revision']==saved['revision']
    with repo.connect() as db:assert db.execute('SELECT COUNT(*) FROM versions WHERE kind=?',('private_chat',)).fetchone()[0]==1


def test_repository_does_not_store_credentials(tmp_path):
    repo=SQLiteRepository(tmp_path/'db.sqlite3');w=repo.create({'api_key':'SECRET','nested':{'Authorization':'Bearer SECRET'},'goal':'공동 목표'})
    assert 'SECRET' not in json.dumps(repo.get(w['id']))


def test_provider_accounting_counts_http_calls_not_parent_workflow_twice(tmp_path):
    repo=SQLiteRepository(tmp_path/'db.sqlite3');sink=PersistentTraceSink(repo)
    from wemeet_ai.contracts import CoachRequest
    req=CoachRequest(**regression_conversation().model_dump())
    result=GoalAI(client(Response('현재 대화에서 가능한 실행안을 제안합니다.')),trace_sink=sink).coach_reply(req)
    assert result.status=='OK'
    calls=repo.calls();assert calls['http_attempts']==1 and calls['total_tokens']==15
    call=calls['calls'][0];assert call['request_id']=='provider-123' and call['host']=='api.deepseek.com'
    for event in list(sink.events):
        if event['event']=='run.completed':sink.emit(event)
    assert repo.calls()['total_tokens']==15
    assert provider_identity(client())['key_fingerprint']
    assert 'test-not-a-real-key' not in json.dumps(provider_identity(client()))


def test_baseline_is_versioned_idempotent_and_rejects_unknown_activity(tmp_path):
    old=example_input();spec=GoalAI(client()).prepare_goal_spec(old).data;group=group_fixture(spec)
    member=member_fixture(spec,group,spec.member_profiles[0].member_id)
    from test_personal import contribution
    req=personal_request();goal=contribution(req);goal.owner_member_id=spec.member_profiles[0].member_id;goal.group_plan_id=group.plan_id;goal.evidence_refs=spec.evidence_ids;goal.review_status='ACCEPTED';member.contribution_goal=goal
    baseline=build_baseline(BaselineRequest(goal_spec=spec,group_plan=group,member_plans=[member],scope='PERSONAL',confirmed_by=goal.owner_member_id,owner_member_id=goal.owner_member_id))
    assert baseline['public_payload']['tasks'][0]['task_id']
    assert 'constraints' not in json.dumps(baseline['public_payload'])
    repo=SQLiteRepository(tmp_path/'db.sqlite3');workspace=repo.create({})
    first=repo.save_baseline(workspace['id'],baseline);baseline['public_payload']['group_goal']='나중에 덮어쓸 수 없음'
    repeated=repo.save_baseline(workspace['id'],baseline)
    assert first==repeated
    activity=ActivityReference(plan_version_id=first['plan_version_id'],task_id=first['public_payload']['tasks'][0]['task_id'],recorded_at='2026-11-01T10:00:00+09:00',text='작업 완료')
    assert validate_activity_reference(first,activity)
    activity.plan_version_id='another-version'
    with pytest.raises(ValueError,match='PLAN_VERSION_MISMATCH'):validate_activity_reference(first,activity)


def test_provider_total_is_not_limited_to_display_page(tmp_path):
    repo=SQLiteRepository(tmp_path/'db.sqlite3')
    with repo.connect() as db:
        db.executemany('INSERT INTO provider_calls VALUES(?,?,?,?,?,?,?,?)',[(str(i),'run',None,'deepseek-flash',json.dumps({'total_tokens':10}),200,'api.deepseek.com',str(i)) for i in range(201)])
    counts=repo.calls()
    assert len(counts['calls'])==200 and counts['http_attempts']==201 and counts['total_tokens']==2010


def test_cli_workspace_roundtrip(tmp_path):
    from wemeet_ai.cli import main
    source=tmp_path/'state.json';created=tmp_path/'created.json';loaded=tmp_path/'loaded.json';db=tmp_path/'db.sqlite3'
    source.write_text(json.dumps({'groupChat':[{'text':'저장 후 복원'}]}),encoding='utf-8')
    assert main(['workspace-save','--db',str(db),'--input',str(source),'--output',str(created)])==0
    workspace=json.loads(created.read_text(encoding='utf-8'))
    assert main(['workspace-load','--db',str(db),'--workspace-id',workspace['id'],'--output',str(loaded)])==0
    assert json.loads(loaded.read_text(encoding='utf-8'))['snapshot']['groupChat'][0]['text']=='저장 후 복원'


def test_stale_evaluation_baseline_is_not_delivered_as_current():
    from wemeet_ai.contracts import HandoffRequest, BaselineRef
    old=example_input();spec=GoalAI(client()).prepare_goal_spec(old).data;group=group_fixture(spec)
    reference=BaselineRef(baseline_id='old-baseline',scope='GROUP',group_plan_version_id='old-plan',plan_version_id='old-plan',confirmed_by=spec.member_profiles[0].member_id,confirmed_at='2026-10-28T00:00:00Z')
    result=GoalAI(client()).build_handoff(HandoffRequest(goal_spec=spec,group_plan=group,baseline_refs=[reference]))
    assert not result.data.group_file['integration_ready']
    assert any(i['code']=='BASELINE_VERSION_MISMATCH' for i in result.data.group_file['validation']['issues'])


def test_telemetry_storage_failure_warns_without_destroying_ai_reply():
    class BrokenRepository:
        def record_call(self,*_):raise OSError('disk unavailable')
        def record_run(self,*_):raise OSError('disk unavailable')
    req=CoachRequest(**regression_conversation().model_dump())
    result=GoalAI(client(Response('공동 목표의 다음 행동을 제안합니다.')),trace_sink=PersistentTraceSink(BrokenRepository())).coach_reply(req)
    assert result.status=='OK' and result.warnings


def test_legacy_task_ids_are_stable_in_baseline_and_handoff():
    from wemeet_ai.contracts import HandoffRequest, BaselineRef
    old=example_input();spec=GoalAI(client()).prepare_goal_spec(old).data;group=group_fixture(spec)
    member=member_fixture(spec,group,spec.member_profiles[0].member_id)
    from test_personal import contribution
    goal=contribution(personal_request());goal.owner_member_id=member.public_plan.member_id;goal.group_plan_id=group.plan_id;goal.evidence_refs=spec.evidence_ids;goal.review_status='ACCEPTED';member.contribution_goal=goal
    baseline=build_baseline(BaselineRequest(goal_spec=spec,group_plan=group,member_plans=[member],scope='PERSONAL',confirmed_by=goal.owner_member_id,owner_member_id=goal.owner_member_id))
    reference=BaselineRef(**{k:baseline[k] for k in ('baseline_id','scope','owner_member_id','plan_version_id','group_plan_version_id','confirmed_by','confirmed_at')},task_ids=[t['task_id'] for t in baseline['public_payload']['tasks']])
    result=GoalAI(client()).build_handoff(HandoffRequest(goal_spec=spec,group_plan=group,member_plans=[member],baseline_refs=[reference]))
    assert not any(i['code']=='BASELINE_VERSION_MISMATCH' for i in result.data.group_file['validation']['issues'])


def test_ai_estimate_is_not_promoted_to_personal_fact_before_confirmation():
    from wemeet_ai.contracts import DraftField
    req=personal_request();req.state.group_plan_id=req.group_plan.plan_id;req.state.answer_states['weekly_available_minutes']='UNKNOWN';req.state.fields['weekly_available_minutes']=DraftField(value=None,evidence_refs=['p1'])
    output=personal_model(False);output.extracted_fields['weekly_available_minutes']=DraftField(value=240,origin='AI_SUGGESTED',evidence_refs=['p1'])
    result=GoalAI(client(Response(output.model_dump_json()))).coach_member_goal(req)
    assert result.status=='OK'
    assert result.data.state.fields['weekly_available_minutes'].value is None
    assert 'weekly_available_minutes' in result.data.missing_fields
