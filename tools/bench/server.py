"""Local test tool only. Optional local record adapter; no accounts, approval service, or workflow engine."""
import json
import os
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from wemeet_ai.cli import registry, execute
from wemeet_ai.m0 import Runtime, MemoryResultCache
from wemeet_ai.shared.privacy import sanitize
from wemeet_ai import __version__
from .revisions import status as revision_status

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples"))
from _data import sample
from .records import router as record_router, saved_call
from .kickoff import router as kickoff_router
from .kickoff_flow import router as kickoff_flow_router
from .kickoff_plans import router as kickoff_plans_router
from .goal_database import router as goal_database_router

app = FastAPI(title="WE·MEET 독립 모듈 테스트", version=__version__)
app.include_router(record_router)
app.include_router(kickoff_router)
app.include_router(kickoff_flow_router)
app.include_router(kickoff_plans_router)
app.include_router(goal_database_router)
cache = MemoryResultCache()

@app.get("/")
def index(): return FileResponse(Path(__file__).with_name("index.html"), headers={"Cache-Control":"no-store"})

@app.get("/assets/{name}")
def asset(name: str):
    if name not in {"bench.js", "bench.css", "continuity.js", "kickoff.js", "kickoff-flow.js", "kickoff-plans.js", "kickoff-version.js", "kickoff-goals.js", "kickoff.css"}: return JSONResponse({"error":"NOT_FOUND"},status_code=404)
    return FileResponse(Path(__file__).with_name(name),headers={"Cache-Control":"no-store"})

@app.get("/api/config")
def config(): return {"version":__version__, "provider":"Timely", "configured":bool(os.getenv("TIMELY_API_KEY")), "model":os.getenv("TIMELY_MODEL","openai/gpt-5.6-luna"), "live_allowed":os.getenv("WEMEET_OFFLINE")!="1"}

@app.get('/api/code-status')
def code_status():return revision_status()

@app.get("/api/example/{module}")
def example(module: str):
    if module not in registry(): return JSONResponse({"error":"UNKNOWN_MODULE"},status_code=404)
    return sample(module)

@app.get("/api/scenarios")
def scenarios():
    path=ROOT/"evaluations"/"scenarios.jsonl"
    return [{"case_id":x["case_id"],"title":x["title"],"domain":x["domain"],"kind":x["kind"]} for x in map(json.loads,path.read_text(encoding="utf-8").splitlines())]

@app.get("/api/scenarios/{case_id}")
def scenario(case_id: str):
    for row in (ROOT/"evaluations"/"scenarios.jsonl").read_text(encoding="utf-8").splitlines():
        data=json.loads(row)
        if data["case_id"]==case_id:
            # Public sample retrieval never exposes the author's private intake or judge answers.
            return {"case_id":case_id,"group_input":data["group_input"],"title":data["title"]}
    return JSONResponse({"error":"NOT_FOUND"},status_code=404)

@app.post("/api/run/{module}")
def run(module: str, body: dict):
    if body.get('record_id'):
        bare={k:v for k,v in body.items() if k not in {'record_id','action_id','expected_revision'}}
        bare['scope_id']=body['record_id']
        return saved_call(module,body,lambda:run(module,bare))
    if module not in registry(): return JSONResponse({"error":{"code":"UNKNOWN_MODULE"}},status_code=404)
    try:
        mode=body.get("mode","LIVE")
        if mode not in {"LIVE","FIXTURE"}: raise ValueError("Unknown mode")
        if mode=="FIXTURE":
            from _fixture import FixtureClient
            runtime=Runtime(FixtureClient(),cache=cache,scope_id=str(body.get("scope_id","local")))
        else: runtime=Runtime(cache=cache,scope_id=str(body.get("scope_id","local")))
        result=execute(module,body["input"],runtime=runtime,fresh=bool(body.get("fresh",False)))
        return sanitize(result.model_dump(mode="json"))
    except (ValidationError,ValueError,KeyError) as exc:
        fields=[{"path":".".join(map(str,e["loc"])),"message":e["msg"]} for e in exc.errors()] if isinstance(exc,ValidationError) else []
        return JSONResponse({"status":"FAILED","error":{"code":"INPUT_INVALID","message":"입력 항목의 형식과 필수값을 확인해주세요."},"fields":fields},status_code=422)

@app.post("/api/review")
def review(body:dict):
    if body.get('record_id'):
        bare={k:v for k,v in body.items() if k not in {'record_id','action_id','expected_revision'}}
        return saved_call('C.review',{**body,'input':{'draft':body['draft'],'field':body['field'],'value':body.get('value'),'edit':body.get('edit',False)}},lambda:review(bare))
    from wemeet_ai.m1.c_goal_draft import GoalDraft, review_field
    try:
        return sanitize(review_field(GoalDraft.model_validate(body["draft"]),body["field"],value=body.get("value"),edit=body.get("edit",False),actor_id="local-review").model_dump(mode="json"))
    except (ValidationError,ValueError,KeyError): return JSONResponse({"status":"FAILED","error":{"message":"수정 형식을 확인해주세요."}},status_code=422)

@app.post("/api/confirm")
def confirm(body:dict):
    if body.get('record_id'):
        bare={k:v for k,v in body.items() if k not in {'record_id','action_id','expected_revision'}}
        return saved_call('C.confirm',{**body,'input':{'draft':body['draft'],'members':body['members']}},lambda:confirm(bare))
    from wemeet_ai.m1.c_goal_draft import GoalDraft, confirm_goal
    from wemeet_ai.shared.contracts import Member
    try:
        return sanitize(confirm_goal(GoalDraft.model_validate(body["draft"]),members=[Member.model_validate(m) for m in body["members"]]).model_dump(mode="json"))
    except (ValidationError,ValueError,KeyError): return JSONResponse({"status":"FAILED","error":{"message":"확인할 항목을 검토해주세요."}},status_code=422)

@app.post("/api/member-condition")
def member_condition(body:dict):
    if body.get('record_id'):
        bare={k:v for k,v in body.items() if k not in {'record_id','action_id','expected_revision'}}
        payload={**bare,'owner_member_id':body['state']['owner_member_id']}
        return saved_call('E.review',{**body,'input':payload},lambda:member_condition(bare))
    from wemeet_ai.m1.e_member_plan import MemberState, update_member_condition
    try:
        return sanitize(update_member_condition(MemberState.model_validate(body['state']),body['field'],value=body.get('value'),status=body.get('status','KNOWN'),actor_id='local-member-review').model_dump(mode='json'))
    except (ValidationError,ValueError,KeyError): return JSONResponse({'status':'FAILED','error':{'message':'개인 조건 수정 형식을 확인해주세요.'}},status_code=422)
