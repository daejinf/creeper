import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import pytest
from pydantic import ValidationError
from wemeet_ai.continuity import Artifact, RecordView, prepare_module_input, source_tokens, input_signature, stale_reasons
from wemeet_ai.shared.contracts import Message
from tools.bench.store import SQLiteRecordStore, RevisionConflict
from _data import sample, plan, MEMBERS

def message(mid='m1',owner='u1',text='공동 목표를 만들어요'):
    return Message(message_id=mid,speaker_id=owner,text=text,created_at='2026-11-20T10:00:00+09:00')

@pytest.fixture
def repo(tmp_path):return SQLiteRecordStore(tmp_path/'records.sqlite3')

def artifact(module,payload,response,*,owner=None,deps=None,action=None):
    scope='PRIVATE' if module.startswith('E.') else 'PUBLIC'
    return Artifact(artifact_id='artifact-'+(action or module),action_id=action or module,module=module,scope=scope,
        owner_member_id=owner,request=payload,response={'status':'OK','data':response},dependencies=deps or {})

def test_core_records_import_no_database_or_web():
    import subprocess,sys
    p=subprocess.run([sys.executable,'-I','-c',"import sys; from wemeet_ai.continuity import prepare_module_input; assert not any(n in sys.modules for n in ['sqlite3','fastapi','uvicorn'])"],capture_output=True,text=True)
    assert p.returncode==0,p.stderr

def test_messages_survive_repository_restart(repo):
    view=repo.create(members=MEMBERS)
    repo.append_message(view.record_id,message(),scope='PUBLIC',expected_revision=0)
    again=SQLiteRecordStore(repo.path).load(view.record_id)
    assert again.conversation[0].text=='공동 목표를 만들어요'
    assert prepare_module_input('B',again).request['conversation'][0]['message_id']=='m1'

def test_duplicate_message_no_revision_increment(repo):
    view=repo.create();one=repo.append_message(view.record_id,message(),scope='PUBLIC',expected_revision=0)
    two=repo.append_message(view.record_id,message(),scope='PUBLIC',expected_revision=0)
    assert one==two==1 and len(repo.load(view.record_id).conversation)==1

def test_parallel_duplicate_message_saved_once(repo):
    rid=repo.create().record_id
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:repo.append_message(rid,message(),scope='PUBLIC'),range(2)))
    assert results==[1,1]

def test_conflicting_message_or_revision_does_not_overwrite(repo):
    rid=repo.create().record_id
    repo.append_message(rid,message(),scope='PUBLIC',expected_revision=0)
    with pytest.raises(RevisionConflict):repo.append_message(rid,message(text='수정된 원문'),scope='PUBLIC')
    with pytest.raises(RevisionConflict):repo.save_input(rid,'A',sample('A'),expected_revision=0)
    assert repo.load(rid).conversation[0].text=='공동 목표를 만들어요'

def test_private_views_do_not_mix(repo):
    rid=repo.create(members=MEMBERS).record_id
    for owner in ['u1','u2']:
        repo.append_message(rid,message(owner,owner,'SECRET_'+owner),scope='PRIVATE',owner_member_id=owner)
        req=sample('E.coach');req['owner_member_id']=owner
        repo.save_input(rid,'E.coach',req,pending='PENDING_'+owner)
        repo.save_artifact(rid,artifact('E.coach',req,{'state':{'owner_member_id':owner}},owner=owner,action=owner))
    public=repo.load(rid).model_dump_json()
    own=repo.load(rid,owner_member_id='u1').model_dump_json()
    assert 'SECRET_' not in public and 'PENDING_' not in public
    assert 'SECRET_u1' in own and 'SECRET_u2' not in own and 'PENDING_u2' not in own

def test_scoped_view_rejects_mixed_artifact():
    with pytest.raises(ValidationError):RecordView(record_id='r',owner_member_id='u1',artifacts=[artifact('E.coach',{}, {},owner='u2')])

def test_failed_result_preserves_success_and_inputs(repo):
    rid=repo.create().record_id
    repo.save_input(rid,'A',sample('A'),pending='아직 작성 중')
    first=artifact('A',sample('A'),{'reply':'성공'},action='success');repo.save_artifact(rid,first)
    failed=artifact('A',sample('A'),{},action='failure');failed.response={'status':'FAILED','error':{'code':'TIMEOUT'}};repo.save_artifact(rid,failed)
    restored=repo.load(rid)
    assert restored.pending['A']=='아직 작성 중'
    assert len(restored.artifacts)==2 and restored.artifacts[0].response['data']['reply']=='성공'

def test_artifact_action_is_idempotent(repo):
    rid=repo.create().record_id;a=artifact('A',sample('A'),{'reply':'same'})
    one=repo.save_artifact(rid,a,expected_revision=0)
    two=repo.save_artifact(rid,a,expected_revision=0)
    assert one==two and len(repo.load(rid).artifacts)==1

def test_service_diagnostics_not_in_public_or_personal_view(repo):
    rid=repo.create().record_id
    a=artifact('F',sample('F'),{'valid':True});a.scope='SERVICE'
    repo.save_artifact(rid,a)
    assert not repo.load(rid).artifacts and not repo.load(rid,owner_member_id='u1').artifacts
    assert len(repo.load(rid,internal=True).artifacts)==1

