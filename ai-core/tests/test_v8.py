import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_core import client,Response,fake_key
from test_personal import request as personal_request,model as personal_model
from test_v6 import proactive_output,dimensions
from wemeet_ai.service import GoalAI
from wemeet_ai.cache import MemoryResultCache
from wemeet_ai.contracts import CoachRequest,DraftField,AnswerOption
from wemeet_ai.examples import regression_conversation
from wemeet_ai.analysis import local_analysis,merge_analysis
from wemeet_ai.personal import normalize_personal_value
from wemeet_ai.tracing import MemoryTraceSink

def req():return CoachRequest(**regression_conversation().model_dump(),coaching_mode='PROACTIVE')
def answer(request):
    output=proactive_output();output['proposals'][0]['evidence_refs']=[request.conversation[0].message_id]
    return Response(json.dumps(output,ensure_ascii=False))

def test_success_cache_typed_result_zero_new_tokens_and_force_idempotency():
    request=req();cache=MemoryResultCache();c=client(answer(request),answer(request))
    def core(**kw):return GoalAI(c,cache_backend=cache,cache_scope='group-1',trace_sink=MemoryTraceSink(),**kw)
    first=core().coach_reply(request);second=core().coach_reply(request)
    assert first.status==second.status=='OK' and len(c.session.calls)==1
    assert second.data.proposals and second.meta.cache_hit and second.meta.usage.total_tokens==0
    assert second.meta.source_run_id==first.meta.run_id and second.meta.provider_request_ids==[]
    assert second.meta.execution_source=='CACHE' and second.meta.cached_usage==first.meta.usage
    third=core(force_refresh=True,idempotency_key='new-intent').coach_reply(request)
    fourth=core(force_refresh=True,idempotency_key='new-intent').coach_reply(request)
    assert len(c.session.calls)==2 and fourth.meta.source_run_id==third.meta.run_id

def test_concurrent_identical_requests_only_one_http():
    request=req();cache=MemoryResultCache();c=client(answer(request))
    def run(_):return GoalAI(c,cache_backend=cache,cache_scope='one').coach_reply(request)
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,range(3)))
    assert all(r.status=='OK' for r in results) and len(c.session.calls)==1
    assert sum(r.meta.cache_hit for r in results)==2

def test_different_workspace_and_changed_prompt_do_not_share():
    request=req();cache=MemoryResultCache();c=client(*(answer(request) for _ in range(3)))
    for scope,variant in [('one',None),('two',None),('two','improved')]:
        assert GoalAI(c,cache_backend=cache,cache_scope=scope,prompt_variant=variant).coach_reply(request).status=='OK'
    assert len(c.session.calls)==3

def test_partial_proposal_preserves_good_fields_but_is_not_cached():
    request=req();output=proactive_output();p=output['proposals'][0];p['evidence_refs']=[request.conversation[0].message_id]
    p['field_updates']['metric']='수치 없음';p['field_updates']['privacy_scope']='나중에 논의'
    c=client(Response(json.dumps(output)),Response(json.dumps(output)));cache=MemoryResultCache()
    for _ in range(2):
        r=GoalAI(c,cache_backend=cache,cache_scope='one').coach_reply(request)
        assert r.status=='OK' and r.data.proposals[0].field_updates['completion_criteria']
        assert set(r.data.proposals[0].field_errors)=={'metric','privacy_scope'}
    assert len(c.session.calls)==2

def test_unresolved_privacy_not_custom_even_when_model_says_custom():
    request=regression_conversation();request.conversation=request.conversation[:1]
    message=request.conversation[0];message.text='좋아. 그런데 인증 방식과 공개 범위는 조금 더 이야기하자.'
    local=local_analysis(request);assert local.fields['privacy'].status=='MISSING'
    from wemeet_ai.contracts import Candidate
    bad=local.model_copy(deep=True);bad.fields['privacy'].status='EXTRACTED';bad.fields['privacy'].candidates=[Candidate(candidate_id='bad',value={'scope':'CUSTOM'},original_text=message.text,evidence_refs=[message.message_id],proposer_ids=[message.speaker_id])]
    merged=merge_analysis(request,bad)
    assert merged.fields['privacy'].status=='MISSING' and not merged.fields['privacy'].candidates

@pytest.mark.parametrize('value,expected',[('120',120),('2시간',120),({'minutes':120},120),({'hours':2},120)])
def test_personal_time_known_shapes(value,expected):assert normalize_personal_value('weekly_available_minutes',value)==expected

def test_bad_personal_buttons_keep_verified_state_after_bounded_repair():
    request=personal_request();request.diagnostic_mode='ADAPTIVE'
    output=personal_model();output.diagnostic_dimensions=dimensions();output.question.options=[AnswerOption(label='이해할 수 없는 시간',value={'arbitrary':1})]
    c=client(Response(output.model_dump_json()),Response(output.model_dump_json()))
    result=GoalAI(c).coach_member_goal(request)
    assert result.status=='OK' and result.data.contract_issues and result.data.state.current_question is None
    assert result.data.missing_fields and len(c.session.calls)==2

def test_no_live_when_explicitly_paused(monkeypatch):
    monkeypatch.setenv('WEMEET_OFFLINE','1');c=client();result=GoalAI(c).coach_reply(req())
    assert result.error.code=='LIVE_CALLS_PAUSED' and not c.session.calls

