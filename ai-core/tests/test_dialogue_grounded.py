import json
import pytest
from test_core import client,Response,fake_key
from test_interactions import draft_fixture
from wemeet_ai import GoalAI
from wemeet_ai.contracts import *
from wemeet_ai.examples import regression_conversation,example_input
from wemeet_ai.analysis import local_analysis,merge_analysis

def dialogue(text='어떻게 시작할지 조언해줘'):
    request=CoachRequest(**regression_conversation().model_dump(),coaching_mode='DIALOGUE');request.conversation=request.conversation[:1];request.conversation[0].text=text;return request

def test_dialogue_plain_text_never_returns_form_cards_or_changes_stage():
    request=dialogue('계획을 만들어줘');c=client(Response('제안: 먼저 핵심 결과물을 작게 완성하고 검증하는 순서가 좋겠습니다. 기간은 아직 결정되지 않았습니다.'))
    result=GoalAI(c).coach_reply(request)
    assert result.status=='OK' and result.meta.task=='coach-dialogue'
    assert result.data.proposals==[] and result.data.next_action=='CONTINUE'
    assert result.data.question is None and len(c.session.calls)==1
    body=c.session.calls[0][1]['json'];assert 'response_format' not in body

def test_dialogue_one_question_only_once_in_reply_and_state_kept():
    result=GoalAI(client(Response('작업 범위를 줄이는 편이 좋겠습니다. 최종 결과물은 무엇인가요?'))).coach_reply(dialogue())
    assert result.status=='OK' and result.data.reply.count('?')==1 and result.data.question is None
    assert result.data.updated_coach_state.consecutive_questions==1

def test_grounded_ignores_model_invented_dates_and_privacy():
    request=dialogue('프로젝트를 해보고 싶어요');analysis=local_analysis(AnalysisRequest.model_validate(request.model_dump(include={'conversation','members','reference_date','timezone'})))
    made=draft_fixture();result=GoalAI(client(Response(made.model_dump_json()))).draft_goal_spec(DraftRequest(analysis=analysis))
    assert result.status=='OK'
    for field in ['start_date','end_date','privacy_scope','verification_method','completion_criteria','metric']:
        assert result.data.fields[field].value is None and result.data.fields[field].origin=='UNRESOLVED'

def test_explicit_suggestion_only_requested_field_and_preserves_confirmed():
    analysis=example_input().analysis;previous=draft_fixture();previous.fields['goal'].review_status='EDITED';previous.fields['privacy_scope']=DraftField()
    output=draft_fixture();output.fields['goal'].value='임의 변경';output.fields['privacy_scope']=DraftField(value='OBJECTIVE_ONLY',origin='EXTRACTED',reason='추천')
    result=GoalAI(client(Response(output.model_dump_json()))).draft_goal_spec(DraftRequest(analysis=analysis,previous_draft=previous,draft_mode='SUGGEST',target_fields=['privacy_scope']))
    assert result.status=='OK' and result.data.fields['goal']==previous.fields['goal']
    assert result.data.fields['privacy_scope'].value=='OBJECTIVE_ONLY' and result.data.fields['privacy_scope'].origin=='AI_SUGGESTED'
    assert result.data.fields['privacy_scope'].evidence_refs==[] and result.data.fields['privacy_scope'].review_status=='PENDING'

def note_analysis(ai_text,user_text,notes):
    request=regression_conversation();request.conversation=request.conversation[:1];first=request.conversation[0]
    request.conversation=[Message(message_id='u1',speaker_id=first.speaker_id,role='user',text='프로젝트를 함께 하고 싶어요.',created_at=first.created_at),Message(message_id='a1',role='assistant',text=ai_text,created_at=first.created_at),Message(message_id='u2',speaker_id=first.speaker_id,role='user',text=user_text,created_at=first.created_at)]
    model=local_analysis(request);model.coaching_notes=notes;return request,model

