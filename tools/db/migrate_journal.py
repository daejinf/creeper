"""Read-only SQLite import, preserving original JSON and identifiers."""
import json,sqlite3,os,hashlib
from pathlib import Path
import sqlalchemy as sa
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore,GoalRepository
from wemeet_ai.persistence import journal_schema as j
ROOT=Path(__file__).resolve().parents[2]

def canonical(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def main():
    store=PostgresRecordStore(user_get('WEMEET_GOAL_ADMIN_DATABASE_URL'));report=[]
    source=ROOT/'runtime/records.sqlite3'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as old:
        old.row_factory=sqlite3.Row
        for r in old.execute('SELECT * FROM records'):
            rid=r['id'];meta=json.loads(r['metadata'])
            messages=[dict(x) for x in old.execute('SELECT * FROM messages WHERE record_id=? ORDER BY rowid',(rid,))]
            drafts=[dict(x) for x in old.execute('SELECT * FROM drafts WHERE record_id=?',(rid,))]
            artifacts=[dict(x) for x in old.execute('SELECT * FROM artifacts WHERE record_id=? ORDER BY seq',(rid,))]
            with store.connect() as c:
                if c.scalar(sa.select(j.records.c.record_id).where(j.records.c.record_id==rid)):
                    report.append({'record_id':rid,'status':'ALREADY_IMPORTED'});continue
                GoalRepository(connection=c).save_group(rid,r['title'],meta['members'])
                from datetime import datetime
                c.execute(j.records.insert().values(record_id=rid,title=r['title'],metadata=meta,revision=r['revision'],updated_at=datetime.fromisoformat(r['updated_at'])))
                for m in messages:c.execute(j.messages.insert().values(record_id=rid,message_id=m['id'],scope=m['scope'],owner=m['owner'],body=json.loads(m['body'])))
                for d in drafts:c.execute(j.drafts.insert().values(record_id=rid,module=d['module'],scope=d['scope'],owner=d['owner'],body=json.loads(d['body']),pending=d['pending'],dependencies=json.loads(d.get('dependencies') or '{}')))
                for a in artifacts:
                    body=json.loads(a['body'])
                    c.execute(j.artifacts.insert().values(record_id=rid,action_id=a['action_id'],artifact_id=body['artifact_id'],module=body['module'],scope=a['scope'],owner=a['owner'],body=body))
                # Check exact raw JSON before any typed projection.
                checks=[]
                for table,rows in ((j.messages,messages),(j.drafts,drafts),(j.artifacts,artifacts)):
                    got=[x[0] for x in c.execute(sa.select(table.c.body).where(table.c.record_id==rid))]
                    checks.append(sorted(canonical(x) for x in got)==sorted(canonical(json.loads(x['body'])) for x in rows))
                if not all(checks):raise RuntimeError('Import hash mismatch')
                try:
                    if rid in {'d4740afd-6d7f-48b5-a99e-4bf3465aa99b','2478d76c-b35d-4696-9366-67ee110dc504'}:raise ValueError('Previously identified inconsistent legacy record')
                    with c.begin_nested():store._project(c,rid)
                    status='IMPORTED'
                except Exception:
                    c.execute(j.records.update().where(j.records.c.record_id==rid).values(quarantine_reason='LEGACY_RELATIONSHIP_REVIEW_REQUIRED'))
                    status='QUARANTINED'
                report.append({'record_id':rid,'status':status,'messages':len(messages),'inputs':len(drafts),'artifacts':len(artifacts),'content_hash_verified':True})
    source.chmod(0o444)
    (ROOT/'runtime/journal-migration.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'records':len(report),'imported':sum(x['status']=='IMPORTED' for x in report),'quarantined':sum(x['status']=='QUARANTINED' for x in report),'paid_http':0}))
if __name__=='__main__':main()