def test_combined_transport_budget_never_exceeds_four_calls():
    import requests
    from wemeet_ai.client import RunStats,AIError
    c=client(Response('{}'),Response('{}'),Response('{}'),Response('{}'));stats=RunStats()
    for _ in range(4):c.complete_text([{'role':'user','content':'test'}],stats=stats)
    with pytest.raises(AIError,match='4회'):c.complete_text([{'role':'user','content':'test'}],stats=stats)
    assert len(c.session.calls)==4

def test_api_response_and_sqlite_omit_key_identifiers(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from wemeet_ai.adapters import web
    monkeypatch.setenv('WEMEET_DB_PATH',str(tmp_path/'bench.sqlite3'));web.repository.cache_clear()
    with TestClient(web.app) as http:
        result=http.get('/ai/v1/config').json();assert 'key_fingerprint' not in result
        saved=http.post('/ai/v1/workspaces',json={'snapshot':{'api_key':'do-not-store','groupChat':[{'text':'sk-testsecret123456789012345'}]}}).json()
        restored=http.get('/ai/v1/workspaces/'+saved['id']).text
        assert 'do-not-store' not in restored and 'sk-testsecret123456789012345' not in restored
    web.repository.cache_clear()


def test_sqlite_cache_survives_instance_reload_and_expires(tmp_path):
    import time
    from wemeet_ai.adapters.result_cache import SQLiteResultCache
    path=tmp_path/'cache.sqlite3';one=SQLiteResultCache(path);one.put('ok',{'value':1})
    two=SQLiteResultCache(path);assert two.get('ok')=={'value':1}
    two.put('expired',{'value':2},ttl=-1);assert two.get('expired') is None


def test_api_cache_returns_same_typed_output_and_zero_new_calls(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from wemeet_ai.adapters import web
    request=req();c=client(answer(request));monkeypatch.setattr(web.ai,'client',c)
    monkeypatch.setenv('WEMEET_DB_PATH',str(tmp_path/'api.sqlite3'));web.repository.cache_clear()
    with TestClient(web.app) as http:
        headers={'X-Workspace-Id':'same-test-scope'}
        first=http.post('/ai/v1/coach',headers=headers,json=request.model_dump(mode='json')).json()
        second=http.post('/ai/v1/coach',headers=headers,json=request.model_dump(mode='json')).json()
        assert first['status']==second['status']=='OK'
        assert second['data']['proposals'] and second['meta']['execution_source']=='CACHE'
        assert second['meta']['usage']['total_tokens']==0 and len(c.session.calls)==1
    web.repository.cache_clear()


def test_new_database_concurrent_initialization(tmp_path):
    from wemeet_ai.adapters.repository import SQLiteRepository
    path=tmp_path/'new.sqlite3'
    with ThreadPoolExecutor(max_workers=5) as pool:
        values=list(pool.map(lambda _:SQLiteRepository(path).list(),range(5)))
    assert values==[[]]*5
    from wemeet_ai.adapters.web import result_cache
    with ThreadPoolExecutor(max_workers=5) as pool:backends=list(pool.map(lambda _:result_cache(str(path)),range(5)))
    assert all(b is backends[0] for b in backends)


def test_cached_intent_survives_later_generation_and_failure_never_cached():
    request=req();cache=MemoryResultCache();c=client(answer(request),answer(request))
    one=GoalAI(c,cache_backend=cache,cache_scope='one',force_refresh=True,idempotency_key='intent-one').coach_reply(request)
    GoalAI(c,cache_backend=cache,cache_scope='one',force_refresh=True,idempotency_key='intent-two').coach_reply(request)
    repeated=GoalAI(c,cache_backend=cache,cache_scope='one',force_refresh=True,idempotency_key='intent-one').coach_reply(request)
    assert repeated.meta.source_run_id==one.meta.run_id and len(c.session.calls)==2
    bad=client(Response(status=401),Response(status=401));backend=MemoryResultCache()
    for _ in range(2):assert GoalAI(bad,cache_backend=backend,cache_scope='other').coach_reply(request).status=='FAILED'
    assert len(bad.session.calls)==2 and backend.values=={}


def test_cache_read_failure_does_not_trigger_paid_call():
    class Broken(MemoryResultCache):
        def get(self,key):raise OSError('storage unavailable')
    c=client();result=GoalAI(c,cache_backend=Broken(),cache_scope='one').coach_reply(req())
    assert result.status=='FAILED' and result.error.code=='CACHE_UNAVAILABLE' and not c.session.calls


def test_different_member_and_public_profile_change_cache_keys():
    from wemeet_ai.cache import fingerprint
    common=dict(scope='group',model='model',endpoint='provider',credential='internal',prompt_version='v1',variant=None,references=[])
    assert fingerprint('member-plan',{'member_profile':{'member_id':'a'}},owner='a',**common)!=fingerprint('member-plan',{'member_profile':{'member_id':'b'}},owner='b',**common)
    assert fingerprint('group-plan',{'goal':'same','member_profiles':[{'private':'one'}]},owner=None,**common)==fingerprint('group-plan',{'goal':'same','member_profiles':[{'private':'changed'}]},owner=None,**common)


def test_key_echo_is_redacted_in_python_output_and_default_log(tmp_path):
    from wemeet_ai.records import JsonlRecorder
    request=req();output=proactive_output();output['reply']='sk-testsecret123456789012345';output['proposals'][0]['evidence_refs']=[request.conversation[0].message_id]
    result=GoalAI(client(Response(json.dumps(output)))).coach_reply(request)
    assert 'sk-testsecret123456789012345' not in result.model_dump_json()
    path=tmp_path/'log.jsonl';JsonlRecorder(path).record({'authorization':'secret','text':'sk-testsecret123456789012345'})
    assert 'secret' not in path.read_text()
