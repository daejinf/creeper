import pytest
from wemeet_ai.m0 import Runtime,MemoryResultCache
from wemeet_ai.m0 import versions
from wemeet_ai.m0 import runtime as runtime_module
from wemeet_ai.m1.a_coaching import CoachRequest,CoachReply
from _data import sample
from test_m0 import Session,Response


def test_module_revisions_track_only_relevant_dependencies():
    sources={'__init__.py':'v1','m0/runtime.py':'m0','shared/contracts.py':'types',
        'm1/a_coaching/__init__.py':'a','m1/b_analysis/__init__.py':'b',
        'm1/d_group_plan/__init__.py':'d','m1/e_member_plan/__init__.py':'e','m1/f_validation/__init__.py':'f'}
    changed={**sources,'m1/b_analysis/__init__.py':'b-new'}
    assert versions.revision_for('A',sources)==versions.revision_for('A',changed)
    assert versions.revision_for('B',sources)!=versions.revision_for('B',changed)
    changed={**sources,'m1/f_validation/__init__.py':'f-new'}
    for task in ('D','E.plan','F'):assert versions.revision_for(task,sources)!=versions.revision_for(task,changed)
    assert versions.revision_for('E.coach',sources)==versions.revision_for('E.coach',changed)
    changed={**sources,'shared/contracts.py':'new-types'}
    for task in versions.TASKS:assert versions.revision_for(task,sources)!=versions.revision_for(task,changed)


def test_old_records_are_not_falsely_current(monkeypatch):
    monkeypatch.setattr(versions,'reload_required',lambda task:False)
    monkeypatch.setattr(versions,'loaded_revision',lambda task:'new-code')
    old={'status':'OK','meta':{'source_revision':'old-code'}}
    assert versions.result_code_status(old,'A')=='OUTDATED'
    assert not versions.reusable_result(old,'A')
    assert versions.result_code_status({'status':'OK','meta':{}},'A')=='UNTRACKED'
    assert versions.reusable_result({'status':'OK','meta':{'source_revision':'new-code'}},'A')


def test_reload_pending_blocks_paid_call(monkeypatch):
    monkeypatch.setattr(runtime_module,'reload_required',lambda task:True)
    client=type('Client',(),{'model':'never-called','base_url':'https://example.invalid'})()
    result=Runtime(client).run('A',CoachRequest.model_validate(sample('A')),CoachReply,'test',text=True)
    assert result.status=='FAILED' and result.error.code=='SOURCE_RELOAD_REQUIRED'
    assert result.meta.execution_source=='CODE' and result.meta.transport_attempts==0


def test_prompt_and_source_changes_do_not_reuse_success_cache(monkeypatch):
    from wemeet_ai.m0 import TimelyClient
    monkeypatch.setenv('TIMELY_API_KEY','tgpt_s_SYNTHETIC_ONLY_NOT_REAL_123456')
    monkeypatch.setenv('WEMEET_OFFLINE','0')
    session=Session([Response(content='one'),Response(content='two'),Response(content='three')])
    runtime=Runtime(TimelyClient(session),cache=MemoryResultCache(),scope_id='versions-test')
    request=CoachRequest.model_validate(sample('A'))
    first=runtime.run('A',request,CoachReply,'prompt one',text=True)
    cached=runtime.run('A',request,CoachReply,'prompt one',text=True)
    changed=runtime.run('A',request,CoachReply,'prompt two',text=True)
    monkeypatch.setattr(runtime_module,'loaded_revision',lambda task:'new-loaded-source')
    newer=runtime.run('A',request,CoachReply,'prompt two',text=True)
    assert cached.meta.cache_hit and len(session.calls)==3
    assert [r.data.reply for r in (first,changed,newer)]==['one','two','three']
    assert newer.meta.source_revision=='new-loaded-source'