def test_ambiguous_agreement_after_two_alternatives_is_not_selected():
    request,model=note_analysis('① 2주 안 또는 ② 3주 안을 제안합니다.','좋아',[CoachingNote(assistant_message_id='a1',suggestion_text='① 2주 안 또는 ② 3주 안을 제안합니다.',user_response_refs=['u2'],disposition='SUPPORTED')])
    result=merge_analysis(request,model)
    assert result.coaching_notes[0].disposition=='AMBIGUOUS'
    assert not result.fields['timeline'].candidates

@pytest.mark.parametrize('text,state',[('2주 안으로 하자','SUPPORTED'),('그 안은 거절할게요','REJECTED')])
def test_clear_choice_and_rejection_keep_actual_source(text,state):
    request,model=note_analysis('2주 동안 검증하는 안을 제안합니다.',text,[CoachingNote(assistant_message_id='a1',suggestion_text='2주 동안 검증하는 안을 제안합니다.',user_response_refs=['u2'],disposition='SUPPORTED')])
    result=merge_analysis(request,model);assert result.coaching_notes[0].disposition==state

def test_coaching_note_cannot_cite_human_as_ai_or_future_user_as_past():
    request,model=note_analysis('작은 범위를 제안합니다.','좋아',[CoachingNote(assistant_message_id='u1',suggestion_text='프로젝트를 함께 하고 싶어요.')])
    from wemeet_ai.client import AIError
    with pytest.raises(AIError):merge_analysis(request,model)

def test_suggest_mode_without_targets_fails_before_http():
    c=client();result=GoalAI(c).draft_goal_spec(DraftRequest(analysis=example_input().analysis,draft_mode='SUGGEST'))
    assert result.status=='FAILED' and not c.session.calls


def test_explicit_objective_privacy_is_not_a_conflicting_custom_policy():
    request=regression_conversation();request.conversation=request.conversation[:1];m=request.conversation[0];m.text='그룹에는 완료 여부와 달성률만 공개하자.'
    model=local_analysis(request);model.fields['privacy'].candidates=[Candidate(candidate_id='model-custom',value={'scope':'CUSTOM','description':m.text},original_text=m.text,evidence_refs=[m.message_id],proposer_ids=[m.speaker_id])]
    result=merge_analysis(request,model)
    assert result.fields['privacy'].status=='EXTRACTED'
    assert [c.value for c in result.fields['privacy'].candidates]==[{'scope':'OBJECTIVE_ONLY'}]


def test_short_member_ids_do_not_corrupt_technical_words():
    request=dialogue();request.members=[Member(member_id='j',nickname='지민'),Member(member_id='s',nickname='서준')];request.conversation[0].speaker_id='j'
    result=GoalAI(client(Response('FastAPI와 JavaScript를 사용하면 됩니다.'))).coach_reply(request)
    assert result.status=='OK' and result.data.reply=='FastAPI와 JavaScript를 사용하면 됩니다.'


def test_explicit_from_to_dates_survive_model_duration_projection():
    from wemeet_ai.analysis import normalize_value
    from datetime import date
    assert normalize_value('timeline',{'start_date':'2026-11-01','end_date':'2026-11-15','duration_days':15},date(2026,10,29))[0]=={'start_date':'2026-11-01','end_date':'2026-11-15'}
    request=regression_conversation();request.conversation=request.conversation[:1];m=request.conversation[0];m.text='일정은 2026-11-01부터 2026-11-15까지로 제안해.'
    model=local_analysis(request);model.fields['timeline'].candidates=[Candidate(candidate_id='duration-only',value={'duration_days':15},original_text=m.text,evidence_refs=[m.message_id],proposer_ids=[m.speaker_id])]
    result=merge_analysis(request,model)
    assert result.fields['timeline'].status=='EXTRACTED'
    assert [c.value for c in result.fields['timeline'].candidates]==[{'start_date':'2026-11-01','end_date':'2026-11-15'}]
