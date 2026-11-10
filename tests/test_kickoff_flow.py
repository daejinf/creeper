import pytest
from fastapi.testclient import TestClient
from tools.bench.server import app
from tools.bench import kickoff
from tools.bench.records import repository
from wemeet_ai.m0 import Runtime
from _fixture import FixtureClient
from _data import sample


@pytest.fixture
def flow(tmp_path,monkeypatch):
    monkeypatch.setenv('WEMEET_RECORD_DB',str(tmp_path/'flow.sqlite3'))
    transport=FixtureClient()
    monkeypatch.setattr(kickoff,'runtime_for',lambda gid:Runtime(transport))
    with TestClient(app) as client:
        group=client.post('/api/kickoff/groups',json={'title':'공동 목표 검토','members':sample('A')['members']}).json()
        url='/api/kickoff/groups/'+group['group_id']
        for message in sample('A')['conversation']:
            client.post(url+'/messages',json={'message_id':message['message_id'],'member_id':message['speaker_id'],'text':message['text']})
        yield client,url,transport


def reviewed(flow):
    client,url,transport=flow
    result=client.post(url+'/review',json={'action_id':'review'}).json()
    assert result['result']['status']=='OK',result
    updates={k:f['value'] for k,f in result['group']['review']['draft']['fields'].items()}
    updates['roles']={'u1':'참여 설계','u2':'진행 문서'}
    out=client.post(url+'/review-fields',json={'action_id':'accept','field':'*','updates':updates,'actor_member_id':'u1'})
    assert out.json()['result']['status']=='OK',out.json()
    return client,url,transport


def confirmed(flow):
    client,url,transport=reviewed(flow)
    out=client.post(url+'/confirm',json={'action_id':'confirm'}).json()
    assert out['result']['status']=='OK',out
    assert out['group']['review']['confirmed_goal']
    return client,url,transport

def test_review_storage_failure_returns_save_only_not_revision_conflict(flow,monkeypatch):
    from tools.bench.records import repository
    client,url,transport=flow;repo=repository();original=repo.save_artifact
    monkeypatch.setattr(repo,'save_artifact',lambda *a,**k:(_ for _ in ()).throw(OSError('synthetic DB commit failure')))
    failed=client.post(url+'/review',json={'action_id':'storage-failure'})
    assert failed.status_code==503 and failed.json()['persistence']['retry_action']=='SAVE_ONLY'
    before=len(transport.calls);monkeypatch.setattr(repo,'save_artifact',original)
    resumed=client.post(url+'/review',json={'action_id':'storage-failure'}).json()
    assert resumed['result']['status']=='OK' and len(transport.calls)==before+1  # Only C; B saved from encrypted response.


def test_review_confirm_restore_and_no_paid_confirmation(flow):
    client,url,transport=confirmed(flow)
    calls=len(transport.calls)
    assert calls==2  # B and C only.
    again=client.post(url+'/review',json={'action_id':'review'}).json()
    assert again['group']['review']['confirmed_goal']
    assert client.post(url+'/confirm',json={'action_id':'confirm'}).json()['result']['status']=='OK'
    assert len(transport.calls)==calls
    assert client.get(url).json()['review']['confirmed_goal']['roles']['u1']=='참여 설계'


def test_changed_analysis_is_not_hidden_by_current_draft(flow,monkeypatch):
    from wemeet_ai.m0 import versions
    from wemeet_ai.m0 import runtime as runtime_module
    client,url,transport=confirmed(flow)
    original=versions.loaded_revision
    new_revision=lambda task:'changed-analysis' if task=='B' else original(task)
    monkeypatch.setattr(versions,'loaded_revision',new_revision)
    monkeypatch.setattr(runtime_module,'loaded_revision',new_revision)
    monkeypatch.setattr(versions,'reload_required',lambda task:False)
    monkeypatch.setattr(runtime_module,'reload_required',lambda task:False)
    state=client.get(url).json()['review']
    assert state['code_status']=='CURRENT' and state['analysis_code_status']=='OUTDATED'
    assert state['confirmed_goal']  # Human decisions remain intact.
    calls=len(transport.calls)
    out=client.post(url+'/review',json={'action_id':'new-analysis-code'}).json()
    assert out['result']['status']=='OK' and len(transport.calls)==calls+2
    assert out['group']['review']['analysis_code_status']=='CURRENT'
    assert out['group']['review']['draft']['fields']['roles']['value']['u1']=='참여 설계'


