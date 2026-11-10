"""Thin record endpoints around the optional pure continuity API and PostgreSQL adapter."""
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from wemeet_ai.continuity import Artifact, prepare_module_input, input_key, input_signature, stale_reasons, latest, artifact_key, source_tokens
from wemeet_ai.shared.privacy import sanitize
from wemeet_ai.m0.runtime import code_result
from wemeet_ai.persistence import PostgresRecordStore
from wemeet_ai.persistence.errors import RevisionConflict,RecordNotFound

router=APIRouter(prefix='/api/records')
class StorageResponse(RuntimeError):
    def __init__(self,response):self.response=response
@lru_cache
def store(url):
    import sqlalchemy as sa
    repository=PostgresRecordStore(url)
    with repository.connect(readonly=True) as c:
        role=c.execute(sa.text('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')).one()
        if any(role):raise RuntimeError('App server requires a restricted database role')
    return repository

def repository():
    url=os.getenv('WEMEET_GOAL_DATABASE_URL')
    if not url:raise RuntimeError('PostgreSQL configuration required; SQLite fallback disabled')
    return store(url)

def failure(exc):
    if isinstance(exc,StorageResponse):return exc.response
    if isinstance(exc,RevisionConflict):code,status='REVISION_CONFLICT',409
    elif isinstance(exc,RecordNotFound):code,status='RECORD_NOT_FOUND',404
    elif isinstance(exc,(ValueError,ValidationError,KeyError)):code,status='INPUT_INVALID',422
    else:code,status='STORAGE_FAILED',503
    messages={'REVISION_CONFLICT':'다른 변경이 먼저 저장됐습니다. 기록을 다시 열고 합쳐주세요.',
        'RECORD_NOT_FOUND':'기록을 찾지 못했습니다.','INPUT_INVALID':'기록의 입력·소유자·형식을 확인해주세요.',
        'STORAGE_FAILED':'기록을 저장하지 못했습니다. 입력은 화면에 유지됩니다. 저장 완료가 아닙니다.'}
    return JSONResponse({'status':'FAILED','error':{'code':code,'message':messages[code]}},status_code=status)

@router.get('')
def list_records():
    try:return repository().list()
    except Exception as exc:return failure(exc)

@router.post('')
def create(body:dict):
    try:return repository().create(body.get('title','새 목표 대화'),members=body.get('members'),reference_date=body.get('reference_date'),execution_mode=body.get('execution_mode','LIVE')).model_dump(mode='json')
    except Exception as exc:return failure(exc)

@router.get('/{record_id}')
def get(record_id:str,owner_member_id:str|None=None,internal:bool=False):
    try:
        view=repository().load(record_id,owner_member_id=owner_member_id,internal=internal)
        result=view.model_dump(mode='json')
        result['artifact_states']={a.artifact_id:{'stale':bool(stale_reasons(view,a.dependencies)),'reasons':stale_reasons(view,a.dependencies)} for a in view.artifacts}
        result['input_states']={m:{'stale':bool(stale_reasons(view,d)),'reasons':stale_reasons(view,d)} for m,d in view.input_dependencies.items()}
        return sanitize(result)
    except Exception as exc:return failure(exc)

@router.post('/{record_id}/input')
def save_input(record_id:str,body:dict):
    try:
        revision=repository().save_input(record_id,body['module'],body['input'],pending=body.get('pending',''),expected_revision=body['expected_revision'],metadata=body.get('metadata'),dependencies=body.get('dependencies'))
        return {'revision':revision}
    except Exception as exc:return failure(exc)

@router.post('/{record_id}/prepare')
def prepare(record_id:str,body:dict):
    try:
        view=repository().load(record_id,owner_member_id=body.get('owner_member_id'),internal=bool(body.get('internal')))
        return prepare_module_input(body['module'],view,body.get('overrides')).model_dump(mode='json')
    except Exception as exc:return failure(exc)

def execution_adapter():
    from wemeet_ai.persistence import ExecutionAdapter,EncryptedRecoveryStore
    key=os.environ.get('WEMEET_RECOVERY_KEY')
    if not key:raise RuntimeError('WEMEET_RECOVERY_KEY is required')
    return ExecutionAdapter(repository(),EncryptedRecoveryStore(Path(__file__).resolve().parents[2]/'runtime'/'recovery',key))


def saved_call(module,body,runner):
    try:
        reused=repository().find_action(body['record_id'],body.get('action_id')) is not None
        result=execution_adapter().execute_and_store(module,body['record_id'],body.get('action_id'),
            body.get('expected_revision'),body['input'],runner=runner,dependencies=body.get('dependencies'))
        state=result.get('persistence',{});status=503 if state.get('persistence_status')=='PERSIST_FAILED' else 202 if state.get('persistence_status') in {'RUNNING','UNKNOWN'} else 200
        return JSONResponse(sanitize(result),status_code=status,headers={'X-Record-Revision':str(repository().load(body['record_id']).revision),'X-Record-Reused':str(reused).lower()})
    except Exception as exc:return failure(exc)

@router.get('/{record_id}/operations/{action_id}')
def operation_status(record_id:str,action_id:str):
    try:
        repository().load(record_id)
        return execution_adapter().status(record_id,action_id) or {'status':'NOT_FOUND'}
    except Exception as exc:return failure(exc)
