"""Real PostgreSQL ownership/recovery regressions; no paid provider calls."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import threading,json
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.persistence.errors import RevisionConflict
from wemeet_ai.persistence import journal_schema as j
from wemeet_ai.m0.runtime import code_result
from wemeet_ai.shared.contracts import Message
from _data import sample

@pytest.fixture
def storage(tmp_path):
    url=user_get('WEMEET_GOAL_DATABASE_URL')
    if not url:pytest.skip('PostgreSQL required')
    store=PostgresRecordStore(url,namespace='test:'+str(uuid4()))
    view=store.create(members=sample('A')['members'])
    key=Fernet.generate_key();recovery=EncryptedRecoveryStore(tmp_path/'spool',key)
    return store,view,ExecutionAdapter(store,recovery),key

def response():return code_result('A',{'reply':'현재 목표를 먼저 정리해보세요.'}).model_dump(mode='json')

def test_privacy_mask_does_not_corrupt_identifier_hashes():
    from wemeet_ai.shared.privacy import sanitize
    value='4c42131943404949dc52381c1f65c0102507321fd3d9b6a0e8dc3372959ab854'
    assert sanitize(value)==value
    assert sanitize('연락처 010-1234-5678 입니다')=='연락처 [REDACTED_PHONE] 입니다'

def test_duplicate_and_mismatched_action(storage):
    store,v,a,key=storage;calls=[]
    one=a.execute_and_store('A',v.record_id,'once',0,sample('A'),runner=lambda:(calls.append(1) or response()))
    two=a.execute_and_store('A',v.record_id,'once',0,sample('A'),runner=lambda:pytest.fail('duplicate call'))
    assert one==two and calls==[1] and one['persistence']['persistence_status']=='PERSISTED'
    with pytest.raises(RevisionConflict):a.execute_and_store('A',v.record_id,'once',0,{**sample('A'),'reference_date':'2027-01-01'})

def test_encrypted_save_only_recovery_after_new_adapter(storage,monkeypatch):
    store,v,a,key=storage;save=store.save_artifact
    monkeypatch.setattr(store,'save_artifact',lambda *args,**kw:(_ for _ in ()).throw(OSError('synthetic commit failure')))
    result=a.execute_and_store('A',v.record_id,'recover',0,sample('A'),runner=response)
    assert result['persistence']['retry_action']=='SAVE_ONLY' and not store.load(v.record_id).artifacts
    raw=next(a.recovery.path.glob('*.bin')).read_bytes()
    assert '현재 목표'.encode() not in raw
    monkeypatch.setattr(store,'save_artifact',save)
    fresh=ExecutionAdapter(PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'),namespace=store.namespace),EncryptedRecoveryStore(a.recovery.path,key))
    good=fresh.execute_and_store('A',v.record_id,'recover',0,sample('A'),runner=lambda:pytest.fail('must save only'))
    assert good['status']=='OK' and len(store.load(v.record_id).artifacts)==1 and not list(a.recovery.path.glob('*.bin'))

def test_concurrent_claim_has_single_provider_owner_and_no_db_lock(storage):
    store,v,a,key=storage;entered=threading.Event();release=threading.Event();calls=[]
    def runner():
        calls.append(1);entered.set();assert release.wait(10);return response()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(a.execute_and_store,'A',v.record_id,'parallel',0,sample('A'),runner=runner)
        assert entered.wait(10)
        second=pool.submit(a.execute_and_store,'A',v.record_id,'parallel',0,sample('A'),runner=lambda:pytest.fail('duplicate'))
        assert second.result(10)['persistence']['persistence_status']=='RUNNING'
        # A different connection writes while the model is waiting.
        store.append_message(v.record_id,Message(message_id='new',speaker_id='u1',text='새 조건입니다.',created_at='2026-11-05T12:00:00+09:00'),scope='PUBLIC')
        release.set();out=first.result(15)
    assert calls==[1] and out['persistence']['persistence_status']=='STALE'

def test_unknown_worker_never_automatically_recalled(storage):
    store,v,a,key=storage
    out=a.execute_and_store('A',v.record_id,'unknown',0,sample('A'),runner=lambda:(_ for _ in ()).throw(RuntimeError('process interrupted')))
    assert out['persistence']['persistence_status']=='UNKNOWN'
    again=a.execute_and_store('A',v.record_id,'unknown',0,sample('A'),runner=lambda:pytest.fail('unknown paid call'))
    assert again['persistence']['retry_action']=='REVIEW'

def test_app_cannot_delete_snapshot_or_create_schema(storage):
    store,v,a,key=storage
    with store.connect(readonly=True) as c:
        roles=c.execute(sa.text('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')).one()
        assert roles==(False,False,False)
    for statement in ['CREATE TABLE wemeet_goal.forbidden(x int)','DELETE FROM wemeet_goal.shared_goals WHERE false']:
        with pytest.raises(sa.exc.DBAPIError):
            with store.connect() as c:c.execute(sa.text(statement))

def test_private_journal_database_fk_rejects_unknown_owner(storage):
    store,v,a,key=storage
    with pytest.raises(sa.exc.DBAPIError):
        with store.connect() as c:
            c.execute(j.messages.insert().values(record_id=v.record_id,message_id='orphan',scope='PRIVATE',owner='not-a-member',body={}))

def test_second_python_process_cannot_claim_same_running_action(storage):
    import subprocess,sys,os
    store,v,a,key=storage
    code='''import os,sys,json
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
s=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'),namespace=sys.argv[2])
a=ExecutionAdapter(s,EncryptedRecoveryStore(sys.argv[3],os.environ['RECOVERY_TEST_KEY']))
def forbidden():raise AssertionError('duplicate provider call')
r=a.execute_and_store('A',sys.argv[1],'cross-process',0,json.loads(sys.stdin.read()),runner=forbidden)
assert r['persistence']['persistence_status']=='RUNNING'
print('single-owner-confirmed')
'''
    def runner():
        child=subprocess.run([sys.executable,'-c',code,v.record_id,store.namespace,str(a.recovery.path)],input=json.dumps(sample('A')),text=True,capture_output=True,
            env={**os.environ,'RECOVERY_TEST_KEY':key.decode()},timeout=20)
        assert child.returncode==0,child.stderr
        assert child.stdout.strip()=='single-owner-confirmed'
        return response()
    out=a.execute_and_store('A',v.record_id,'cross-process',0,sample('A'),runner=runner)
    assert out['persistence']['persistence_status']=='PERSISTED'

def test_database_unavailable_never_starts_ai(tmp_path):
    from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
    from sqlalchemy.exc import OperationalError
    # Intentionally closed loopback port, short connect timeout.
    store=PostgresRecordStore('postgresql+psycopg://unavailable@127.0.0.1:1/unavailable')
    adapter=ExecutionAdapter(store,EncryptedRecoveryStore(tmp_path/'offline',Fernet.generate_key()))
    with pytest.raises(OperationalError):adapter.execute_and_store('A','missing','offline',0,sample('A'),runner=lambda:pytest.fail('AI before storage'))

def test_standalone_changed_frequency_creates_new_goal_version(storage):
    from _data import plan
    from wemeet_ai.shared.contracts import Plan
    store,v,a,key=storage;payload=sample('D')
    def output():
        p=Plan.model_validate(plan());p.plan_version_id=str(uuid4())
        for t in p.tasks:t.task_id=str(uuid4())
        return code_result('D',p)
    one=a.execute_and_store('D',v.record_id,'first-goal',0,payload,runner=output)
    assert one['persistence']['persistence_status']=='PERSISTED'
    changed={**payload,'frequency_per_week':2 if payload.get('frequency_per_week')!=2 else 3}
    two=a.execute_and_store('D',v.record_id,'changed-frequency',store.load(v.record_id).revision,changed,runner=output)
    assert two['persistence']['persistence_status']=='PERSISTED'
    rows=store.goals.public_group(v.record_id)
    assert len(rows['shared_goals'])==2
    assert rows['group_plans'][0]['shared_goal_version_id']!=rows['group_plans'][1]['shared_goal_version_id']

def test_unconfirmed_runner_result_is_recorded_as_failure_not_save_retry_loop(storage):
    store,v,a,key=storage;payload=sample('E.plan');payload['profile']['confirmed']=False
    result=a.execute_and_store('E.contribution.confirm',v.record_id,'unconfirmed',0,payload,runner=lambda:code_result('E.contribution.confirm',payload))
    assert result['status']=='FAILED' and result['error']['code']=='RESULT_VALIDATION_FAILED'
    assert result['persistence']['persistence_status']=='PERSISTED'
    assert not store.goals.private_member(v.record_id,'u1')['member_goals']
