"""Read-only operating report; raw anonymous examples stay under runtime/."""
import json
from pathlib import Path
from tools.db.cutover import user_get
from wemeet_ai.persistence import PostgresRecordStore
ROOT=Path(__file__).resolve().parents[2]

def main():
    report=json.loads((ROOT/'runtime/unified-live.json').read_text(encoding='utf-8'))
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'))
    rows=[];totals={'http_completed':0,'tokens':0,'live_runs':0};seen=set()
    folder=ROOT/'runtime/anonymous-operating-examples';folder.mkdir(exist_ok=True)
    for name,group in report['groups'].items():
        view=store.load(group['record_id'],internal=True)
        public=store.goals.public_group(view.record_id)
        a=next(x for x in view.artifacts if x.module=='A');b=next(x for x in view.artifacts if x.module=='B')
        current=public['heads']['current_plans'];plans={p['plan_version_id']:p for p in public['group_plans']}
        item={'case':name,'record_id':view.record_id,'members':group['members'],'A':a.response['data']['reply'][:1700],
            'B_fields':{k:f['value'] for k,f in b.response['data']['facts'].items()},
            'group_tasks':[(t['title'],t['due_date']) for t in plans[current['GROUP']]['tasks']],'member_plans':[]}
        casefolder=folder/name;casefolder.mkdir(exist_ok=True)
        (casefolder/'group-public.json').write_text(json.dumps(public,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        for member in view.members:
            private=store.goals.private_member(view.record_id,member.member_id)
            plan=next(p for p in private['plans'] if p['plan_version_id']==current[member.member_id])
            profile=next(g['profile'] for g in private['member_goals'] if g['member_goal_version_id']==plan['member_goal_version_id'])
            item['member_plans'].append({'member':member.member_id,'minutes':profile['weekly_available_minutes'],
                'tasks':[(t['title'],t['estimated_minutes'],t['due_date']) for t in plan['tasks']]})
            (casefolder/(member.member_id+'-private.json')).write_text(json.dumps(private,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        for artifact in view.artifacts:
            meta=artifact.response.get('meta',{});run=meta.get('run_id')
            if meta.get('execution_source')=='LIVE' and run not in seen:
                seen.add(run);totals['live_runs']+=1;totals['http_completed']+=meta['transport_attempts'];totals['tokens']+=meta['usage']['total_tokens']
        rows.append(item)
    fault=json.loads((ROOT/'runtime/fault-drill.json').read_text(encoding='utf-8'))
    refined=json.loads((ROOT/'runtime/refined-live.json').read_text(encoding='utf-8')) if (ROOT/'runtime/refined-live.json').exists() else {'http_reserved':0}
    totals['http_reserved_including_unknown']=report['http_reserved']+fault['actual_http']+refined['http_reserved']
    totals['tokens']+=fault['meta']['usage']['total_tokens']
    totals['http_completed']+=fault['actual_http'];totals['live_runs']+=1
    interrupted={k:g.get('uncertain_operations',{}) for k,g in report['groups'].items() if g.get('uncertain_operations')}
    result={'cases':rows,'totals':totals,'interrupted_operations':interrupted,'confirmation':'ANONYMOUS_SIMULATED','reviewer':'AGENT_NOT_HUMAN'}
    (ROOT/'runtime/agent-review-input.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,default=str))
if __name__=='__main__':main()
