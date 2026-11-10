import json
import pytest
from fastapi.testclient import TestClient
from tools.bench.server import app
from tools.bench.records import repository
from _data import sample

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('WEMEET_RECORD_DB',str(tmp_path/'api.sqlite3'))
    with TestClient(app) as c:yield c

def test_input_saved_before_AI_failure_and_previous_success_kept(client,monkeypatch):
    rid=client.post('/api/records',json={}).json()['record_id']
    first=client.post('/api/run/A',json={'record_id':rid,'action_id':'first','expected_revision':0,'input':sample('A'),'mode':'FIXTURE'})
    assert first.json()['status']=='OK'
    def fail(*a,**k):raise AssertionError('Unexpected paid network')
    monkeypatch.delenv('TIMELY_API_KEY',raising=False);monkeypatch.setattr('requests.Session.post',fail)
    request={**sample('A')};request['conversation']=[*request['conversation'],{'message_id':'m3','speaker_id':'u1','role':'user','text':'실패해도 보존','created_at':'2026-11-20T10:05:00+09:00'}]
    failed=client.post('/api/run/A',json={'record_id':rid,'action_id':'failed','expected_revision':int(first.headers['X-Record-Revision']),'input':request,'mode':'LIVE'})
    assert failed.json()['status']=='FAILED'
    stored=client.get('/api/records/'+rid).json()
    assert stored['conversation'][-1]['text']=='실패해도 보존'
    assert len(stored['artifacts'])==2 and stored['artifacts'][0]['response']['status']=='OK'

def test_duplicate_action_does_not_execute_again(client,monkeypatch):
    rid=client.post('/api/records',json={}).json()['record_id']
    body={'record_id':rid,'action_id':'once','expected_revision':0,'input':sample('A'),'mode':'FIXTURE'}
    one=client.post('/api/run/A',json=body)
    def fail(*a,**k):raise AssertionError('Duplicate execution')
    monkeypatch.setattr('tools.bench.server.execute',fail)
    two=client.post('/api/run/A',json=body)
    assert two.status_code==200 and two.headers['X-Record-Reused']=='true'
    assert one.json()==two.json()

def test_open_and_prepare_do_not_call_model(client,monkeypatch):
    def fail(*a,**k):raise AssertionError('Unexpected model call')
    monkeypatch.setattr('tools.bench.server.execute',fail)
    rid=client.post('/api/records',json={}).json()['record_id']
    saved=client.post('/api/records/'+rid+'/input',json={'module':'A','input':sample('A'),'expected_revision':0,'pending':'작성 중'})
    view=client.get('/api/records/'+rid).json()
    ready=client.post('/api/records/'+rid+'/prepare',json={'module':'B'}).json()
    assert saved.status_code==200 and view['pending']['A']=='작성 중' and ready['ready']

def test_storage_failure_never_looks_saved(client,monkeypatch):
    rid=client.post('/api/records',json={}).json()['record_id']
    def fail(*a,**k):raise OSError('disk full synthetic')
    monkeypatch.setattr(repository(),'save_input',fail)
    out=client.post('/api/records/'+rid+'/input',json={'module':'A','input':sample('A'),'expected_revision':0})
    assert out.status_code==503 and out.json()['error']['code']=='STORAGE_FAILED'

def test_public_record_has_no_other_private_intake(client):
    rid=client.post('/api/records',json={}).json()['record_id']
    req=sample('E.coach');req['conversation']=[{'message_id':'p1','role':'user','speaker_id':'u1','text':'PRIVATE_MARKER_012','created_at':'2026-11-20T10:00:00+09:00'}]
    client.post('/api/records/'+rid+'/input',json={'module':'E.coach','input':req,'expected_revision':0})
    assert 'PRIVATE_MARKER_012' not in client.get('/api/records/'+rid).text
    assert 'PRIVATE_MARKER_012' not in client.get('/api/records/'+rid+'?owner_member_id=u2').text
    assert 'PRIVATE_MARKER_012' in client.get('/api/records/'+rid+'?owner_member_id=u1').text

def test_successful_AI_result_not_lost_on_storage_failure(client,monkeypatch):
    rid=client.post('/api/records',json={}).json()['record_id'];repo=repository();save=repo.save_artifact
    def fail(*a,**k):raise OSError('after-generation disk failure')
    monkeypatch.setattr(repo,'save_artifact',fail)
    body={'record_id':rid,'action_id':'retry-store-only','expected_revision':0,'input':sample('A'),'mode':'FIXTURE'}
    failed=client.post('/api/run/A',json=body)
    assert failed.status_code==503 and failed.json()['unsaved_result']['data']['reply']
    monkeypatch.setattr(repo,'save_artifact',save)
    def no_model(*a,**k):raise AssertionError('Must not regenerate successful result')
    monkeypatch.setattr('tools.bench.server.execute',no_model)
    retried=client.post('/api/run/A',json=body)
    assert retried.status_code==200 and retried.json()['status']=='OK'
    assert len(repo.load(rid).artifacts)==1

def test_mode_is_persisted_explicitly(client):
    rid=client.post('/api/records',json={'execution_mode':'FIXTURE'}).json()['record_id']
    assert client.get('/api/records/'+rid).json()['execution_mode']=='FIXTURE'


def test_new_private_button_answer_is_current_input_not_stale(client):
    rid=client.post('/api/records',json={'members':sample('A')['members']}).json()['record_id']
    rev=client.post('/api/records/'+rid+'/input',json={'module':'D','input':sample('D'),'expected_revision':0}).json()['revision']
    inp=sample('E.coach')
    rev=client.post('/api/records/'+rid+'/input',json={'module':'E.coach','input':inp,'expected_revision':rev}).json()['revision']
    prep=client.post('/api/records/'+rid+'/prepare',json={'module':'E.coach','owner_member_id':'u1'}).json()
    inp=prep['request']
    rev=client.post('/api/records/'+rid+'/input',json={'module':'E.coach','input':inp,'dependencies':prep['dependencies'],'expected_revision':rev}).json()['revision']
    first=client.post('/api/run/E.coach',json={'record_id':rid,'action_id':'start','expected_revision':rev,'input':inp,'mode':'FIXTURE'})
    data=first.json()['data'];q=data['state']['question']
    inp['state']=data['state'];inp['conversation'].append({'message_id':'new-answer','speaker_id':'u1','role':'user','text':q['options'][0]['label'],'created_at':'2026-11-20T11:00:00+09:00'})
    inp['answer']={'question_id':q['question_id'],'option_id':q['options'][0]['option_id'],'message_id':'new-answer'}
    second=client.post('/api/run/E.coach',json={'record_id':rid,'action_id':'answer','expected_revision':int(first.headers['X-Record-Revision']),'input':inp,'mode':'FIXTURE'})
    assert second.json()['status']=='OK',second.text
    assert second.json()['data']['state']['fields']['current_level']['value']=='일부 경험'


def test_standalone_personal_plan_needs_no_prior_group_record(client):
    rid=client.post('/api/records',json={}).json()['record_id']
    out=client.post('/api/run/E.plan',json={'record_id':rid,'action_id':'direct-plan','expected_revision':0,'input':sample('E.plan'),'mode':'FIXTURE'})
    assert out.json()['status']=='OK',out.text


def test_frontend_assets_cannot_silently_reuse_old_versions(client):
    for file in ['bench.js','bench.css','continuity.js']:
        out=client.get('/assets/'+file+'?v=0.13.0')
        assert out.status_code==200 and out.headers['cache-control']=='no-store'
    from wemeet_ai import __version__
    assert f'bench.js?v={__version__}' in client.get('/').text
