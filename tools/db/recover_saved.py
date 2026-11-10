"""Administrator-operated save-only recovery. Never calls a model."""
import argparse,json
from pathlib import Path
import sqlalchemy as sa
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.persistence import journal_schema as j
ROOT=Path(__file__).resolve().parents[2]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'))
    adapter=ExecutionAdapter(store,EncryptedRecoveryStore(ROOT/'runtime/recovery',user_get('WEMEET_RECOVERY_KEY')))
    with store.connect(readonly=True) as c:
        ops=[dict(r) for r in c.execute(sa.select(j.operations).join(j.records,j.records.c.record_id==j.operations.c.record_id).where(j.records.c.namespace=='default',j.operations.c.state=='PERSIST_FAILED')).mappings()]
    results=[]
    for op in ops:
        row={'operation_id':op['operation_id'],'module':op['module'],'state':op['state']}
        if args.apply:
            value=adapter.execute_and_store(op['module'],op['record_id'],op['action_id'],0,op['payload'],runner=lambda:(_ for _ in ()).throw(AssertionError('save-only recovery cannot call AI')))
            row.update(state=value['persistence']['persistence_status'],result_status=value['status'],error=(value.get('error') or {}).get('code'))
        results.append(row)
    print(json.dumps({'operations':results,'actual_http':0}))
if __name__=='__main__':main()
