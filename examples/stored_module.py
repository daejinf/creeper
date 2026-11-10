"""From root: python -m examples.stored_module [--live]. Default: fixture, HTTP 0.

Requires .[database] and PostgreSQL environment configuration. Independent D:
no A/B/C history, no test web imports. SW confirmation is simulated input here.
"""
import os,json,argparse
from uuid import uuid4
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.m0 import Runtime
from examples._data import sample
from examples._fixture import FixtureClient

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    store=PostgresRecordStore(os.environ['WEMEET_GOAL_DATABASE_URL'])
    recovery=EncryptedRecoveryStore('runtime/recovery',os.environ['WEMEET_RECOVERY_KEY'])
    adapter=ExecutionAdapter(store,recovery);payload=sample('D')
    record=store.create('SW 독립 D 저장 예제',members=payload['members'],reference_date='2026-11-05')
    action=str(uuid4());runtime=Runtime() if args.live else Runtime(FixtureClient())
    result=adapter.execute_and_store('D',record.record_id,action,record.revision,payload,runtime=runtime)
    print(json.dumps({'request':payload,'response':result},ensure_ascii=False,indent=2))
    # Retry with THE SAME action and payload. This must not generate another plan.
    restored=ExecutionAdapter(PostgresRecordStore(os.environ['WEMEET_GOAL_DATABASE_URL']),recovery)
    same=restored.execute_and_store('D',record.record_id,action,0,payload,runtime=runtime)
    assert result==same
    print(json.dumps({'reused_same_operation':True,'record_id':record.record_id,'database':store.goals.public_group(record.record_id)},ensure_ascii=False,default=str,indent=2))
if __name__=='__main__':main()
