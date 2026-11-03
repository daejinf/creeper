"""Local developer adapter; all AI behavior remains in the independent core."""
import os
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import json
import threading

from ..contracts import (
    AnalysisRequest, CoachReply, CoachRequest, ConversationAnalysis, DraftRequest,
    DraftWorkflow, GoalSpec, GoalSpecDraft, GroupPlan, HandoffPackage, HandoffRequest,
    MemberPlanDraft, MemberPlanRequest, PrepareRequest, PrivateIntakeAnalysis, Result,
    RevisionRequest, ValidationReport, ValidationRequest, WorkflowRequest, GroupGoalSpec, GroupPrepareRequest,
)
from ..contracts import GoalSetupRequest, GoalSetupOutput
from ..service import GoalAI
from ..contracts import MemberGoalRequest, MemberGoalDraft, MemberCoachReply
from ..contracts import DraftValidationRequest
from ..contracts import ProposalReviewRequest, Contract, Result
from ..baseline import BaselineRequest, build_baseline
from .repository import SQLiteRepository, RevisionConflict
from .telemetry import PersistentTraceSink, provider_identity
from ..tracing import MemoryTraceSink

app = FastAPI(title="WE·MEET AI Core Developer Bench", version="0.10.1")
ai = GoalAI()


@app.middleware('http')
async def protect_json(request,call_next):
    from ..privacy import sanitize
    response=await call_next(request)
    if 'application/json' not in response.headers.get('content-type',''):return response
    body=b''.join([part async for part in response.body_iterator])
    try:data=sanitize(json.loads(body))
    except (ValueError,TypeError):return JSONResponse({'detail':'INVALID_RESPONSE'},status_code=500)
    return JSONResponse(data,status_code=response.status_code,headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}})


@lru_cache
def repository():
    return SQLiteRepository(os.getenv('WEMEET_DB_PATH',str(Path(__file__).parents[3]/'runs'/'workspace.sqlite3')))


from .scenario_api import scenario_router
app.include_router(scenario_router(repository, ai.client))


_cache_instances={}
_cache_initialization_lock=threading.RLock()
def result_cache(path):
    from .result_cache import SQLiteResultCache
    with _cache_initialization_lock:
        if path not in _cache_instances:_cache_instances[path]=SQLiteResultCache(path)
        return _cache_instances[path]


def service(x_trace_id: str | None = Header(default=None), x_parent_run_id: str | None = Header(default=None),x_workspace_id:str|None=Header(default=None),x_scenario_id:str|None=Header(default=None),x_force_refresh:bool=Header(default=False),x_idempotency_key:str|None=Header(default=None)):
    from .scenario_store import ScenarioStore
    from ..scenarios import get_scenario
    from ..references import select_references
    store=ScenarioStore(os.getenv('WEMEET_SCENARIO_DB_PATH',str(repository().path)))
    pool=[get_scenario(k) for k,v in store.reviews().items() if v['status']=='APPROVED']
    try:family=get_scenario(x_scenario_id).family_id if x_scenario_id else None
    except KeyError:family=None
    return GoalAI(ai.client, trace_sink=PersistentTraceSink(repository(),x_workspace_id), trace_id=x_trace_id, parent_run_id=x_parent_run_id,prompt_variant='improved',reference_provider=lambda task,payload:select_references(pool,task,payload,family),cache_backend=result_cache(str(repository().path)),cache_scope=x_workspace_id,force_refresh=x_force_refresh,idempotency_key=x_idempotency_key)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).with_name("index.html"), headers={"Cache-Control": "no-store"})


@app.get('/scenarios',include_in_schema=False)
def library():return FileResponse(Path(__file__).with_name('library.html'),headers={'Cache-Control':'no-store'})


@app.get("/assets/{name}", include_in_schema=False)
def asset(name: str):
    if name not in {"bench.js", "bench.css", "personal.js", "draft_editor.js", "workspace.js", "proactive.js", "scenarios.js", "goal_setup.js"}:
        raise HTTPException(404, "ASSET_NOT_FOUND")
    return FileResponse(Path(__file__).with_name(name))


@app.get("/ai/v1/config")
def config():
    return {**provider_identity(ai.client),'live_calls_paused':os.getenv('WEMEET_OFFLINE')=='1',
            "notice": "Local developer bench; no production authentication or persistence"}


@app.get("/ai/v1/examples/{name}")
def example(name: str):
    from ..examples import example_input, regression_conversation, design_conversation
    if name == "regression": return regression_conversation()
    if name == "design": return design_conversation()
    if name not in ("study", "exercise", "project"): raise HTTPException(404, "EXAMPLE_NOT_FOUND")
    return example_input(name)


@app.post("/ai/v1/goal-setup", response_model=Result[GoalSetupOutput])
def goal_setup(request: GoalSetupRequest, core: GoalAI = Depends(service)):
    return core.advance_goal_setup(request)


@app.post("/ai/v1/coach", response_model=Result[CoachReply])
def coach(request: CoachRequest, core: GoalAI = Depends(service)): return core.coach_reply(request)


@app.post('/ai/v1/coach/review')
def coach_review(request:ProposalReviewRequest,core:GoalAI=Depends(service)):return core.review_coach_proposal(request)


