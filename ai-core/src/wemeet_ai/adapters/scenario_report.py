"""Execution statistics and contrastive review queues, never synthetic quality scores."""
from collections import Counter
import json
from ..scenarios import get_scenario


def behavior(result):
    values=[]
    for run in result['response'].get('runs',[]):
        d=run.get('data') or {}
        if 'reply' in d and not d.get('proposals'):values.append({'reply':d['reply'],'kind':d.get('kind')})
        elif 'proposals' in d:values.append([p.get('field_updates') for p in d['proposals']])
        elif 'fields' in d:values.append({k:v.get('value',v.get('candidates')) for k,v in d['fields'].items()})
        elif 'state' in d:values.append({k:v.get('value') for k,v in d['state'].get('fields',{}).items()})
        elif 'public_plan' in d:values.append(d['public_plan'].get('weekly_targets'))
        else:values.append({k:d[k] for k in ('goal','start_date','end_date','weekly_outline') if k in d})
    return values


def evaluation_report(store,job_id):
    job=store.job(job_id);results={};summaries={}
    for item in job['items']:
        if item['result_id']:results[item['case_id'],item['configuration']]=store.result(item['result_id'])
    for configuration in job['spec']['configurations']:
        rows=[r for (c,k),r in results.items() if k==configuration];times=sorted(r['metrics'].get('latency_ms',0) for r in rows)
        errors=Counter(run['error']['code'] for r in rows for run in r['response'].get('runs',[]) if run.get('error'))
        summaries[configuration]={'executed_combinations':len(rows),'api_completed':sum(r['status']=='DONE' for r in rows),
            'code_checks_passed':sum(r['checks'].get('passed',False) for r in rows),'errors':dict(errors),
            'referenced_case_ids':sorted({ref for r in rows for run in r['response'].get('runs',[]) for ref in run.get('meta',{}).get('reference_case_ids',[])}),
            'p95_execution_ms':times[min(len(times)-1,int(len(times)*.95))] if times else None,'semantic_quality':'NOT_YET_HUMAN_REVIEWED'}
    contrasts=[]
    for case_id in job['spec']['case_ids']:
        case=get_scenario(case_id)
        if case.kind!='BASE':continue
        for configuration in job['spec']['configurations']:
            base=results.get((case_id,configuration));variant=results.get((case.paired_case_id,configuration))
            contrasts.append({'family_id':case.family_id,'configuration':configuration,'base_case_id':case_id,'variant_case_id':case.paired_case_id,
                'comparison':case.comparison,'status':'NEEDS_HUMAN_REVIEW' if base and variant else 'NOT_BOTH_EXECUTED',
                'outputs_differ':behavior(base)!=behavior(variant) if base and variant else None,
                'base_result_id':base['id'] if base else None,'variant_result_id':variant['id'] if variant else None})
    return {'job_id':job_id,'counts':store.counts(),'progress':job['counts'],'usage':job['usage'],'configurations':summaries,'contrastive_pairs':contrasts,
        'limitations':['출력 차이 자체는 올바른 적응이나 품질 향상의 증거가 아닙니다.','API 성공과 코드 검사 통과, 사람의 의미적 품질 검토는 서로 다릅니다.',
            '승인 참고 풀이 비어 있으면 참고 효과를 측정할 수 없습니다.','작성된 이전 단계 입력을 사용하는 C/E/PLAN은 전체 워크플로의 성공률을 뜻하지 않습니다.']}
