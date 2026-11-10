"""SQLite test adapter. The installed AI core never imports this module."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from wemeet_ai.continuity import input_key, input_signature, fingerprint, Artifact, RecordView
from wemeet_ai.shared.contracts import Message
from wemeet_ai.shared.privacy import sanitize

from wemeet_ai.persistence.errors import RevisionConflict, RecordNotFound

def dumps(value):return json.dumps(sanitize(value),ensure_ascii=False,sort_keys=True)

class SQLiteRecordStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,title TEXT NOT NULL,revision INTEGER NOT NULL,metadata TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(record_id TEXT NOT NULL,id TEXT NOT NULL,scope TEXT NOT NULL,owner TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(record_id,id),FOREIGN KEY(record_id) REFERENCES records(id));
            CREATE TABLE IF NOT EXISTS drafts(record_id TEXT NOT NULL,module TEXT NOT NULL,scope TEXT NOT NULL,owner TEXT NOT NULL,body TEXT NOT NULL,pending TEXT NOT NULL,PRIMARY KEY(record_id,module,scope,owner),FOREIGN KEY(record_id) REFERENCES records(id));
            CREATE TABLE IF NOT EXISTS artifacts(seq INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,action_id TEXT NOT NULL,scope TEXT NOT NULL,owner TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(record_id,action_id),FOREIGN KEY(record_id) REFERENCES records(id));
            ''')
            if 'dependencies' not in {r['name'] for r in db.execute('PRAGMA table_info(drafts)')}:
                db.execute("ALTER TABLE drafts ADD COLUMN dependencies TEXT NOT NULL DEFAULT '{}'")
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()
    @contextmanager
    def transaction(self,record_id,expected_revision=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM records WHERE id=?',(record_id,)).fetchone()
            if row is None:raise RecordNotFound(record_id)
            if expected_revision is not None and row['revision']!=expected_revision:raise RevisionConflict('새 버전이 저장됐습니다. 기록을 다시 열어 변경 내용을 합쳐주세요.')
            yield db,row
    def create(self,title='새 목표 대화',*,members=None,reference_date=None,timezone_name='Asia/Seoul',execution_mode='LIVE'):
        from datetime import date
        rid=str(uuid4());metadata={'schema_version':'1.0','members':members or [{'member_id':'u1','nickname':'나'}],
            'reference_date':reference_date or date.today().isoformat(),'timezone':timezone_name,'selected_module':'A','execution_mode':execution_mode}
        RecordView(record_id=rid,title=title,**metadata)
        with self.connect() as db:db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(rid,title,0,dumps(metadata),datetime.now(timezone.utc).isoformat()))
        return self.load(rid)
    def list(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT id,title,revision,updated_at FROM records ORDER BY updated_at DESC')]
    def add_member(self,record_id,member):
        """Append a demo participant without replacing other members or history."""
        from wemeet_ai.shared.contracts import Member
        member=Member.model_validate(member)
        with self.transaction(record_id) as (db,row):
            metadata=json.loads(row['metadata'])
            existing=next((m for m in metadata['members'] if m['member_id']==member.member_id),None)
            if existing:
                if existing!=member.model_dump():raise RevisionConflict('Member ID already used')
                return row['revision']
            metadata['members'].append(member.model_dump())
            RecordView(record_id=record_id,title=row['title'],**metadata)
            return self._touch(db,record_id,row['revision'],metadata)
    def _touch(self,db,record_id,revision,metadata=None,title=None):
        fields=['revision=?','updated_at=?'];values=[revision+1,datetime.now(timezone.utc).isoformat()]
        if metadata is not None:fields.append('metadata=?');values.append(dumps(metadata))
        if title is not None:fields.append('title=?');values.append(title)
        db.execute('UPDATE records SET '+','.join(fields)+' WHERE id=?',(*values,record_id));return revision+1
    @staticmethod
    def scope_for(module,payload):
        if module.startswith('E.'):
            owner=payload.get('owner_member_id')
            if not owner:raise ValueError('개인 대상 ID가 필요합니다.')
            return 'PRIVATE',owner
        if module.startswith('F'):
            return 'SERVICE',''
        if set(payload)&{'owner_member_id','private_notes','member_profile','member_profiles','private_conversation'}:
            raise ValueError('개인 자료를 공개 모듈 입력으로 저장할 수 없습니다.')
        return 'PUBLIC',''
    def _messages(self,db,record_id,messages,scope,owner):
        for raw in messages:
            msg=Message.model_validate(raw)
            if scope=='PRIVATE' and msg.role=='user' and msg.speaker_id!=owner:raise ValueError('다른 사람의 개인 발언입니다.')
            body=dumps(msg.model_dump(mode='json'))
            old=db.execute('SELECT * FROM messages WHERE record_id=? AND id=?',(record_id,msg.message_id)).fetchone()
            if old:
                if old['scope']!=scope or old['owner']!=owner or old['body']!=body:raise RevisionConflict('같은 메시지 ID의 내용/공개 범위를 바꿀 수 없습니다.')
            else:db.execute('INSERT INTO messages VALUES(?,?,?,?,?)',(record_id,msg.message_id,scope,owner,body))
    def append_message(self,record_id,message,*,scope,owner_member_id=None,expected_revision=None):
        if scope not in {'PUBLIC','PRIVATE'} or scope=='PRIVATE' and not owner_member_id or scope=='PUBLIC' and owner_member_id:raise ValueError('공개 범위와 소유자를 확인해주세요.')
        owner=owner_member_id or ''
        with self.connect() as db:
            old=db.execute('SELECT body,scope,owner FROM messages WHERE record_id=? AND id=?',(record_id,message.message_id)).fetchone()
            if old and old['body']==dumps(message.model_dump(mode='json')) and old['scope']==scope and old['owner']==owner:
                return db.execute('SELECT revision FROM records WHERE id=?',(record_id,)).fetchone()[0]
        with self.transaction(record_id,expected_revision) as (db,row):
            old=db.execute('SELECT body,scope,owner FROM messages WHERE record_id=? AND id=?',(record_id,message.message_id)).fetchone()
            if old and old['body']==dumps(message.model_dump(mode='json')) and old['scope']==scope and old['owner']==owner:return row['revision']
            self._messages(db,record_id,[message.model_dump(mode='json')],scope,owner)
            return self._touch(db,record_id,row['revision'])
    def save_input(self,record_id,module,payload,*,pending='',expected_revision=None,metadata=None,dependencies=None):
        scope,owner=self.scope_for(module,payload)
        with self.transaction(record_id,expected_revision) as (db,row):
            old_meta=json.loads(row['metadata'])
            if metadata:
                for key in ['members','reference_date','timezone','selected_module','execution_mode','selected_owner_member_id']:
                    if key in metadata:old_meta[key]=metadata[key]
                if old_meta.get('execution_mode') not in {'LIVE','FIXTURE'}:raise ValueError('Invalid execution mode')
            if payload.get('members'):old_meta['members']=payload['members']
            if payload.get('reference_date'):old_meta['reference_date']=payload['reference_date']
            self._messages(db,record_id,payload.get('conversation',[]),scope,owner)
            old=db.execute('SELECT * FROM drafts WHERE record_id=? AND module=? AND scope=? AND owner=?',(record_id,module,scope,owner)).fetchone()
            lineage=dumps(dependencies if dependencies is not None else json.loads(old['dependencies']) if old else {})
            if old and old['body']==dumps(payload) and old['pending']==sanitize(pending) and old['dependencies']==lineage and old_meta==json.loads(row['metadata']):return row['revision']
            db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?) ON CONFLICT(record_id,module,scope,owner) DO UPDATE SET body=excluded.body,pending=excluded.pending,dependencies=excluded.dependencies',
                (record_id,module,scope,owner,dumps(payload),sanitize(pending),lineage))
            return self._touch(db,record_id,row['revision'],old_meta)
    def save_artifact(self,record_id,artifact:Artifact,*,expected_revision=None):
        expected_scope,expected_owner=self.scope_for(artifact.module,artifact.request)
        if artifact.scope!=expected_scope or (artifact.owner_member_id or '')!=expected_owner:raise ValueError('모듈 결과의 공개 범위가 일치하지 않습니다.')
        with self.connect() as db:
            old=db.execute('SELECT body FROM artifacts WHERE record_id=? AND action_id=?',(record_id,artifact.action_id)).fetchone()
            if old:
                prior=Artifact.model_validate_json(old['body'])
                if prior.module!=artifact.module or prior.request!=artifact.request or prior.response!=artifact.response:raise RevisionConflict('같은 동작 ID에 다른 내용을 저장할 수 없습니다.')
                return db.execute('SELECT revision FROM records WHERE id=?',(record_id,)).fetchone()[0]
        with self.transaction(record_id,expected_revision) as (db,row):
            old=db.execute('SELECT body FROM artifacts WHERE record_id=? AND action_id=?',(record_id,artifact.action_id)).fetchone()
            if old:
                prior=Artifact.model_validate_json(old['body'])
                if prior.request!=artifact.request or prior.response!=artifact.response:raise RevisionConflict('중복 동작의 내용이 다릅니다.')
                return row['revision']
            artifact=artifact.model_copy(update={'revision':row['revision']+1,'created_at':artifact.created_at or datetime.now(timezone.utc).isoformat()})
            db.execute('INSERT INTO artifacts(record_id,action_id,scope,owner,body) VALUES(?,?,?,?,?)',
                (record_id,artifact.action_id,artifact.scope,expected_owner,dumps(artifact.model_dump(mode='json'))))
            return self._touch(db,record_id,row['revision'])
    def find_action(self,record_id,action_id):
        with self.connect() as db:
            row=db.execute('SELECT body FROM artifacts WHERE record_id=? AND action_id=?',(record_id,action_id)).fetchone()
            return Artifact.model_validate_json(row['body']) if row else None
    def load(self,record_id,*,owner_member_id=None,internal=False):
        owner=owner_member_id or ''
        with self.connect() as db:
            row=db.execute('SELECT * FROM records WHERE id=?',(record_id,)).fetchone()
            if row is None:raise RecordNotFound(record_id)
            metadata=json.loads(row['metadata'])
            public=[];private=[];private_by_owner={};internal_tokens={}
            for m in db.execute('SELECT * FROM messages WHERE record_id=? ORDER BY rowid',(record_id,)):
                if m['scope']=='PUBLIC':public.append(json.loads(m['body']))
                else:
                    value=json.loads(m['body']);private_by_owner.setdefault(m['owner'],[]).append(value)
                    if owner and m['owner']==owner:private.append(value)
            if internal:
                for mid,values in private_by_owner.items():
                    internal_tokens['private_user_messages:'+mid]=fingerprint([v for v in values if v['role']=='user'])
                    internal_tokens['private_messages:'+mid]=fingerprint(values)
            inputs={};pending={};dependencies={}
            for d in db.execute('SELECT * FROM drafts WHERE record_id=?',(record_id,)):
                if internal and d['scope']=='PRIVATE':
                    value=json.loads(d['body']);internal_tokens[input_key(d['module'],value)]=input_signature(d['module'],value)
                if d['scope']=='PUBLIC' or d['scope']=='PRIVATE' and d['owner']==owner or d['scope']=='SERVICE' and internal:
                    inputs[d['module']]=json.loads(d['body']);pending[d['module']]=d['pending']
                    dependencies[d['module']]=json.loads(d['dependencies'])
            artifacts=[]
            for a in db.execute('SELECT * FROM artifacts WHERE record_id=? ORDER BY seq',(record_id,)):
                if a['scope']=='PUBLIC' or a['scope']=='PRIVATE' and (a['owner']==owner or internal) or a['scope']=='SERVICE' and internal:
                    artifacts.append(json.loads(a['body']))
            return RecordView(record_id=record_id,title=row['title'],revision=row['revision'],**metadata,
                conversation=public,owner_member_id=owner_member_id,private_conversation=private,
                inputs=inputs,input_dependencies=dependencies,pending=pending,artifacts=artifacts,internal=internal,service_source_tokens=internal_tokens)
