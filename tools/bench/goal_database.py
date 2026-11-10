"""Bridge old conversation records to the explicit PostgreSQL goal model.

Historical records are copied, never deleted or silently approved. This module
contains no AI calls; the independent core also works without this adapter.
"""
import os
from datetime import datetime
from functools import lru_cache
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router=APIRouter(prefix='/api/kickoff/groups')
_sync_failures={}

@lru_cache
def configured(url):
    from wemeet_ai.persistence import GoalRepository
    return GoalRepository(url)

def goal_repository():
    url=os.getenv('WEMEET_GOAL_DATABASE_URL')
    if not url:raise RuntimeError('GOAL_DATABASE_NOT_CONFIGURED')
    return configured(url)

def context_matches(goal,context,owner=None):
    fields=('goal','start_date','end_date','completion_criteria','verification_method','privacy_scope','milestones')
    if any(goal.get(k)!=context.get(k) for k in fields):return False
    return not owner or goal.get('roles',{}).get(owner)==context.get('role')

def sync_record(group_id):
    from .records import repository
    return repository().repair_projection(group_id)


def sync_safely(group_id):
    if not os.getenv('WEMEET_GOAL_DATABASE_URL'):return {'status':'NOT_CONFIGURED','backend':'POSTGRESQL'}
    try:
        result=sync_record(group_id);_sync_failures.pop(group_id,None);record_sync_attempt(group_id,'SAVED');return result
    except Exception:
        # No raw exception/DSN/profile in browser responses or server logs.
        _sync_failures[group_id]=True
        try:record_sync_attempt(group_id,'FAILED')
        except Exception:pass
        return {'status':'FAILED','backend':'POSTGRESQL','error':'GOAL_DATABASE_SYNC_FAILED',
            'message':'대화 기록은 유지되지만 정식 목표 DB 저장은 완료되지 않았습니다. 다시 동기화해주세요.'}

def record_sync_attempt(group_id,status):
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy import func
    from wemeet_ai.persistence import schema as db
    with goal_repository().transaction() as c:
        c.execute(insert(db.sync_attempts).values(record_id=group_id,status=status,
            error_code='GOAL_DATABASE_SYNC_FAILED' if status=='FAILED' else None).on_conflict_do_update(
                index_elements=['record_id'],set_={'status':status,'error_code':'GOAL_DATABASE_SYNC_FAILED' if status=='FAILED' else None,'created_at':func.now()}))

def storage_status(group_id):
    if not os.getenv('WEMEET_GOAL_DATABASE_URL'):return {'status':'NOT_CONFIGURED','backend':'POSTGRESQL'}
    try:
        import sqlalchemy as sa
        from wemeet_ai.persistence import schema as db
        from wemeet_ai.persistence import journal_schema as journal
        repo=goal_repository()
        with repo.transaction() as c:
            count=lambda t:c.scalar(sa.select(sa.func.count()).select_from(t).where(t.c.group_id==group_id))
            head=c.execute(sa.select(db.heads).where(db.heads.c.group_id==group_id)).mappings().first()
            record=c.execute(sa.select(journal.records).where(journal.records.c.record_id==group_id)).mappings().first()
            operation=c.execute(sa.select(journal.operations).where(journal.operations.c.record_id==group_id).order_by(journal.operations.c.created_at.desc()).limit(1)).mappings().first()
            state='QUARANTINED' if record and record['quarantine_reason'] else 'SAVE_PENDING' if operation and operation['state']=='PERSIST_FAILED' else 'CHECK_REQUIRED' if operation and operation['state']=='UNKNOWN' else 'CONNECTED' if head else 'NOT_SYNCED'
            return {'status':state,'backend':'POSTGRESQL','atomic_storage':True,'record_revision':record['revision'] if record else None,
                'last_operation_state':operation['state'] if operation else None,
                'shared_goal_versions':count(db.shared_goals),'member_goal_versions':count(db.member_goals),
                'plan_versions':count(db.plans),'current_shared_goal_version_id':head['current_shared_goal_version_id'] if head else None}
    except Exception:return {'status':'UNAVAILABLE','backend':'POSTGRESQL','message':'목표 DB 연결·마이그레이션을 확인해주세요.'}

@router.get('/{gid}/goal-database')
def public_database(gid:str):
    from .records import repository
    try:
        repository().load(gid)  # Existing test group only, never arbitrary data.
        return {**goal_repository().public_group(gid),'storage_status':storage_status(gid)}
    except Exception:return JSONResponse({'error':{'code':'GOAL_DATABASE_UNAVAILABLE','message':'정식 목표 DB를 확인해주세요.'}},status_code=503)

@router.post('/{gid}/goal-database/sync')
def resync(gid:str):
    value=sync_safely(gid)
    return JSONResponse(value,status_code=200 if value['status']=='SAVED' else 503)

@router.get('/{gid}/members/{mid}/goal-database')
def private_database(gid:str,mid:str):
    from .kickoff_flow import member_view
    try:
        member_view(gid,mid)
        return goal_repository().private_member(gid,mid)
    except Exception:return JSONResponse({'error':{'code':'GOAL_DATABASE_UNAVAILABLE','message':'본인 목표 DB를 확인해주세요.'}},status_code=503)

class BaselineInput(BaseModel):
    actor_member_id:str=Field(min_length=1)

@router.post('/{gid}/goal-database/plans/{plan_id}/baseline')
def baseline(gid:str,plan_id:str,body:BaselineInput):
    try:
        from .kickoff_flow import member_view
        member_view(gid,body.actor_member_id)
        from .records import repository
        from wemeet_ai.persistence import GoalRepository
        with repository().transaction(gid) as (connection,row):
            return GoalRepository(connection=connection).confirm_baseline(gid,plan_id,body.actor_member_id)
    except ValueError:
        return JSONResponse({'error':{'code':'BASELINE_NOT_READY','message':'현재 목표·계획을 검토한 뒤 본인 계획을 확인해주세요.'}},status_code=409)
    except Exception:
        return JSONResponse({'error':{'code':'GOAL_DATABASE_UNAVAILABLE','message':'평가 기준 저장이 완료되지 않았습니다.'}},status_code=503)
