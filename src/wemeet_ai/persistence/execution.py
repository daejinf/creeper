"""Durable action ownership, unlocked model calls, atomic journal/domain commits."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import sqlalchemy as sa
from . import journal_schema as j
from .repository import digest
from .errors import RevisionConflict
from ..continuity import Artifact,prepare_module_input,source_tokens,input_key,input_signature,latest,artifact_key,stale_reasons,planning_dependencies
from ..m0.runtime import code_result
from ..shared.contracts import Error
from ..shared.privacy import sanitize

def checked_result(module,result):
    """A caller-supplied runner cannot smuggle unconfirmed typed snapshots."""
    if result.get('status')!='OK':return result
    try:
        if module=='C.confirm':
            from ..shared.contracts import ConfirmedGoal
            value=ConfirmedGoal.model_validate(result['data'])
            if not value.confirmed:raise ValueError('Unconfirmed common goal')
        elif module=='E.contribution.confirm':
            from ..m1.e_member_plan import Contribution,MemberProfile
            value=result['data']
            if not Contribution.model_validate(value['contribution']).confirmed or not MemberProfile.model_validate(value['profile']).confirmed:raise ValueError('Unconfirmed personal conditions')
    except (ValueError,KeyError,TypeError):
        invalid=code_result(module,error=Error(code='RESULT_VALIDATION_FAILED',message='확인되지 않았거나 형식이 잘못된 결과입니다. 해당 조건을 확인하고 새 동작으로 제출해주세요.',retryable=False)).model_dump(mode='json')
        invalid['meta']=result.get('meta',invalid['meta'])
        return invalid
    return result

def dependencies_for(module,view,payload,explicit=None):
    from ..cli import registry
    prepared=prepare_module_input(module,view) if module in registry() else None
    deps=dict(explicit if explicit is not None else view.input_dependencies.get(module,prepared.dependencies if prepared else {}))
    if module in {'C.review','C.confirm','E.review'}:
        previous=latest(view,{'C','C.review'} if module.startswith('C.') else {'E.coach','E.review'},payload.get('owner_member_id'))
        if previous:
            deps.update(previous.dependencies)
            if previous.module!=module:deps[artifact_key(previous.module,previous.scope,previous.owner_member_id)]=source_tokens(view)[artifact_key(previous.module,previous.scope,previous.owner_member_id)]
    roots={'A':['public_user_messages'],'B':['public_messages'],'E.coach':['private_user_messages:'+str(payload.get('owner_member_id'))],
        'E.draft':['private_user_messages:'+str(payload.get('owner_member_id'))]}.get(module,[])
    tokens=source_tokens(view)
    deps.update({k:tokens[k] for k in roots if k in tokens});deps[input_key(module,payload)]=input_signature(module,payload)
    return planning_dependencies(module,deps)

class ExecutionAdapter:
    def __init__(self,store,recovery):self.store=store;self.recovery=recovery

    def _info(self,op,state,revision=None,retry='NONE',entities=None):
        return {'operation_id':op['operation_id'],'record_revision':revision,'persistence_status':state,'retry_action':retry,'entity_ids':entities or {}}

    def _pending(self,op,state):
        result=code_result(op['module'],needed=['실행 중입니다. 같은 동작의 상태를 조회해주세요.' if state=='RUNNING' else '실행 결과 확인이 필요합니다. 자동으로 AI를 다시 호출하지 않습니다.']).model_dump(mode='json')
        result['persistence']=self._info(op,state,retry='POLL' if state=='RUNNING' else 'REVIEW')
        return result

    def execute_and_store(self,module,record_id,action_id,expected_revision,payload,owner_member_id=None,*,runner=None,runtime=None,fresh=False,dependencies=None):
        if not action_id:raise ValueError('action_id is required')
        payload=sanitize(payload);scope,owner=self.store.scope_for(module,payload)
        if owner_member_id is not None and owner_member_id!=owner:raise ValueError('실행 대상 소유자가 다릅니다.')
        signature=digest([module,payload,scope,owner]);op=None;claimed=False
        # Commit the claim and input BEFORE opening a provider connection.
        with self.store.transaction(record_id) as (c,row):
            old=self.store.find_action(record_id,action_id,connection=c)
            if old:
                if old.module!=module or old.request!=payload:raise RevisionConflict('같은 동작 ID의 입력이 다릅니다.')
                return old.response
            op=c.execute(sa.select(j.operations).where(j.operations.c.record_id==record_id,j.operations.c.action_id==action_id)).mappings().first()
            if op:
                op=dict(op)
                if op['input_hash']!=signature:raise RevisionConflict('같은 동작 ID의 입력이 다릅니다.')
                if op['state'] in {'PERSISTED','STALE'}:return op['result']
                if op['state']=='RUNNING' and op['created_at']<datetime.now(timezone.utc)-timedelta(minutes=10):
                    c.execute(j.operations.update().where(j.operations.c.operation_id==op['operation_id']).values(state='UNKNOWN',error_code='WORKER_NOT_CONFIRMED',updated_at=sa.func.now()));op['state']='UNKNOWN'
            else:
                if row['quarantine_reason']:raise RevisionConflict('이관 보류 기록입니다. 원본 복구 또는 새 기록이 필요합니다.')
                if expected_revision is None:raise ValueError('새 동작에는 expected_revision이 필요합니다.')
                if expected_revision is not None and row['revision']!=expected_revision:raise RevisionConflict('실행 입력의 기록 버전이 바뀌었습니다.')
                revision=self.store.save_input(record_id,module,payload,dependencies=dependencies,connection=c)
                view=self.store.load(record_id,owner_member_id=owner or None,internal=scope=='SERVICE',connection=c)
                deps=dependencies_for(module,view,payload,dependencies)
                op={'record_id':record_id,'action_id':action_id,'operation_id':str(uuid4()),'module':module,'owner':owner,
                    'input_hash':signature,'payload':payload,'dependencies':deps,'started_revision':revision,'claim_token':str(uuid4()),'state':'RUNNING'}
                c.execute(j.operations.insert().values(**op));claimed=True
        packet=self.recovery.get(op['operation_id'])
        if packet is not None:return self._finish(packet)
        if not claimed:return self._pending(op,'UNKNOWN' if op['state'] in {'UNKNOWN','PERSIST_FAILED'} else 'RUNNING')
        try:
            view=self.store.load(record_id,owner_member_id=owner or None,internal=scope=='SERVICE')
            if stale_reasons(view,op['dependencies']):
                result=code_result(module,needed=['입력의 근거가 바뀌었습니다. 최신 입력으로 검토해주세요.'])
            elif runner is not None:result=runner()
            else:
                from ..cli import execute
                result=execute(module,payload,runtime=runtime,fresh=fresh)
            if hasattr(result,'model_dump'):result=result.model_dump(mode='json')
            if hasattr(result,'body'):
                import json
                result=json.loads(result.body)
            packet={'record_id':record_id,'action_id':action_id,'operation_id':op['operation_id'],'claim_token':op['claim_token'],
                'module':module,'payload':payload,'scope':scope,'owner':owner,'dependencies':op['dependencies'],
                'started_revision':op['started_revision'],'result':sanitize(checked_result(module,result)),'created_at':datetime.now(timezone.utc).isoformat()}
            self.recovery.put(op['operation_id'],packet)
        except Exception:
            self._mark(op,'UNKNOWN','RESULT_NOT_DURABLE')
            return self._pending(op,'UNKNOWN')
        return self._finish(packet)

    def _mark(self,op,state,error):
        try:
            with self.store.connect() as c:c.execute(j.operations.update().where(j.operations.c.operation_id==op['operation_id'],
                j.operations.c.claim_token==op['claim_token'],j.operations.c.state.not_in(['PERSISTED','STALE'])).values(state=state,error_code=error,updated_at=sa.func.now()))
        except Exception:pass

    def _finish(self,packet):
        try:
            with self.store.transaction(packet['record_id']) as (c,row):
                op=c.execute(sa.select(j.operations).where(j.operations.c.operation_id==packet['operation_id'])).mappings().one()
                if op['record_id']!=packet['record_id'] or op['action_id']!=packet['action_id'] or op['claim_token']!=packet['claim_token'] or op['input_hash']!=digest([packet['module'],packet['payload'],packet['scope'],packet['owner']]):raise RevisionConflict('복구 입력의 소유권이 일치하지 않습니다.')
                if op['state'] in {'PERSISTED','STALE'}:result=op['result']
                else:
                    view=self.store.load(packet['record_id'],owner_member_id=packet['owner'] or None,internal=packet['scope']=='SERVICE',connection=c)
                    stale=bool(stale_reasons(view,packet['dependencies']))
                    result=dict(checked_result(packet['module'],packet['result']));state='STALE' if stale else 'PERSISTED'
                    data=result.get('data') or {};entities={k:data[k] for k in ('plan_version_id','previous_plan_version_id') if data.get(k)}
                    if packet['module']=='C.confirm' and result['status']=='OK' and not stale:entities['shared_goal_version_id']=packet['operation_id']
                    if packet['module']=='E.contribution.confirm' and result['status']=='OK' and not stale:entities['member_goal_version_id']=packet['operation_id']
                    result['persistence']=self._info(op,state,row['revision']+1,'REVIEW' if stale else 'NONE',entities)
                    result['persistence']['task_ids']=[t['task_id'] for t in data.get('tasks',[]) if t.get('task_id')]
                    artifact=Artifact(artifact_id=packet['operation_id'],action_id=packet['action_id'],module=packet['module'],scope=packet['scope'],
                        owner_member_id=packet['owner'] or None,request=packet['payload'],response=result,dependencies=packet['dependencies'],
                        created_at=packet['created_at'],input_stale=stale)
                    if result.get('status')=='OK' and data.get('reply') and packet['module'] in {'A','E.coach'} and not result.get('meta',{}).get('cache_hit'):
                        from ..shared.contracts import Message
                        prefix='coach:' if packet['module']=='A' else 'personal-coach:'
                        message=Message(message_id=prefix+result['meta']['run_id'],role='assistant',text=data['reply'],created_at=packet['created_at'])
                        self.store._messages(c,packet['record_id'],[message.model_dump(mode='json')],packet['scope'],packet['owner'],row['metadata']['members'])
                    revision=self.store.save_artifact(packet['record_id'],artifact,connection=c)
                    c.execute(j.operations.update().where(j.operations.c.operation_id==packet['operation_id']).values(state=state,result=result,error_code=None,updated_at=sa.func.now()))
        except Exception:
            self._mark(packet,'PERSIST_FAILED','ATOMIC_COMMIT_FAILED')
            result=code_result(packet['module'],error=Error(code='STORAGE_FAILED',message='AI 결과는 암호화해 보관했습니다. 같은 동작으로 저장만 다시 시도해주세요.',retryable=True)).model_dump(mode='json')
            result['meta']=packet['result'].get('meta',result['meta'])
            result['persistence']=self._info(packet,'PERSIST_FAILED',retry='SAVE_ONLY')
            result['unsaved_result']=packet['result']
            return result
        # A committed transaction remains successful even if spool cleanup fails.
        try:self.recovery.delete(packet['operation_id'])
        except OSError:pass
        return result

    def status(self,record_id,action_id):
        self.store.load(record_id)
        with self.store.connect(readonly=True) as c:
            row=c.execute(sa.select(j.operations).where(j.operations.c.record_id==record_id,j.operations.c.action_id==action_id)).mappings().first()
            if not row:return None
            result={k:row[k] for k in ('operation_id','module','state','started_revision','created_at','updated_at','error_code')}
            if result['state']=='RUNNING' and row['created_at']<datetime.now(timezone.utc)-timedelta(minutes=10):
                result.update(state='UNKNOWN',error_code='WORKER_NOT_CONFIRMED')
            return result