def test_prepare_C_and_D_reuses_record_data_without_model():
    view=RecordView(record_id='r',members=MEMBERS,conversation=sample('B')['conversation'])
    deps={'public_messages':source_tokens(view)['public_messages']}
    view.artifacts=[artifact('B',sample('B'),{'facts':sample('C')['facts'],'conflicted_fields':[]},deps=deps)]
    c=prepare_module_input('C',view)
    assert c.ready and c.request['facts']['goal']['value']
    view.artifacts.append(artifact('C.confirm',{},sample('D'),deps=deps))
    assert prepare_module_input('D',view).ready

def test_new_public_message_marks_dependent_goal_stale():
    view=RecordView(record_id='r',members=MEMBERS,conversation=[message()])
    deps={'public_messages':source_tokens(view)['public_messages']}
    view.artifacts=[artifact('C.confirm',{},sample('D'),deps=deps)]
    view.conversation.append(message('m2',text='마감일을 바꾸자'))
    prepared=prepare_module_input('D',view)
    assert prepared.stale and not prepared.ready and 'public_messages' in prepared.stale_reasons

def test_AI_reply_does_not_immediately_stale_own_output():
    view=RecordView(record_id='r',members=MEMBERS,conversation=[message()])
    prepared=prepare_module_input('A',view)
    view.conversation.append(Message(message_id='ai1',role='assistant',text='조언',created_at='2026-11-20T10:01:00+09:00'))
    assert not stale_reasons(view,prepared.dependencies)

def test_E_continuation_uses_only_owner_and_public_goal():
    view=RecordView(record_id='r',members=MEMBERS,owner_member_id='u1',private_conversation=[message('p1',text='30분 가능')])
    view.inputs['D']=sample('D')
    request=prepare_module_input('E.coach',view)
    assert request.ready and request.request['context']['goal']==sample('D')['goal']
    assert request.request['conversation'][0]['message_id']=='p1'
    assert 'group_plan' not in request.request
    assert not prepare_module_input('E.coach',view,{'owner_member_id':'u2'}).ready

def test_personal_message_does_not_invalidate_group(repo):
    rid=repo.create(members=MEMBERS).record_id
    repo.save_input(rid,'D',sample('D'))
    before=prepare_module_input('D',repo.load(rid))
    repo.append_message(rid,message('p','u1','PRIVATE'),scope='PRIVATE',owner_member_id='u1')
    after=prepare_module_input('D',repo.load(rid))
    assert before.request==after.request and before.dependencies==after.dependencies

def test_direct_overrides_still_allow_standalone_modules():
    view=RecordView(record_id='r',members=MEMBERS)
    assert prepare_module_input('D',view,sample('D')).ready
    assert prepare_module_input('F',view,sample('F')).ready

def test_failed_coaching_state_is_preserved_for_next_input(repo):
    rid=repo.create(members=MEMBERS).record_id;repo.save_input(rid,'D',sample('D'))
    request=sample('E.coach')
    failed=artifact('E.coach',request,{},owner='u1')
    failed.response={'status':'FAILED','data':{'state':{'owner_member_id':'u1','fields':{'weekly_available_minutes':{'value':30}},'answer_states':{'weekly_available_minutes':'KNOWN'}}}}
    repo.save_artifact(rid,failed)
    prepared=prepare_module_input('E.coach',repo.load(rid,owner_member_id='u1'))
    assert prepared.request['state']['fields']['weekly_available_minutes']['value']==30

def test_obvious_private_fields_cannot_be_stored_public(repo):
    rid=repo.create().record_id
    with pytest.raises(ValueError):repo.save_input(rid,'A',{**sample('A'),'private_notes':['PRIVATE']})
    assert 'PRIVATE' not in repo.load(rid).model_dump_json()

def test_explained_date_has_canonical_value():
    from wemeet_ai.shared.normalization import normalize_fact
    assert normalize_fact('start_date','12/1 (연도 보완 2026-12-01 제안)')=='2026-12-01'


def test_prepare_is_pure_even_when_personal_context_differs():
    from examples._data import sample
    from wemeet_ai.continuity import RecordView,prepare_module_input
    view=RecordView(record_id='pure',owner_member_id='u1',inputs={'E.plan':sample('E.plan')})
    before=view.model_dump(mode='json')
    prepare_module_input('E.plan',view)
    assert view.model_dump(mode='json')==before


def test_independent_E_context_continues_without_D(repo):
    rid=repo.create(members=MEMBERS).record_id
    request=sample('E.coach');repo.save_input(rid,'E.coach',request)
    own=repo.load(rid,owner_member_id='u1')
    prepared=prepare_module_input('E.draft',own)
    assert prepared.ready and prepared.request['context']==request['context']
    assert not prepare_module_input('E.draft',repo.load(rid,owner_member_id='u2')).ready


def test_independent_E_plan_prepares_F_with_known_comparison_conditions(repo):
    from wemeet_ai.m1.f_validation import ValidationRequest,validate_plan
    rid=repo.create(members=MEMBERS).record_id
    request=sample('E.plan');generated=plan('u1')
    repo.save_artifact(rid,artifact('E.plan',request,generated,owner='u1'))
    prepared=prepare_module_input('F',repo.load(rid,owner_member_id='u1',internal=True))
    assert prepared.ready and prepared.request['context']==request['context']
    prepared.request['member_plans'][0]['end_date']='2026-12-28'
    out=validate_plan(ValidationRequest.model_validate(prepared.request))
    assert not out.data.valid and any(i.code=='CONFIRMED_VALUE_CHANGED'for i in out.data.issues)
