"""Append audited metadata repair versions using existing LIVE output; zero HTTP.

Original immutable journal/snapshots are retained. No answer content is regenerated.
"""
import json
from pathlib import Path
from uuid import uuid4
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,ExecutionAdapter,EncryptedRecoveryStore
from wemeet_ai.m0.runtime import code_result
ROOT=Path(__file__).resolve().parents[2]
PATH=ROOT/'runtime/unified-live.json'

def main():
    report=json.loads(PATH.read_text(encoding='utf-8'));store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'))
    adapter=ExecutionAdapter(store,EncryptedRecoveryStore(ROOT/'runtime/recovery',user_get('WEMEET_RECOVERY_KEY')))
    for group in report['groups'].values():
        if group.get('database_checks')=='PASS' or group.get('blocked')!='GoalConflict':continue
        rid=group['record_id'];store.repair_projection(rid)
        for stage,entry in list(group['stages'].items()):
            if entry['status']!='OK' or entry.get('metadata_repair'):continue
            old=store.find_action(rid,entry['action_id'])
            if old.module not in {'D','E.contribution.confirm','E.plan'}:continue
            view=store.load(rid,owner_member_id=old.owner_member_id)
            # Only the original faulty deployment needs this repair.
            result=json.loads(json.dumps(old.response));data=result['data']
            if old.module in {'D','E.plan'}:
                data['previous_plan_version_id']=data['plan_version_id'];data['plan_version_id']=str(uuid4())
                ids={t['task_id']:str(uuid4()) for t in data['tasks']}
                for task in data['tasks']:
                    task['task_id']=ids[task['task_id']];task['dependencies']=[ids.get(v,v) for v in task['dependencies']]
            meta=code_result(old.module).model_dump(mode='json')['meta'];meta.update(execution_source='REPLAY',source_run_id=old.response['meta']['run_id'],cached_usage=old.response['meta']['usage'])
            result['meta']=meta;result.pop('persistence',None)
            result.setdefault('warnings',[]).append('저장 지문·관계 수정: 기존 LIVE 응답 재사용, 신규 HTTP 0회. 익명 모의 확인.')
            action=entry['action_id']+':metadata-repair'
            out=adapter.execute_and_store(old.module,rid,action,view.revision,old.request,runner=lambda r=result:r)
            if out['status']!='OK' or out['persistence']['persistence_status']!='PERSISTED':raise RuntimeError('Repair not committed')
            group['stages'][stage]={**entry,'action_id':action,'metadata_repair':True,'original_action_id':entry['action_id'],'meta':out['meta']}
            PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        store.repair_projection(rid)
    print('Appended immutable repair versions; original LIVE output preserved; HTTP 0.')
if __name__=='__main__':main()
