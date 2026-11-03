"""Read-only recheck of STORED parsed outputs. No provider; never rewrites LIVE records."""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from wemeet_ai.scenarios import get_scenario
from wemeet_ai.contracts import ConversationAnalysis
from wemeet_ai.analysis import merge_analysis

def recheck(db_path,output,job_id=None):
    db=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)
    job_id=job_id or db.execute('SELECT id FROM eval_jobs ORDER BY created_at DESC LIMIT 1').fetchone()[0]
    sql='SELECT i.case_id,i.configuration,r.status,r.response,r.checks FROM eval_items i JOIN eval_results r ON i.result_id=r.id WHERE i.job_id=? ORDER BY i.ordinal'
    rows=db.execute(sql,(job_id,)).fetchall()
    digest=lambda data:hashlib.sha256(json.dumps(data,ensure_ascii=False).encode()).hexdigest()
    before=digest(rows);stats=Counter();items=[]
    for case_id,configuration,status,response,checks in rows:
        data=json.loads(response);original_checks=json.loads(checks);stats['original_api_completed']+=status=='DONE';stats['original_code_passed']+=bool(original_checks.get('passed'))
        result={'case_id':case_id,'configuration':configuration,'original_status':status,'original_code_passed':original_checks.get('passed',False),'recheck':'NOT_REPLAYABLE_FROM_PARSED_RESULT'}
        if data.get('entry_module')=='B' and data.get('runs') and data['runs'][0].get('data'):
            original=ConversationAnalysis.model_validate(data['runs'][0]['data']);case=get_scenario(case_id)
            try:
                revised=merge_analysis(case.group_input,original);stats['analysis_replayed']+=1
                if case.expected['privacy_scope'] is None:
                    old=original.fields.get('privacy');new=revised.fields.get('privacy')
                    stats['missing_privacy_wrong_before']+=bool(old and old.candidates)
                    stats['missing_privacy_wrong_after']+=bool(new and new.candidates)
                result.update(recheck='REPLAY_STORED_PARSED_ANALYSIS',privacy_before=original.fields['privacy'].model_dump(mode='json'),privacy_after=revised.fields['privacy'].model_dump(mode='json'))
            except Exception as exc:result.update(recheck='REPLAY_CHECK_FAILED',error=type(exc).__name__)
        items.append(result)
    after=digest(db.execute(sql,(job_id,)).fetchall());db.close()
    report={'report_type':'REPLAY_STORED_PARSED_OUTPUT','checker_version':'v8-recheck-1','job_id':job_id,'actual_http_requests':0,'actual_tokens':0,
        'recorded_combinations':len(rows),'counts':dict(stats),'source_digest':before,'original_records_unchanged':before==after,'items':items,
        'limitations':['프롬프트 변경 후 실제 모델 품질을 검증한 결과가 아닙니다.','실패한 원본 모델 본문이 저장되지 않은 사례는 재현 불가로 남깁니다.','사람 검토·승인 통계는 변경하지 않습니다.']}
    Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'items','limitations'}},ensure_ascii=True));return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--job-id');p.add_argument('--output',default='runs/v8-stored-recheck.json');a=p.parse_args();recheck(a.db,a.output,a.job_id)
