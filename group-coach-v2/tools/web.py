"""Local integration example, not service authentication. Always LIVE for AI."""

import json, os
from functools import lru_cache
from uuid import uuid4
from fastapi import FastAPI, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from tools.config import ROOT, load
from wemeet_coach import __version__
from wemeet_coach.persistence.store import Store, Conflict, NotFound
from wemeet_coach.shared.contracts import ActorContext

load()
app = FastAPI(title="WE·MEET 공동·개인 코치", version=__version__)


@lru_cache
def store():
    return Store(
        os.getenv("WEMEET_COACH_DATABASE_URL"),
        ROOT / "runtime/recovery",
        os.getenv("WEMEET_COACH_RECOVERY_KEY"),
    )


@app.exception_handler(Exception)
async def failure(request, exc):
    if isinstance(exc, PermissionError):
        code, status, message = "ACCESS_DENIED", 403, str(exc)
    elif isinstance(exc, Conflict):
        code, status, message = "CONFLICT", 409, str(exc)
    elif isinstance(exc, NotFound):
        code, status, message = "NOT_FOUND", 404, str(exc)
    elif isinstance(exc, (ValueError, ValidationError, KeyError)):
        code, status, message = (
            "INPUT_INVALID",
            422,
            (
                "입력 항목·구성원·조건을 확인해주세요."
                if isinstance(exc, (ValidationError, KeyError))
                else str(exc)
            ),
        )
    else:
        code, status, message = (
            "STORAGE_UNAVAILABLE",
            503,
            "새 프로젝트 DB 저장·연결을 확인해주세요. 저장 완료가 아닙니다.",
        )
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status
    )


@app.get("/")
def index():
    return FileResponse(ROOT / "web/index.html", headers={"Cache-Control": "no-store"})


