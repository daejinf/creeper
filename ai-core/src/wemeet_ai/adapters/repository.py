"""Local test persistence. The AI core never imports this module."""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from contextlib import contextmanager


class WorkspaceRepository(Protocol):
    def create(self,snapshot:dict)->dict: ...
    def get(self,workspace_id:str)->dict: ...
    def save(self,workspace_id:str,snapshot:dict,expected_revision:int)->dict: ...


class RevisionConflict(Exception):pass
_initialization_lock=threading.RLock()


def without_secrets(value):
    from ..privacy import sanitize
    return sanitize(value)


class SQLiteRepository:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with _initialization_lock, self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,snapshot TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions(workspace_id TEXT,kind TEXT,entity_key TEXT,digest TEXT,payload TEXT,created_at TEXT,UNIQUE(workspace_id,kind,entity_key,digest));
            CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,workspace_id TEXT,owner_id TEXT,meta TEXT,status TEXT);
            CREATE TABLE IF NOT EXISTS provider_calls(call_id TEXT PRIMARY KEY,run_id TEXT,request_id TEXT,model TEXT,usage TEXT,status INTEGER,host TEXT,called_at TEXT);
            CREATE TABLE IF NOT EXISTS baselines(id TEXT PRIMARY KEY,workspace_id TEXT,scope TEXT,owner_id TEXT,version_id TEXT,public_json TEXT,private_json TEXT,UNIQUE(workspace_id,scope,owner_id,version_id));
            PRAGMA user_version=1;
            ''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower()!='wal':db.execute('PRAGMA journal_mode=WAL')
            with db:yield db
        finally:db.close()
    def create(self,snapshot):
        key=str(uuid4());now=datetime.now(timezone.utc).isoformat()
        snapshot=without_secrets(snapshot)
        for run in snapshot.get('runs',[]):run['traces']=[]
        with self.connect() as db:
            db.execute('INSERT INTO workspaces VALUES(?,?,?,?)',(key,0,json.dumps(snapshot,ensure_ascii=False),now))
            self._record_versions(db,key,snapshot,now)
        return self.get(key)
    def list(self):
        with self.connect() as db:rows=db.execute('SELECT id,revision,updated_at,snapshot FROM workspaces ORDER BY updated_at DESC').fetchall()
        return [{'id':r['id'],'revision':r['revision'],'updated_at':r['updated_at'],'title':json.loads(r['snapshot']).get('spec',{}).get('goal') if json.loads(r['snapshot']).get('spec') else '진행 중인 목표 대화'} for r in rows]
    def get(self,key):
        with self.connect() as db:r=db.execute('SELECT * FROM workspaces WHERE id=?',(key,)).fetchone()
        if not r:raise KeyError(key)
        return {'id':r['id'],'revision':r['revision'],'snapshot':json.loads(r['snapshot']),'updated_at':r['updated_at']}
    def save(self,key,snapshot,expected_revision):
        snapshot=without_secrets(snapshot);now=datetime.now(timezone.utc).isoformat()
        # Persist actual conversation/decisions, but not raw provider traces by default.
        for run in snapshot.get('runs',[]):run['traces']=[]
        with self.connect() as db:
            changed=db.execute('UPDATE workspaces SET snapshot=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?',(json.dumps(snapshot,ensure_ascii=False),now,key,expected_revision)).rowcount
            if not changed:raise RevisionConflict(key)
            self._record_versions(db,key,snapshot,now)
        return self.get(key)
    def _record_versions(self,db,key,snapshot,now):
        entities={'group_chat':{'group':snapshot.get('groupChat',[])},'private_chat':snapshot.get('privateChats',{}),'profiles':snapshot.get('profiles',{}),
            'member_plans':snapshot.get('memberPlans',{}),'personal_drafts':snapshot.get('personalDrafts',{}),'decisions':{'group':snapshot.get('reviewedProposals',[])},
            'goals':{'spec':snapshot.get('spec'),'draft':snapshot.get('draft')},'group_plan':{'group':snapshot.get('group')},'validation':{'latest':snapshot.get('validation')}}
        for kind,records in entities.items():
            for entity,payload in records.items():
                if payload is None:continue
                encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(encoded.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?)',(key,kind,entity,digest,encoded,now))
    def record_run(self,workspace,owner,meta,status):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)',(meta['run_id'],workspace,owner,json.dumps(meta),status))
    def record_call(self,data):
        usage={}
        for name in ('prompt_tokens','completion_tokens','total_tokens'):
            try:usage[name]=max(0,int(data.get('usage',{}).get(name,0)))
            except (ValueError,TypeError,AttributeError):usage[name]=0
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO provider_calls VALUES(?,?,?,?,?,?,?,?)',(data['call_id'],data['run_id'],data.get('request_id'),data.get('model'),json.dumps(usage),data.get('status'),data.get('host'),data['called_at']))
    def calls(self):
        with self.connect() as db:
            rows=db.execute('SELECT c.*,r.meta,r.status AS run_status FROM provider_calls c LEFT JOIN runs r ON r.run_id=c.run_id ORDER BY called_at DESC LIMIT 200').fetchall()
            totals=db.execute('SELECT usage FROM provider_calls').fetchall()
        calls=[]
        for row in rows:
            c=dict(row);meta=json.loads(c.pop('meta') or '{}');c['usage']=json.loads(c['usage']);c['run_latency_ms']=meta.get('latency_ms');calls.append(c)
        return {'calls':calls,'display_limit':200,'http_attempts':len(totals),'total_tokens':sum(json.loads(r['usage']).get('total_tokens',0) for r in totals),'cost':None}
    def save_baseline(self,workspace,baseline):
        self.get(workspace)
        public={k:v for k,v in baseline.items() if k!='private_payloads'}
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO baselines VALUES(?,?,?,?,?,?,?)',(baseline['baseline_id'],workspace,baseline['scope'],baseline.get('owner_member_id') or '',baseline['plan_version_id'],json.dumps(public,ensure_ascii=False),json.dumps(baseline['private_payloads'],ensure_ascii=False)))
            row=db.execute('SELECT public_json FROM baselines WHERE workspace_id=? AND scope=? AND owner_id=? AND version_id=?',(workspace,baseline['scope'],baseline.get('owner_member_id') or '',baseline['plan_version_id'])).fetchone()
        return json.loads(row['public_json'])
    def baselines(self,workspace,owner=None):
        with self.connect() as db:rows=db.execute('SELECT public_json,private_json FROM baselines WHERE workspace_id=? ORDER BY rowid DESC',(workspace,)).fetchall()
        result=[]
        for row in rows:
            public=json.loads(row['public_json'])
            if owner:public['private_payloads']={owner:json.loads(row['private_json']).get(owner)}
            result.append(public)
        return result
