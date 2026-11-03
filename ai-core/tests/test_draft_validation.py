import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_core import client, Response, fake_key
from test_interactions import draft_fixture
from wemeet_ai import GoalAI
from wemeet_ai.contracts import ConfirmedDecisions, DraftField, DraftRequest, DraftValidationRequest, GroupPrepareRequest
from wemeet_ai.draft_validation import normalize_goal_draft
from wemeet_ai.examples import example_input
from wemeet_ai.tracing import MemoryTraceSink


def test_reported_object_checkin_is_canonical_without_invented_time():
    draft=draft_fixture()
    draft.fields['checkins']=DraftField(value=[{'day_of_week':'화요일','method':'사진 인증'}],review_status='ACCEPTED')
    result=normalize_goal_draft(draft,example_input().analysis.members)
    assert result.fields['checkins'].value==['화요일 · 사진 인증']
    assert result.fields['checkins'].review_status=='ACCEPTED'
    assert not result.fields['checkins'].validation_errors
    assert draft.fields['checkins'].value==[{'day_of_week':'화요일','method':'사진 인증'}]


@pytest.mark.parametrize('value',[['금요일 20:00'],[],[{'weekday':'금요일','time':'20:00','method':'이미지'}]])
def test_string_checkins_empty_optional_and_explicit_time_are_preserved(value):
    result=ConfirmedDecisions(checkins=value)
    assert isinstance(result.checkins,list) and all(isinstance(s,str) for s in result.checkins)
    if value and isinstance(value[0],str):assert result.checkins==value
    if value and isinstance(value[0],dict):assert result.checkins==['금요일 · 20:00 · 이미지']


@pytest.mark.parametrize('value',[[{'unexpected':'삭제하면 안 되는 원문'}],[{'day':'월요일','weekday':'화요일'}],[42]])
def test_unknown_or_ambiguous_objects_remain_inline_errors(value):
    draft=draft_fixture();draft.fields['checkins'].value=value
    normalized=normalize_goal_draft(draft)
    assert normalized.fields['checkins'].value==value
    assert normalized.fields['checkins'].validation_errors
    with pytest.raises(ValidationError):ConfirmedDecisions(checkins=value)


def test_typed_field_validation_preserves_unrelated_cards():
    draft=draft_fixture();draft.fields['frequency_per_week'].value=9
    normalized=normalize_goal_draft(draft)
    assert normalized.fields['frequency_per_week'].validation_errors
    assert normalized.fields['goal']==draft.fields['goal']
    assert not normalized.fields['metric'].validation_errors


def test_checkin_frequency_is_preserved_without_becoming_execution_frequency():
    result=ConfirmedDecisions(checkins=[{'day_of_week':'화요일','method':'사진 인증','frequency_per_week':1}])
    assert result.checkins==['주 1회 · 화요일 · 사진 인증']
    assert result.frequency_per_week is None


def test_dates_and_milestones_produce_card_errors_instead_of_exceptions():
    draft=draft_fixture();draft.fields['end_date'].value='2026-10-01'
    draft.fields['milestones'].value=[{'title':'개발 완료','due_date':123}]
    normalized=normalize_goal_draft(draft)
    assert normalized.fields['end_date'].validation_errors
    assert normalized.fields['milestones'].validation_errors


def test_normalization_runs_after_reviewed_card_restoration():
    previous=draft_fixture();previous.fields['checkins']=DraftField(value=[{'day_of_week':'화요일','method':'사진 인증'}],review_status='EDITED')
    req=example_input();c=client(Response(draft_fixture().model_dump_json()))
    result=GoalAI(c).draft_goal_spec(DraftRequest(analysis=req.analysis,previous_draft=previous))
    assert result.status=='OK'
    assert result.data.fields['checkins'].value==['화요일 · 사진 인증']
    assert result.data.fields['checkins'].review_status=='EDITED'


def test_legacy_reviewed_object_and_next_request_match_after_normalization():
    old=example_input();old.confirmed_decisions.checkins=['화요일 · 사진 인증']
    reviewed=draft_fixture();reviewed.fields={k:DraftField(value=v,review_status='EDITED') for k,v in old.confirmed_decisions.model_dump(mode='json').items() if k!='confirmed'}
    reviewed.fields['checkins'].value=[{'day_of_week':'화요일','method':'사진 인증'}]
    result=GoalAI(client()).prepare_group_goal_spec(GroupPrepareRequest(analysis=old.analysis,confirmed_decisions=old.confirmed_decisions,reviewed_draft=reviewed))
    assert result.status=='OK',result.model_dump_json()
    assert result.data.checkins==['화요일 · 사진 인증']


def test_targeted_ai_supplement_cannot_restore_pre_edit_value():
    old=example_input();previous=draft_fixture()
    previous.fields['checkins']=DraftField(value=['사용자가 저장한 새 일정'],review_status='EDITED')
    generated=draft_fixture();generated.fields['checkins'].value=[{'day_of_week':'화요일','method':'이전 사진 인증'}]
    result=GoalAI(client(Response(generated.model_dump_json()))).draft_goal_spec(
        DraftRequest(analysis=old.analysis,previous_draft=previous,target_fields=['metric']))
    assert result.status=='OK'
    assert result.data.fields['checkins'].value==['사용자가 저장한 새 일정']
    assert result.data.fields['checkins'].review_status=='EDITED'


def test_draft_validation_endpoint_is_pure_and_records_actual_normalization():
    from wemeet_ai.adapters.web import app
    draft=draft_fixture();draft.fields['checkins'].value=[{'day_of_week':'화요일','method':'사진 인증'}]
    with TestClient(app) as web:
        response=web.post('/ai/v1/goal-spec/validate-draft',json={'draft':draft.model_dump(mode='json'),'members':example_input().analysis.model_dump(mode='json')['members']})
        data=response.json();assert response.status_code==200 and data['status']=='OK'
        assert data['meta']['usage']['total_tokens']==0
        assert any(e['event']=='draft.field.normalized' for e in data['traces'])


def test_design_period_sends_explicit_start_anchored_weeks():
    from datetime import date
    from test_core import group_fixture
    old=example_input()
    spec=GoalAI(client()).prepare_goal_spec(old).data.model_copy(update={'start_date':date(2026,10,28),'end_date':date(2026,11,15)})
    group=group_fixture(spec)
    group.weekly_outline.append(group.weekly_outline[0].model_copy(update={'week':3}))
    c=client(Response(group.model_dump_json()))
    result=GoalAI(c).generate_group_plan(spec)
    assert result.status=='OK',result.model_dump_json()
    payload=json.loads(c.session.calls[0][1]['json']['messages'][-1]['content'])
    assert payload['required_weeks']==[1,2,3]
    assert payload['week_ranges']==[
        {'week':1,'start_date':'2026-10-28','end_date':'2026-11-03'},
        {'week':2,'start_date':'2026-11-04','end_date':'2026-11-10'},
        {'week':3,'start_date':'2026-11-11','end_date':'2026-11-15'},
    ]