@app.get("/assets/{name}")
def asset(name: str):
    if name not in {"app.js", "style.css"}:
        return JSONResponse({}, status_code=404)
    return FileResponse(ROOT / "web" / name, headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def config():
    return {
        "version": __version__,
        "model": os.getenv("TIMELY_MODEL", "openai/gpt-5.6-luna"),
        "configured": bool(os.getenv("TIMELY_API_KEY")),
        "provider": "Timely",
        "database_configured": bool(os.getenv("WEMEET_COACH_DATABASE_URL")),
        "authentication": "LOCAL_TEST_ONLY",
    }


@app.get("/api/groups")
def groups():
    return store().list()


@app.post("/api/groups")
def create(body: dict):
    return store().create(body.get("title", "새 모임"), body["members"])


@app.get("/api/groups/{gid}")
def group(gid: str):
    return store().view(gid)


@app.post("/api/groups/{gid}/members")
def add_member(gid: str, body: dict):
    store().add_member(gid, body["member"], body["action_id"])
    return store().view(gid)


@app.post("/api/groups/{gid}/messages")
def message(gid: str, body: dict):
    return store().message(gid, body["member_id"], body["text"], body["message_id"])


@app.post("/api/groups/{gid}/ai")
def ai(gid: str, body: dict):
    result = store().execute(
        gid, body["action_id"], "group." + body["action"], {}, body["expected_revision"]
    )
    return {"result": result, "group": store().view(gid)}


@app.post("/api/groups/{gid}/proposals/{pid}/vote")
def vote(gid: str, pid: str, body: dict):
    if type(body.get("agree")) != bool:
        raise ValueError("찬성/반대를 선택해주세요.")
    store().vote(
        gid,
        pid,
        body["member_id"],
        body["agree"],
        body.get("issue", ""),
        body.get("reason", ""),
        body["action_id"],
    )
    return store().view(gid)


@app.post("/api/groups/{gid}/proposal-edit")
def edit(gid: str, body: dict):
    store().edit_proposal(
        gid,
        body["action_id"],
        body["member_id"],
        body["proposal"],
        body["expected_revision"],
    )
    return store().view(gid)


@app.get("/api/groups/{gid}/personal/{owner}")
def personal(gid: str, owner: str):
    return store().personal_view(gid, owner)


@app.post("/api/groups/{gid}/personal/{owner}/ai")
def private_ai(gid: str, owner: str, body: dict):
    params = {k: body[k] for k in ["text", "message_id", "answer"] if k in body}
    result = store().execute(
        gid,
        body["action_id"],
        "personal." + body["action"],
        params,
        body["expected_revision"],
        owner=owner,
    )
    return {
        "result": result,
        "personal": store().personal_view(gid, owner),
        "group": store().view(gid),
    }


@app.post("/api/groups/{gid}/personal/{owner}/publish")
def publish(gid: str, owner: str, body: dict):
    result = store().publish_personal(gid, owner, body["plan_id"], body["action_id"])
    return {
        "result": result,
        "group": store().view(gid),
        "personal": store().personal_view(gid, owner),
    }


@app.post("/api/groups/{gid}/personal/{owner}/conditions")
def conditions(gid: str, owner: str, body: dict):
    store().review_conditions(gid, owner, body["updates"], body["action_id"])
    return store().personal_view(gid, owner)


@app.post("/api/groups/{gid}/personal/{owner}/edit")
def edit_personal(gid: str, owner: str, body: dict):
    store().edit_personal(gid, owner, body["plan_id"], body["plan"], body["action_id"])
    return store().personal_view(gid, owner)


@app.get("/api/scenarios")
def scenarios():
    return [
        {k: r[k] for k in ["case_id", "title", "summary", "domain", "kind"]}
        for line in (ROOT / "evaluations/scenarios.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if (r := json.loads(line))
    ]


@app.get("/api/scenarios/{sid}")
def scenario(sid: str):
    for line in (
        (ROOT / "evaluations/scenarios.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        r = json.loads(line)
        if r["case_id"] == sid:
            return {
                "case_id": sid,
                "title": r["title"],
                "summary": r["summary"],
                "input": r["group_input"],
            }
    raise NotFound("예제를 찾지 못했습니다.")


@app.post("/api/scenarios/{sid}/load")
def load_scenario(sid: str):
    example = scenario(sid)
    request = example["input"]
    view = store().create(
        "예제 · " + example["title"],
        request["members"],
        purpose="EXAMPLE",
        reference_date=request["reference_date"],
        timezone_name=request.get("timezone", "Asia/Seoul"),
    )
    for message in request["conversation"]:
        store().message(
            view["group_id"],
            message["speaker_id"],
            message["text"],
            message["message_id"] + ":" + view["group_id"],
        )
    return store().view(view["group_id"])


async def verified_actor(request: Request):
    """SW installs a verified identity resolver. Never trust a query/body/header ID."""
    resolver = getattr(request.app.state, "identity_resolver", None)
    if resolver is None:
        raise PermissionError(
            "SW 인증 연결이 필요합니다. 테스트 발언자 선택은 인증이 아닙니다."
        )
    import inspect

    actor = resolver(request)
    if inspect.isawaitable(actor):
        actor = await actor
    return ActorContext.model_validate(actor)


@app.get("/integration/v1/me/groups")
def my_groups(actor=Depends(verified_actor)):
    return store().reader(actor).groups()


@app.get("/integration/v1/groups/{gid}/context")
def group_context(gid: str, actor=Depends(verified_actor)):
    return store().reader(actor).group(gid)


@app.get("/integration/v1/groups/{gid}/me")
def member_context(gid: str, actor=Depends(verified_actor)):
    return store().reader(actor).personal(gid)


@app.get("/integration/v1/groups/{gid}/plans/{pid}")
def plan_context(gid: str, pid: str, actor=Depends(verified_actor)):
    return store().reader(actor).plan(gid, pid)


@app.get("/integration/v1/groups/{gid}/baselines/{bid}/plans/{pid}/tasks/{tid}")
def baseline_context(
    gid: str, bid: str, pid: str, tid: str, actor=Depends(verified_actor)
):
    return store().reader(actor).baseline(gid, bid, pid, tid)


@app.get("/integration/v1/groups/{gid}/module-context")
def module_context(
    gid: str,
    scope: str,
    plan_version_id: str | None = None,
    actor=Depends(verified_actor),
):
    return (
        store().reader(actor).context(gid, scope=scope, plan_version_id=plan_version_id)
    )


@app.post("/api/groups/{gid}/retry-save")
def retry_group_save(gid: str, body: dict):
    return {
        "result": store().retry_save(gid, body["action_id"]),
        "group": store().view(gid),
    }


@app.post("/api/groups/{gid}/personal/{owner}/retry-save")
def retry_personal_save(gid: str, owner: str, body: dict):
    return {
        "result": store().retry_save(gid, body["action_id"], owner=owner),
        "group": store().view(gid),
        "personal": store().personal_view(gid, owner),
    }
