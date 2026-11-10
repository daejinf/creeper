"""Data checks only; never a model quality score."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from wemeet_ai.shared.contracts import ConversationInput

def audit():
    path=Path(__file__).with_name('scenarios.jsonl')
    cases=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    errors=[];by_id={c['case_id']:c for c in cases}
    if len(by_id)!=len(cases):errors.append('Duplicate case IDs')
    for c in cases:
        try:ConversationInput.model_validate(c['group_input'])
        except ValueError:errors.append(c['case_id']+': invalid conversation contract')
        pair=by_id.get(c['paired_case_id'])
        if not pair or pair['family_id']!=c['family_id'] or pair['split']!=c['split'] or pair['kind']==c['kind']:errors.append(c['case_id']+': pair/split mismatch')
        members={m['member_id'] for m in c['group_input']['members']}
        messages={m['message_id'] for m in c['group_input']['conversation']}
        if not set(c['expected'].get('evidence_refs',[]))<=messages:errors.append(c['case_id']+': unknown evidence')
        if {p['member_id'] for p in c['profiles']}!=members:errors.append(c['case_id']+': profile coverage')
        for owner,turns in c['private_conversations'].items():
            if owner not in members or any(t['role']=='user' and t['speaker_id']!=owner for t in turns):errors.append(c['case_id']+': private ownership')
            if {t['message_id'] for t in turns}&messages:errors.append(c['case_id']+': public/private message ID collision')
        if c['confirmed_fixture']['start_date']>=c['confirmed_fixture']['end_date']:errors.append(c['case_id']+': dates')
    return {'type':'DATA_CHECK_NOT_LIVE','cases':len(cases),'kinds':dict(Counter(c['kind'] for c in cases)),
            'domains':dict(Counter(c['domain'] for c in cases)),'splits':dict(Counter(c['split'] for c in cases)),
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'errors':errors,'passed':not errors}

if __name__=='__main__':
    report=audit();print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['passed'] else 1)
