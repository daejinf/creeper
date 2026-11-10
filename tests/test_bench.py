import pytest
from fastapi.testclient import TestClient
from tools.bench.server import app
from _data import sample

@pytest.fixture
def browser():
    with TestClient(app) as client: yield client

def test_loading_does_not_call_model(browser,monkeypatch):
    def fail(*a,**k):raise AssertionError('Unexpected HTTP')
    monkeypatch.setattr('requests.Session.post',fail)
    assert browser.get('/').status_code==200
    assert browser.get('/api/config').status_code==200
    assert len(browser.get('/api/scenarios').json())==200
    assert browser.get('/api/example/D').status_code==200

@pytest.mark.parametrize('module',['A','B','C','D','E.coach','E.draft','E.plan','F'])
def test_direct_module_http(browser,module):
    out=browser.post('/api/run/'+module,json={'mode':'FIXTURE','input':sample(module),'scope_id':'test-'+module})
    assert out.status_code==200
    assert out.json()['status']=='OK'

def test_input_error_no_key_echo(browser):
    out=browser.post('/api/run/D',json={'input':{'unexpected':'sk-SYNTHETIC_SECRET_NOT_REAL'}})
    assert out.status_code==422
    assert 'sk-SYNTHETIC' not in out.text

def test_scenario_only_public_input(browser):
    value=browser.get('/api/scenarios/software-01-b').json()
    assert set(value)=={'case_id','group_input','title'}


def test_source_status_has_no_model_call_or_key_material(browser,monkeypatch):
    def fail(*a,**k):raise AssertionError('Unexpected HTTP')
    monkeypatch.setattr('requests.Session.post',fail)
    monkeypatch.setenv('TIMELY_API_KEY','tgpt_s_SYNTHETIC_STATUS_SECRET')
    monkeypatch.setenv('WEMEET_DEV_RELOAD','1')
    response=browser.get('/api/code-status')
    value=response.json()
    assert value['reload_mode']=='AUTO' and value['source_kind']=='WORKSPACE'
    assert not value['reload_required'] and not value['adapter_reload_required']
    assert set(value['modules'])=={'A','B','C','D','E.coach','E.draft','E.plan','F'}
    assert all(v['loaded']==v['disk'] for v in value['modules'].values())
    assert 'tgpt_' not in response.text and 'SECRET' not in response.text
