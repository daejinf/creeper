"""Scenario reviews and resumable evaluation bookkeeping; separate from AI core."""
import json
from datetime import datetime,timezone
from uuid import uuid4
from .repository import SQLiteRepository
from ..scenarios import all_scenarios, get_scenario, DATA_VERSION

def now():return datetime.now(timezone.utc).isoformat()

class ScenarioStore(SQLiteRepository):
    def __init__(self,path):
        super().__init__(path)
        with self.connect() as db:db.executescript('''
        CREATE TABLE IF NOT EXISTS scenario_reviews(case_id TEXT,data_version TEXT,status TEXT,reviewer TEXT,note TEXT,updated_at TEXT,PRIMARY KEY(case_id,data_version));
        CREATE TABLE IF NOT EXISTS scenario_feedback(id TEXT PRIMARY KEY,case_id TEXT,rating TEXT,reason TEXT,reviewer TEXT,result_id TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS eval_jobs(id TEXT PRIMARY KEY,spec TEXT,status TEXT,reason TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS eval_items(id TEXT PRIMARY KEY,job_id TEXT,ordinal INTEGER,case_id TEXT,configuration TEXT,fingerprint TEXT,status TEXT,result_id TEXT,lease_until TEXT,attempts INTEGER DEFAULT 0,cached INTEGER DEFAULT 0,UNIQUE(job_id,case_id,configuration));
        CREATE TABLE IF NOT EXISTS eval_results(id TEXT PRIMARY KEY,fingerprint TEXT,case_id TEXT,configuration TEXT,status TEXT,response TEXT,checks TEXT,metrics TEXT,created_at TEXT);
        CREATE INDEX IF NOT EXISTS eval_result_cache ON eval_results(fingerprint,status);
        ''')
    def reviews(self):
        with self.connect() as db:rows=db.execute('SELECT * FROM scenario_reviews WHERE data_version=?',(DATA_VERSION,)).fetchall()
        return {r['case_id']:dict(r) for r in rows}
    def review(self,case_id,status,reviewer,note):
        case=get_scenario(case_id)
        if status not in {'APPROVED','REJECTED','UNREVIEWED'} or not reviewer.strip():raise ValueError('검토 상태와 검토자를 확인해주세요.')
        if status=='APPROVED' and case.split!='REFERENCE':raise ValueError('평가 전용 사례는 참고 사례로 승인할 수 없습니다.')
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO scenario_reviews VALUES(?,?,?,?,?,?)',(case_id,DATA_VERSION,status,reviewer,note,now()))
        return self.reviews()[case_id]
    def feedback(self,case_id,rating,reason,reviewer,result_id=None):
        get_scenario(case_id)
        if result_id and self.result(result_id)['case_id']!=case_id:raise ValueError('선택한 결과가 이 사례의 실행이 아닙니다.')
        if rating not in {'GOOD','NEEDS_WORK','FAILED'} or not reviewer.strip() or not reason.strip():raise ValueError('평가와 이유·검토자가 필요합니다.')
        key=str(uuid4())
        with self.connect() as db:db.execute('INSERT INTO scenario_feedback VALUES(?,?,?,?,?,?,?)',(key,case_id,rating,reason,reviewer,result_id,now()))
        return {'feedback_id':key}
    def counts(self):
        with self.connect() as db:
            live=db.execute("SELECT COUNT(DISTINCT case_id) FROM eval_results WHERE json_extract(metrics,'$.http_attempts')>0").fetchone()[0]
            completed=db.execute("SELECT COUNT(DISTINCT case_id) FROM eval_results WHERE status='DONE' AND json_extract(metrics,'$.http_attempts')>0").fetchone()[0]
            approved=db.execute("SELECT COUNT(*) FROM scenario_reviews WHERE data_version=? AND status='APPROVED'",(DATA_VERSION,)).fetchone()[0]
            reviewed=db.execute("SELECT COUNT(DISTINCT case_id) FROM (SELECT case_id FROM scenario_reviews WHERE data_version=? AND status!='UNREVIEWED' UNION SELECT case_id FROM scenario_feedback)",(DATA_VERSION,)).fetchone()[0]
        return {'written':200,'checked':200,'base':100,'variants':100,'live_attempted_cases':live,'live_completed_cases':completed,'human_reviewed':reviewed,'reference_approved':approved}
    def create_job(self,spec,items):
        key=str(uuid4());stamp=now()
        with self.connect() as db:
            db.execute('INSERT INTO eval_jobs VALUES(?,?,?,?,?,?)',(key,json.dumps(spec,ensure_ascii=False),'PAUSED','실행 전',stamp,stamp))
            for ordinal,item in enumerate(items):db.execute('INSERT INTO eval_items(id,job_id,ordinal,case_id,configuration,fingerprint,status) VALUES(?,?,?,?,?,?,?)',(str(uuid4()),key,ordinal,item['case_id'],item['configuration'],item['fingerprint'],'PENDING'))
        return self.job(key)
    def jobs(self):
        with self.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM eval_jobs ORDER BY created_at DESC')]
        return [self.job(key,include_items=False) for key in ids]
    def job(self,key,include_items=True):
        with self.connect() as db:
            job=db.execute('SELECT * FROM eval_jobs WHERE id=?',(key,)).fetchone()
            if not job:raise KeyError(key)
            rows=db.execute('SELECT * FROM eval_items WHERE job_id=? ORDER BY ordinal',(key,)).fetchall()
            result_rows=db.execute('SELECT r.* FROM eval_results r JOIN eval_items i ON i.result_id=r.id WHERE i.job_id=?',(key,)).fetchall()
        counts={status:sum(r['status']==status for r in rows) for status in ('PENDING','RUNNING','DONE','FAILED')}
        metas=[json.loads(r['metrics']) for r in result_rows if not next((i['cached'] for i in rows if i['result_id']==r['id']),0)]
        result={**dict(job),'spec':json.loads(job['spec']),'counts':counts,'case_count':len({r['case_id'] for r in rows}),'combination_count':len(rows),'cached':sum(r['cached'] for r in rows),
            'usage':{k:sum(m.get(k,0) for m in metas) for k in ('total_tokens','http_attempts','latency_ms')},'semantic_review':'별도 사람 검토 필요'}
        if include_items:result['items']=[dict(r) for r in rows]
        return result
    def set_job(self,key,status,reason=''):
        with self.connect() as db:db.execute('UPDATE eval_jobs SET status=?,reason=?,updated_at=? WHERE id=?',(status,reason,now(),key))
    def result(self,key):
        with self.connect() as db:r=db.execute('SELECT * FROM eval_results WHERE id=?',(key,)).fetchone()
        if not r:raise KeyError(key)
        return {**dict(r),**{k:json.loads(r[k]) for k in ('response','checks','metrics')}}
    def case_results(self,case_id):
        with self.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM eval_results WHERE case_id=? ORDER BY created_at DESC LIMIT 20',(case_id,))]
        return [self.result(key) for key in ids]