@app.post("/ai/v1/analyze", response_model=Result[ConversationAnalysis])
def analyze(request: AnalysisRequest, core: GoalAI = Depends(service)): return core.analyze_conversation(request)


@app.post("/ai/v1/intake/analyze", response_model=Result[PrivateIntakeAnalysis])
def intake(request: CoachRequest, core: GoalAI = Depends(service)): return core.analyze_private_intake(request)


@app.post("/ai/v1/goal-spec/draft", response_model=Result[GoalSpecDraft])
def draft(request: DraftRequest, core: GoalAI = Depends(service)): return core.draft_goal_spec(request)


@app.post("/ai/v1/workflows/goal-draft", response_model=Result[DraftWorkflow])
def workflow(request: WorkflowRequest, core: GoalAI = Depends(service)): return core.build_goal_draft(request)


@app.post("/ai/v1/goal-spec", response_model=Result[GoalSpec])
def prepare(request: PrepareRequest, core: GoalAI = Depends(service)): return core.prepare_goal_spec(request)


@app.post("/ai/v1/group-plan", response_model=Result[GroupPlan])
def group_plan(request: GoalSpec | GroupGoalSpec, core: GoalAI = Depends(service)): return core.generate_group_plan(request)


@app.post("/ai/v1/group-goal-spec", response_model=Result[GroupGoalSpec])
def prepare_group(request: GroupPrepareRequest, core: GoalAI = Depends(service)): return core.prepare_group_goal_spec(request)


@app.post("/ai/v1/goal-spec/validate-draft", response_model=Result[GoalSpecDraft])
def validate_draft(request: DraftValidationRequest, core: GoalAI = Depends(service)): return core.validate_goal_draft(request)


@app.post("/ai/v1/member-plan", response_model=Result[MemberPlanDraft])
def member_plan(request: MemberPlanRequest, core: GoalAI = Depends(service)): return core.generate_member_plan(request)


@app.post("/ai/v1/member-goal/coach", response_model=Result[MemberCoachReply])
def member_coach(request: MemberGoalRequest, core: GoalAI = Depends(service)): return core.coach_member_goal(request)


@app.post("/ai/v1/member-goal/draft", response_model=Result[MemberGoalDraft])
def member_goal_draft(request: MemberGoalRequest, core: GoalAI = Depends(service)): return core.draft_member_goal(request)


@app.post("/ai/v1/validate", response_model=Result[ValidationReport])
def validate(request: ValidationRequest, core: GoalAI = Depends(service)): return core.validate_plan(request)


@app.post("/ai/v1/revise", response_model=Result[GroupPlan] | Result[MemberPlanDraft])
def revise(request: RevisionRequest, core: GoalAI = Depends(service)): return core.revise_plan(request)


@app.post("/ai/v1/handoff", response_model=Result[HandoffPackage])
def handoff(request: HandoffRequest, core: GoalAI = Depends(service)): return core.build_handoff(request)


class WorkspaceCreate(Contract):
    snapshot:dict


class WorkspaceSave(WorkspaceCreate):
    expected_revision:int


@app.get('/ai/v1/workspaces')
def list_workspaces():return repository().list()


@app.post('/ai/v1/workspaces')
def create_workspace(request:WorkspaceCreate):return repository().create(request.snapshot)


@app.get('/ai/v1/workspaces/{workspace_id}')
def get_workspace(workspace_id:str):
    try:return repository().get(workspace_id)
    except KeyError:raise HTTPException(404,'WORKSPACE_NOT_FOUND')


@app.put('/ai/v1/workspaces/{workspace_id}')
def save_workspace(workspace_id:str,request:WorkspaceSave):
    try:return repository().save(workspace_id,request.snapshot,request.expected_revision)
    except RevisionConflict:raise HTTPException(409,'WORKSPACE_REVISION_CONFLICT')
    except Exception:raise HTTPException(503,'WORKSPACE_SAVE_FAILED')


@app.get('/ai/v1/provider/status')
def provider_status():return {**provider_identity(ai.client),'live_calls_paused':os.getenv('WEMEET_OFFLINE')=='1',**repository().calls()}


@app.post('/ai/v1/provider/probe')
def probe(core:GoalAI=Depends(service)):
    return core.probe_provider()


@app.post('/ai/v1/baselines')
def baseline(request:BaselineRequest,core:GoalAI=Depends(service),x_workspace_id:str|None=Header(default=None)):
    result=core._run('baseline',request,lambda _:build_baseline(request),uses_ai=False)
    if result.status=='OK':
        if not x_workspace_id:raise HTTPException(400,'WORKSPACE_REQUIRED')
        try:
            public=repository().save_baseline(x_workspace_id,result.data)
            result.data=public
        except KeyError:raise HTTPException(404,'WORKSPACE_NOT_FOUND')
        except Exception:raise HTTPException(503,'BASELINE_SAVE_FAILED')
    return result


@app.get('/ai/v1/workspaces/{workspace_id}/baselines')
def list_baselines(workspace_id:str,owner_member_id:str|None=None):return repository().baselines(workspace_id,owner_member_id)