def test_unreviewed_draft_cannot_confirm_and_bad_edit_preserved(flow):
    client,url,_=flow
    out=client.post(url+'/review',json={'action_id':'draft'}).json()
    before=out['group']['review']['draft']
    assert client.post(url+'/confirm',json={'action_id':'early'}).json()['result']['status']=='NEEDS_INPUT'
    bad=client.post(url+'/review-fields',json={'action_id':'bad','field':'start_date','updates':{'start_date':'bad-date'},'actor_member_id':'u1'}).json()
    assert bad['result']['status']=='NEEDS_INPUT'
    assert bad['group']['review']['draft']==before


def test_new_public_message_invalidates_confirmation_without_losing_personal(flow):
    client,url,transport=confirmed(flow)
    first=client.post(url+'/members/u1/personal/coach',json={'action_id':'first'}).json()
    assert first['result']['status']=='OK'
    client.post(url+'/messages',json={'message_id':'changed','member_id':'u1','text':'마감일을 바꾸고 싶어요.'})
    assert client.get(url).json()['review']['stale']
    personal=client.get(url+'/members/u1/personal').json()
    assert personal['needs_review'] and personal['messages']
    calls=len(transport.calls)
    assert client.post(url+'/members/u1/personal/coach',json={'action_id':'blocked'}).json()['result']['status']=='NEEDS_INPUT'
    assert len(transport.calls)==calls


def test_private_buttons_deduplicate_and_never_enter_public_or_other_owner(flow):
    client,url,transport=confirmed(flow)
    personal_url=url+'/members/u1/personal'
    out=client.post(personal_url+'/coach',json={'action_id':'personal-1'}).json()
    assert out['result']['status']=='OK',out
    question=out['personal']['result']['data']['state']['question']
    answer={'action_id':'answer','message_id':'private-answer','question_id':question['question_id'],'option_id':question['options'][0]['option_id']}
    out=client.post(personal_url+'/coach',json=answer).json()
    assert out['result']['status']=='OK',out
    assert out['personal']['result']['data']['state']['fields']['current_level']['value']=='일부 경험'
    calls=len(transport.calls)
    assert client.post(personal_url+'/coach',json=answer).json()['result']==out['result']
    assert len(transport.calls)==calls
    assert client.post(personal_url+'/coach',json={**answer,'action_id':'duplicate'}).status_code==409
    assert 'private-answer' not in client.get(url).text
    assert client.get(url+'/members/u2/personal').json()['messages']==[]
    assert client.get(url+'/members/not-a-member/personal').status_code==422
    assert out['personal']['request']['context']['role']=='참여 설계'
    assert out['personal']['request']['owner_member_id']=='u1'


def test_private_failure_keeps_answer(flow,monkeypatch):
    from wemeet_ai.m0 import AIError
    client,url,transport=confirmed(flow)
    def broken(*a,**kw):raise AIError('TIMEOUT','Synthetic timeout',True)
    monkeypatch.setattr(transport,'complete_json',broken)
    out=client.post(url+'/members/u1/personal/coach',json={'action_id':'timeout','message_id':'saved','text':'내 비공개 제약은 SYNTHETIC_ONLY 입니다.'}).json()
    assert out['result']['status']=='FAILED'
    assert any(m['message_id']=='saved' for m in out['personal']['messages'])
    assert 'SYNTHETIC_ONLY' not in client.get(url).text
