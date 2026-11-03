"""Local scenario gallery and explicit LIVE evaluation controls."""
from functools import lru_cache
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..scenarios import all_scenarios, get_scenario, audit_scenarios, DOMAINS
from ..scenario_inputs import entry_request
from .scenario_store import ScenarioStore
from .scenario_eval import EvaluationManager


class ReviewBody(BaseModel):
    status: str
    reviewer: str
    note: str = ''

class FeedbackBody(BaseModel):
    rating: str
    reviewer: str
    reason: str
    result_id: str | None = None

class JobBody(BaseModel):
    case_ids: list[str] | None = None
    configurations: list[str] = Field(default_factory=lambda:['baseline','improved'])

class StartBody(BaseModel):
    automatic: bool = False
    retry_failed: bool = False


def scenario_router(repository, client):
    router = APIRouter(prefix='/ai/v1/scenarios', tags=['Scenario library'])
    @lru_cache
    def stored(path): return ScenarioStore(path)
    def store():return stored(os.getenv('WEMEET_SCENARIO_DB_PATH',str(repository().path)))
    @lru_cache
    def managed(path): return EvaluationManager(stored(path), client)
    def manager():return managed(str(store().path))

    def invoke(fn):
        try: return fn()
        except KeyError: raise HTTPException(404,'SCENARIO_OR_RESULT_NOT_FOUND')
        except ValueError as exc: raise HTTPException(409,str(exc))

    @router.get('')
    def listing(q:str='',domain:str='',module:str='',kind:str='',difficulty:str='',review:str='',tag:str='',page:int=1):
        reviews=store().reviews(); cases=[]
        for c in all_scenarios():
            status=reviews.get(c.case_id,{}).get('status','UNREVIEWED')
            if domain and c.domain!=domain or module and c.entry_module!=module or kind and c.kind!=kind or difficulty and c.difficulty!=difficulty or review and status!=review or tag and tag not in c.tags: continue
            if q and q.lower() not in (' '.join([c.title,c.summary,c.purpose,*c.tags])).lower():continue
            cases.append({**c.model_dump(mode='json',include={'case_id','family_id','kind','paired_case_id','split','domain','title','summary','difficulty','entry_module','tags'}),'review_status':status})
        page=max(1,page)
        return {'counts':store().counts(),'domains':DOMAINS,'tags':sorted({tag for c in all_scenarios() for tag in c.tags}),'total_filtered':len(cases),'page':page,'page_size':20,'items':cases[(page-1)*20:page*20]}

    @router.get('/audit')
    def audit():return audit_scenarios()

    @router.get('/jobs')
    def jobs():return store().jobs()

    @router.post('/jobs')
    def create_job(body:JobBody):return invoke(lambda:manager().create(body.case_ids,body.configurations))

    @router.get('/jobs/{job_id}')
    def job(job_id:str):return invoke(lambda:store().job(job_id))

    @router.get('/jobs/{job_id}/report')
    def report(job_id:str):
        from .scenario_report import evaluation_report
        return invoke(lambda:evaluation_report(store(),job_id))

    @router.post('/jobs/{job_id}/start')
    def start(job_id:str,body:StartBody):return invoke(lambda:manager().start(job_id,body.automatic,body.retry_failed))

    @router.post('/jobs/{job_id}/pause')
    def pause(job_id:str):return invoke(lambda:manager().pause(job_id))

    @router.get('/results/{result_id}')
    def result(result_id:str):return invoke(lambda:store().result(result_id))

    @router.get('/{case_id}')
    def detail(case_id:str):
        def run():
            c=get_scenario(case_id)
            return {'scenario':c.model_dump(mode='json'),'pair':get_scenario(c.paired_case_id).model_dump(mode='json'),'review':store().reviews().get(case_id),'live_results':store().case_results(case_id)}
        return invoke(run)

    @router.get('/{case_id}/entry')
    def entry(case_id:str):
        def run():
            c=get_scenario(case_id);request=entry_request(c)
            return {'module':c.entry_module,'input':request.model_dump(mode='json'),'prerequisite_source':'AUTHORED_TEST_FIXTURE' if c.entry_module in {'C','E','PLAN'} else 'SCENARIO_CONVERSATION'}
        return invoke(run)

    @router.post('/{case_id}/review')
    def review(case_id:str,body:ReviewBody):return invoke(lambda:store().review(case_id,**body.model_dump()))

    @router.post('/{case_id}/feedback')
    def feedback(case_id:str,body:FeedbackBody):return invoke(lambda:store().feedback(case_id,**body.model_dump()))
    return router
