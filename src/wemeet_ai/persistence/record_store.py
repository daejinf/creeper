"""Canonical journal. Every write locks one record; no SQLite fallback."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone,date
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from . import journal_schema as j
from .repository import GoalRepository
from .errors import RevisionConflict,RecordNotFound
from .record_rules import scope_for
from ..continuity import Artifact,RecordView,input_key,input_signature,fingerprint
from ..shared.contracts import Member,Message
from ..shared.privacy import sanitize

def now():return datetime.now(timezone.utc)

class PostgresRecordStore:
    scope_for=staticmethod(scope_for)

    def __init__(self,url=None,*,connection=None,namespace='default'):
        self.goals=GoalRepository(url,connection=connection)
        self.engine=self.goals.engine;self.connection=connection;self.namespace=namespace

    @contextmanager
    def connect(self,*,connection=None,readonly=False):
        c=connection if connection is not None else self.connection
        if c is not None:yield c;return
        with self.engine.connect() as c:
            if readonly:
                c=c.execution_options(isolation_level='REPEATABLE READ')
                c.execute(sa.text('SET TRANSACTION READ ONLY'))
            else:c.begin()
            try:
                yield c
                c.commit()
            except BaseException:c.rollback();raise

    @contextmanager
    def transaction(self,record_id,expected_revision=None,*,connection=None):
        with self.connect(connection=connection) as c:
            row=c.execute(sa.select(j.records).where(j.records.c.record_id==record_id,j.records.c.namespace==self.namespace).with_for_update()).mappings().first()
            if not row:raise RecordNotFound(record_id)
            if expected_revision is not None and row['revision']!=expected_revision:raise RevisionConflict('기록 버전이 바뀌었습니다. 최신 입력으로 다시 확인해주세요.')
            yield c,row

    def _touch(self,c,row,metadata=None):
        values={'revision':row['revision']+1,'updated_at':now()}
        if metadata is not None:values['metadata']=metadata
        c.execute(j.records.update().where(j.records.c.record_id==row['record_id']).values(**values))
        return values['revision']

    def _project(self,c,record_id):
        row=c.execute(sa.select(j.records.c.quarantine_reason).where(j.records.c.record_id==record_id)).first()
        if row and row.quarantine_reason:return
        from .projector import project_record
        project_record(self,c,record_id)

    def create(self,title='새 목표 대화',*,members=None,reference_date=None,timezone_name='Asia/Seoul',execution_mode='LIVE'):
        record_id=str(uuid4());metadata={'schema_version':'1.0','members':members or [{'member_id':'u1','nickname':'나'}],
            'reference_date':reference_date or date.today().isoformat(),'timezone':timezone_name,'selected_module':'A','execution_mode':execution_mode}
        RecordView(record_id=record_id,title=title,**metadata)
        with self.connect() as c:
            GoalRepository(connection=c).save_group(record_id,title,metadata['members'])
            c.execute(j.records.insert().values(record_id=record_id,namespace=self.namespace,title=title,revision=0,metadata=metadata))
            self._project(c,record_id)
        return self.load(record_id)

    def list(self):
        with self.connect(readonly=True) as c:
            return [{'id':r.record_id,'title':r.title,'revision':r.revision,'updated_at':r.updated_at.isoformat()} for r in c.execute(sa.select(j.records).where(j.records.c.namespace==self.namespace).order_by(j.records.c.updated_at.desc()))]

    def add_member(self,record_id,member):
        member=Member.model_validate(member).model_dump()
        with self.transaction(record_id) as (c,row):
            metadata=deepcopy(row['metadata']);old=next((m for m in metadata['members'] if m['member_id']==member['member_id']),None)
            if old:
                if old!=member:raise RevisionConflict('구성원 ID의 내용이 다릅니다.')
                return row['revision']
            metadata['members'].append(member)
            revision=self._touch(c,row,metadata);self._project(c,record_id);return revision

    def _messages(self,c,record_id,messages,scope,owner,members):
        ids={m['member_id'] for m in members}
        if scope=='PRIVATE' and owner not in ids:raise ValueError('개인 대화의 구성원을 확인해주세요.')
        for raw in messages:
            message=Message.model_validate(raw);body=sanitize(message.model_dump(mode='json'))
            if message.role=='user' and (message.speaker_id not in ids or scope=='PRIVATE' and message.speaker_id!=owner):raise ValueError('대화 작성자/소유자가 일치하지 않습니다.')
            old=c.execute(sa.select(j.messages).where(j.messages.c.record_id==record_id,j.messages.c.message_id==message.message_id)).mappings().first()
            if old:
                if old['scope']!=scope or old['owner']!=owner or old['body']!=body:raise RevisionConflict('같은 메시지 ID의 내용·공개 범위를 바꿀 수 없습니다.')
            else:c.execute(j.messages.insert().values(record_id=record_id,message_id=message.message_id,scope=scope,owner=owner,body=body))

    def append_message(self,record_id,message,*,scope,owner_member_id=None,expected_revision=None):
        if scope not in {'PUBLIC','PRIVATE'} or (scope=='PRIVATE')!=bool(owner_member_id):raise ValueError('메시지 공개 범위를 확인해주세요.')
        with self.transaction(record_id) as (c,row):
            old=c.execute(sa.select(j.messages).where(j.messages.c.record_id==record_id,j.messages.c.message_id==message.message_id)).mappings().first()
            if old:
                self._messages(c,record_id,[message.model_dump(mode='json')],scope,owner_member_id or '',row['metadata']['members']);return row['revision']
            if expected_revision is not None and expected_revision!=row['revision']:raise RevisionConflict('기록 버전이 바뀌었습니다.')
            self._messages(c,record_id,[message.model_dump(mode='json')],scope,owner_member_id or '',row['metadata']['members'])
            revision=self._touch(c,row);self._project(c,record_id);return revision

    def save_input(self,record_id,module,payload,*,pending='',expected_revision=None,metadata=None,dependencies=None,connection=None):
        payload=sanitize(payload);scope,owner=scope_for(module,payload)
        with self.transaction(record_id,expected_revision,connection=connection) as (c,row):
            meta=deepcopy(row['metadata'])
            for k in ('reference_date','timezone','selected_module','execution_mode','selected_owner_member_id'):
                if metadata and k in metadata:meta[k]=metadata[k]
            provided=(metadata or {}).get('members',payload.get('members',[]))
            by_id={m['member_id']:m for m in meta['members']}
            for m in provided:by_id[m['member_id']]=Member.model_validate(m).model_dump()
            meta['members']=list(by_id.values())
            if payload.get('reference_date'):meta['reference_date']=payload['reference_date']
            RecordView(record_id=record_id,title=row['title'],**meta)
            GoalRepository(connection=c).save_group(record_id,row['title'],meta['members'])
            self._messages(c,record_id,payload.get('conversation',[]),scope,owner,meta['members'])
            key=(j.drafts.c.record_id==record_id,j.drafts.c.module==module,j.drafts.c.scope==scope,j.drafts.c.owner==owner)
            old=c.execute(sa.select(j.drafts).where(*key)).mappings().first()
            deps=dependencies if dependencies is not None else old['dependencies'] if old else {}
            if old and old['body']==payload and old['pending']==pending and old['dependencies']==deps and meta==row['metadata']:return row['revision']
            values=dict(record_id=record_id,module=module,scope=scope,owner=owner,body=payload,pending=sanitize(pending),dependencies=deps)
            c.execute(insert(j.drafts).values(**values).on_conflict_do_update(index_elements=['record_id','module','scope','owner'],set_={k:values[k] for k in ('body','pending','dependencies')}))
            revision=self._touch(c,row,meta);self._project(c,record_id);return revision

    def save_artifact(self,record_id,artifact,*,expected_revision=None,connection=None,project=True):
        artifact=Artifact.model_validate(artifact);scope,owner=scope_for(artifact.module,artifact.request)
        if artifact.scope!=scope or (artifact.owner_member_id or '')!=owner:raise ValueError('결과의 공개 범위가 일치하지 않습니다.')
        with self.transaction(record_id,connection=connection) as (c,row):
            old=self.find_action(record_id,artifact.action_id,connection=c)
            if old:
                if old.module!=artifact.module or old.request!=artifact.request or old.response!=artifact.response:raise RevisionConflict('같은 동작 ID에 다른 결과를 저장할 수 없습니다.')
                return row['revision']
            if expected_revision is not None and row['revision']!=expected_revision:raise RevisionConflict('기록 버전이 바뀌었습니다.')
            artifact=artifact.model_copy(update={'revision':row['revision']+1,'created_at':artifact.created_at or now().isoformat()})
            c.execute(j.artifacts.insert().values(record_id=record_id,action_id=artifact.action_id,artifact_id=artifact.artifact_id,module=artifact.module,
                scope=scope,owner=owner,body=sanitize(artifact.model_dump(mode='json'))))
            revision=self._touch(c,row)
            if project:self._project(c,record_id)
            return revision

    def find_action(self,record_id,action_id,*,connection=None):
        with self.connect(connection=connection,readonly=True) as c:
            row=c.execute(sa.select(j.artifacts.c.body).join(j.records,j.records.c.record_id==j.artifacts.c.record_id).where(j.artifacts.c.record_id==record_id,
                j.artifacts.c.action_id==action_id,j.records.c.namespace==self.namespace)).first()
            return Artifact.model_validate(row.body) if row else None

    def load(self,record_id,*,owner_member_id=None,internal=False,connection=None):
        owner=owner_member_id or ''
        with self.connect(connection=connection,readonly=True) as c:
            row=c.execute(sa.select(j.records).where(j.records.c.record_id==record_id,j.records.c.namespace==self.namespace)).mappings().first()
            if not row:raise RecordNotFound(record_id)
            members=row['metadata']['members']
            if owner and owner not in {m['member_id'] for m in members}:raise ValueError('해당 그룹 구성원이 아닙니다.')
            query=sa.select(j.messages).where(j.messages.c.record_id==record_id)
            if not internal:query=query.where(sa.or_(j.messages.c.scope=='PUBLIC',sa.and_(j.messages.c.scope=='PRIVATE',j.messages.c.owner==owner)))
            public=[];private=[];by_owner={};tokens={}
            for r in c.execute(query.order_by(j.messages.c.seq)).mappings():
                if r['scope']=='PUBLIC':public.append(r['body'])
                else:
                    by_owner.setdefault(r['owner'],[]).append(r['body'])
                    if r['owner']==owner:private.append(r['body'])
            if internal:
                for mid,values in by_owner.items():
                    tokens['private_user_messages:'+mid]=fingerprint([v for v in values if v['role']=='user']);tokens['private_messages:'+mid]=fingerprint(values)
            inputs={};pending={};deps={}
            for r in c.execute(sa.select(j.drafts).where(j.drafts.c.record_id==record_id)).mappings():
                if internal and r['scope']=='PRIVATE':tokens[input_key(r['module'],r['body'])]=input_signature(r['module'],r['body'])
                if r['scope']=='PUBLIC' or r['scope']=='PRIVATE' and r['owner']==owner or r['scope']=='SERVICE' and internal:
                    inputs[r['module']]=r['body'];pending[r['module']]=r['pending'];deps[r['module']]=r['dependencies']
            query=sa.select(j.artifacts).where(j.artifacts.c.record_id==record_id)
            if not internal:query=query.where(sa.or_(j.artifacts.c.scope=='PUBLIC',sa.and_(j.artifacts.c.scope=='PRIVATE',j.artifacts.c.owner==owner)))
            artifacts=[r.body for r in c.execute(query.order_by(j.artifacts.c.seq))]
            return RecordView(record_id=record_id,title=row['title'],revision=row['revision'],**row['metadata'],conversation=public,
                owner_member_id=owner_member_id,private_conversation=private,inputs=inputs,input_dependencies=deps,pending=pending,
                artifacts=artifacts,internal=internal,service_source_tokens=tokens)

    def repair_projection(self,record_id):
        with self.transaction(record_id) as (c,row):
            if row['quarantine_reason']:raise RevisionConflict('이관 보류 기록입니다. 원본 확인 후 명시적인 복구가 필요합니다.')
            self._project(c,record_id)
        return {'status':'SAVED','backend':'POSTGRESQL','source_revision':row['revision']}
