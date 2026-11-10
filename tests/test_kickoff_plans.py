import pytest
from test_kickoff_flow import flow, confirmed
from tools.bench.records import repository
from wemeet_ai.shared.contracts import Message


def personal_review(client,url,mid='u1',action='review-contribution'):
    body={'action_id':action,'title':'참여 방식 안내 준비','deliverable':'선택형 참여 안내 한 장',
        'completion_criteria':['선택 가능한 참여 방식 제시','강제 지목 없음'],
        'current_level':'일부 경험','weekly_available_minutes':30}
    out=client.post(url+'/members/'+mid+'/contribution/confirm',json=body)
    assert out.json()['result']['status']=='OK',out.json()
    return out.json()


def test_group_plan_without_any_personal_profile_and_reuse(flow):
    client,url,transport=confirmed(flow);calls=len(transport.calls)
    first=client.post(url+'/plan',json={'action_id':'group-plan'}).json()
    assert first['result']['status']=='OK',first
    assert len(transport.calls)==calls+1
    assert first['group']['group_plan']['validation']['data']['valid']
    version=first['result']['data']['plan_version_id']
    assert client.post(url+'/plan',json={'action_id':'group-plan'}).json()['result']['data']['plan_version_id']==version
    assert client.post(url+'/plan',json={'action_id':'same-input'}).json()['reused']
    assert len(transport.calls)==calls+1
    changed=client.post(url+'/plan',json={'action_id':'fresh','fresh':True}).json()['result']['data']
    assert changed['plan_version_id']!=version and changed['previous_plan_version_id']==version


def test_changed_core_does_not_reuse_old_database_plan(flow,monkeypatch):
    from wemeet_ai.m0 import versions,runtime as runtime_module
    client,url,transport=confirmed(flow)
    first=client.post(url+'/plan',json={'action_id':'old-source'}).json()['result']
    original=versions.loaded_revision
    new_revision=lambda task:'updated-'+original(task)
    monkeypatch.setattr(versions,'loaded_revision',new_revision)
    monkeypatch.setattr(versions,'reload_required',lambda task:False)
    monkeypatch.setattr(runtime_module,'loaded_revision',new_revision)
    monkeypatch.setattr(runtime_module,'reload_required',lambda task:False)
    calls=len(transport.calls)
    assert client.get(url).json()['group_plan']['code_status']=='OUTDATED'
    second=client.post(url+'/plan',json={'action_id':'new-source'}).json()['result']
    assert second['status']=='OK' and len(transport.calls)==calls+1
    assert second['data']['previous_plan_version_id']==first['data']['plan_version_id']
    assert second['meta']['source_revision']!=first['meta']['source_revision']
    assert client.get(url).json()['group_plan']['code_status']=='CURRENT'


def test_personal_missing_inputs_no_paid_call_and_draft_still_available(flow):
    client,url,transport=confirmed(flow);calls=len(transport.calls)
    out=client.post(url+'/members/u1/plan',json={'action_id':'early'}).json()
    assert out['result']['status']=='NEEDS_INPUT' and len(transport.calls)==calls
    draft=client.post(url+'/members/u1/contribution/draft',json={'action_id':'draft'}).json()
    assert draft['result']['status']=='OK'
    bad=client.post(url+'/members/u1/contribution/confirm',json={'action_id':'missing','title':'x','deliverable':'y','completion_criteria':['z']}).json()
    assert 'current_level' in bad['result']['needed_inputs'] and 'weekly_available_minutes' in bad['result']['needed_inputs']
    assert len(transport.calls)==calls+1


def test_member_plan_without_D_and_profile_sync_and_private_scope(flow):
    client,url,transport=confirmed(flow)
    reviewed=personal_review(client,url)
    assert reviewed['personal']['result']['data']['state']['fields']['weekly_available_minutes']['value']==30
    out=client.post(url+'/members/u1/plan',json={'action_id':'personal-plan'}).json()
    assert out['result']['status']=='OK',out
    assert out['personal']['planning']['plan']['validation']['data']['valid']
    assert not out['personal']['planning']['review_stale']
    version=out['result']['data']['plan_version_id'];calls=len(transport.calls)
    assert client.post(url+'/members/u1/plan',json={'action_id':'reuse'}).json()['reused']
    assert len(transport.calls)==calls
    assert version not in client.get(url).text
    assert client.get(url+'/members/u2/personal').json()['planning']['plan']['result'] is None
    new=client.post(url+'/members/u1/plan',json={'action_id':'new-version','fresh':True}).json()['result']['data']
    assert new['previous_plan_version_id']==version and new['plan_version_id']!=version


def test_changed_profile_cannot_reuse_old_confirmation_action(flow):
    client,url,_=confirmed(flow)
    first=personal_review(client,url)
    payload=first['result']['data']
    changed={'action_id':'review-contribution',**{k:v for k,v in payload['contribution'].items() if k!='confirmed'},
        'current_level':payload['profile']['current_level'],'weekly_available_minutes':15}
    assert client.post(url+'/members/u1/contribution/confirm',json=changed).status_code==409
    assert client.get(url+'/members/u1/personal').json()['planning']['review']['profile']['weekly_available_minutes']==30


def test_private_change_only_invalidates_own_plan(flow):
    client,url,transport=confirmed(flow)
    client.post(url+'/plan',json={'action_id':'D'})
    personal_review(client,url)
    client.post(url+'/members/u1/plan',json={'action_id':'E'})
    rid=url.rsplit('/',1)[-1]
    repository().append_message(rid,Message(message_id='changed-private',speaker_id='u1',text='가능 시간이 달라졌어요.',created_at='2026-11-04T12:00:00+09:00'),scope='PRIVATE',owner_member_id='u1')
    view=client.get(url+'/members/u1/personal').json()
    assert view['planning']['review_stale'] and view['planning']['plan']['stale']
    assert not client.get(url).json()['group_plan']['stale']
    calls=len(transport.calls)
    assert client.post(url+'/members/u1/plan',json={'action_id':'blocked'}).json()['result']['status']=='NEEDS_INPUT'
    assert len(transport.calls)==calls


def test_one_member_failure_preserves_group_and_other_members(flow,monkeypatch):
    import json
    from wemeet_ai.m0 import AIError
    client,url,transport=confirmed(flow)
    group=client.post(url+'/plan',json={'action_id':'group'}).json()['result']['data']
    personal_review(client,url,'u1','review-u1');personal_review(client,url,'u2','review-u2')
    original=transport.complete_json
    def complete(messages,*args,**kwargs):
        payload=json.loads(messages[1]['content'])
        if payload.get('owner_member_id')=='u1':raise AIError('TIMEOUT','Synthetic timeout',True)
        return original(messages,*args,**kwargs)
    monkeypatch.setattr(transport,'complete_json',complete)
    assert client.post(url+'/members/u1/plan',json={'action_id':'failed'}).json()['result']['status']=='FAILED'
    assert client.post(url+'/members/u2/plan',json={'action_id':'ok'}).json()['result']['status']=='OK'
    assert client.get(url).json()['group_plan']['result']['data']['plan_version_id']==group['plan_version_id']
