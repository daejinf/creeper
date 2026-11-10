"""Explicit isolated-local-cluster outage after one real model response."""
import os,json,subprocess
from pathlib import Path
from uuid import uuid4
from tools.db.cutover import user_get
from tools.db.bootstrap import BIN,DATA,start
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.m0 import Runtime,TimelyClient
from wemeet_ai.cli import execute
from examples._data import sample
ROOT=Path(__file__).resolve().parents[2]

def main():
    os.environ['TIMELY_API_KEY']=user_get('TIMELY_API_KEY') or ''
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'));payload=sample('D')
    view=store.create('운영 검증 · 실제 응답 후 DB 중단',members=payload['members'])
    recovery=EncryptedRecoveryStore(ROOT/'runtime/recovery',user_get('WEMEET_RECOVERY_KEY'));adapter=ExecutionAdapter(store,recovery)
    received={};calls=[];action=str(uuid4())
    class Budget:
        def reserve(self):
            if len(calls)>=4:raise RuntimeError('Fault drill HTTP cap 4')
            calls.append(1)
    def runner():
        result=execute('D',payload,runtime=Runtime(TimelyClient(budget=Budget())))
        if result.status!='OK':raise RuntimeError('Model did not produce a valid plan')
        received.update(result.model_dump(mode='json'))
        # Only this repository's dedicated development cluster is stopped.
        assert DATA.resolve()==(ROOT/'runtime/postgres-goals').resolve()
        subprocess.run([str(BIN/'pg_ctl.exe'),'stop','-D',str(DATA),'-m','fast','-w'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return result
    try:
        failed=adapter.execute_and_store('D',view.record_id,action,0,payload,runner=runner)
        assert failed['persistence']['persistence_status']=='PERSIST_FAILED'
        assert list(recovery.path.glob('*.bin'))
    finally:start()
    # A different adapter simulates a restarted web worker. No model is invoked.
    resumed=ExecutionAdapter(PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL')),recovery)
    good=resumed.execute_and_store('D',view.record_id,action,0,payload,runner=lambda:(_ for _ in ()).throw(AssertionError('paid retry forbidden')))
    assert good['status']=='OK' and good['persistence']['persistence_status']=='PERSISTED'
    assert good['data']==received['data'] and good['meta']['run_id']==received['meta']['run_id']
    plans=store.goals.public_group(view.record_id)['group_plans'];assert len(plans)==1
    assert plans[0]['plan_version_id']==good['data']['plan_version_id']
    assert {t['task_id'] for t in plans[0]['tasks']}=={t['task_id'] for t in good['data']['tasks']}
    report={'record_id':view.record_id,'operation_id':good['persistence']['operation_id'],'actual_http':len(calls),'meta':received['meta'],
        'outage_state':'PERSIST_FAILED','restart_state':'PERSISTED','same_run_plan_task_ids':True,'paid_retry':False,'tasks':len(plans[0]['tasks'])}
    (ROOT/'runtime/fault-drill.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='meta'}))
if __name__=='__main__':main()
