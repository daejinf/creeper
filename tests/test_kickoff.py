"""Embedded phase-1 contracts. Fake transport is injected only by these tests."""
import pytest
from fastapi.testclient import TestClient
from tools.bench.server import app
from tools.bench import kickoff
from tools.bench.records import repository
from wemeet_ai.m0 import Runtime
from wemeet_ai.shared.contracts import Message
from _fixture import FixtureClient


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('WEMEET_RECORD_DB',str(tmp_path/'kickoff.sqlite3'))
    with TestClient(app) as client:yield client


def create(client,count):
    return client.post('/api/kickoff/groups',json={'title':'함께 세우는 목표','members':[
        {'member_id':f'person-{i}','nickname':f'참여자 {i+1}'} for i in range(count)]}).json()


@pytest.mark.parametrize('count',[1,3,7,25])
def test_member_count_not_fixed(client,count,monkeypatch):
    monkeypatch.setattr('requests.Session.post',lambda *a,**k:pytest.fail('Paid call during storage'))
    group=create(client,count);rid=group['group_id'];url='/api/kickoff/groups/'+rid
    assert len(group['members'])==count
    out=client.post(url+'/messages',json={'message_id':'first','member_id':f'person-{count-1}','text':'목표를 정해봅시다.'})
    assert out.status_code==200 and out.json()['messages'][0]['speaker_id']==f'person-{count-1}'
    added=client.post(url+'/members',json={'member_id':'new-person','nickname':'추가 구성원'})
    assert len(added.json()['members'])==count+1
    assert len(client.get(url).json()['messages'])==1


def test_message_duplicate_and_conflict(client):
    url='/api/kickoff/groups/'+create(client,3)['group_id']
    body={'message_id':'one','member_id':'person-0','text':'같이 계획하자'}
    first=client.post(url+'/messages',json=body).json()
    assert client.post(url+'/messages',json=body).json()['revision']==first['revision']
    assert client.post(url+'/messages',json={**body,'text':'다른 내용'}).status_code==409
    assert client.post(url+'/messages',json={**body,'message_id':'two','member_id':'unknown'}).status_code==422
    assert len(client.get(url).json()['messages'])==1


def test_coach_restores_reuses_and_new_speaker_reaches_core(client,monkeypatch):
    transport=FixtureClient();monkeypatch.setattr(kickoff,'runtime_for',lambda rid:Runtime(transport))
    url='/api/kickoff/groups/'+create(client,4)['group_id']
    client.post(url+'/messages',json={'message_id':'one','member_id':'person-3','text':'PPT 준비 계획을 세우자.'})
    one=client.post(url+'/coach',json={'action_id':'coach-1'}).json()
    assert one['result']['status']=='OK' and len(transport.calls)==1
    assert len(one['group']['messages'])==2
    assert one['group']['coach_request']['members'][-1]['nickname']=='참여자 4'
    assert client.post(url+'/coach',json={'action_id':'coach-1'}).json()['result']==one['result']
    repeat=client.post(url+'/coach',json={'action_id':'coach-2'}).json()
    assert repeat['reused'] and len(transport.calls)==1
    client.post(url+'/members',json={'member_id':'new','nickname':'새 사람'})
    client.post(url+'/messages',json={'message_id':'two','member_id':'new','text':'나는 자료조사를 맡을게.'})
    two=client.post(url+'/coach',json={'action_id':'coach-3'}).json()
    assert two['result']['status']=='OK' and len(transport.calls)==2
    assert len(two['group']['members'])==5
    assert len(client.get(url).json()['messages'])==4
    assert len(client.post(url+'/coach',json={'action_id':'coach-3'}).json()['group']['messages'])==4


def test_coach_failure_preserves_chat_and_no_fixture_fallback(client,monkeypatch):
    monkeypatch.delenv('TIMELY_API_KEY',raising=False)
    monkeypatch.setattr('requests.Session.post',lambda *a,**k:pytest.fail('Key missing: no network'))
    url='/api/kickoff/groups/'+create(client,3)['group_id']
    client.post(url+'/messages',json={'message_id':'one','member_id':'person-1','text':'내일까지 계획을 정하자.'})
    out=client.post(url+'/coach',json={'action_id':'fail'}).json()
    assert out['result']['status']=='FAILED' and out['result']['error']['code']=='AUTH_FAILED'
    assert len(out['group']['messages'])==1
    assert client.get(url).json()['messages'][0]['text']=='내일까지 계획을 정하자.'


def test_reuse_after_transient_reload_failure_restores_visible_success(client,monkeypatch):
    from wemeet_ai.m0 import runtime as runtime_module
    transport=FixtureClient();monkeypatch.setattr(kickoff,'runtime_for',lambda rid:Runtime(transport))
    url='/api/kickoff/groups/'+create(client,3)['group_id']
    client.post(url+'/messages',json={'message_id':'one','member_id':'person-0','text':'발표 준비 계획을 정하자.'})
    success=client.post(url+'/coach',json={'action_id':'success'}).json()['result']
    monkeypatch.setattr(runtime_module,'reload_required',lambda task:True)
    failed=client.post(url+'/coach',json={'action_id':'reload-pending','fresh':True}).json()
    assert failed['result']['error']['code']=='SOURCE_RELOAD_REQUIRED'
    monkeypatch.setattr(runtime_module,'reload_required',lambda task:False)
    recovered=client.post(url+'/coach',json={'action_id':'recovered'}).json()
    assert recovered['reused'] and recovered['group']['coach_result']['status']=='OK'
    meta=recovered['result']['meta']
    assert meta['source_run_id']==success['meta']['run_id'] and meta['cache_hit']
    assert meta['usage']['total_tokens']==meta['transport_attempts']==0
    assert client.get(url).json()['coach_result']==recovered['result']
    duplicate=client.post(url+'/coach',json={'action_id':'recovered'}).json()
    assert duplicate['result']==recovered['result'] and len(duplicate['group']['messages'])==2
    assert len(transport.calls)==1


def test_private_text_not_in_embed_response(client):
    rid=create(client,3)['group_id'];repo=repository()
    repo.append_message(rid,Message(message_id='private',speaker_id='person-0',text='SYNTHETIC_PRIVATE_CONSTRAINT',created_at='2026-11-04T10:00:00+09:00'),scope='PRIVATE',owner_member_id='person-0')
    assert 'SYNTHETIC_PRIVATE_CONSTRAINT' not in client.get('/api/kickoff/groups/'+rid).text


def test_pages_no_model_call_and_live_only(client,monkeypatch):
    monkeypatch.setattr('requests.Session.post',lambda *a,**k:pytest.fail('Unexpected model call'))
    page=client.get('/kickoff')
    assert page.status_code==200 and page.headers['content-security-policy']=="frame-ancestors 'self'"
    assert '/kickoff' in client.get('/kickoff-demo').text
    assert client.get('/assets/kickoff.js').status_code==200
    url='/api/kickoff/groups/'+create(client,3)['group_id']
    assert client.post(url+'/coach',json={'action_id':'fake','mode':'FIXTURE'}).status_code==422
    assert client.post('/api/kickoff/groups',json={'title':'bad','members':[]}).status_code==422
