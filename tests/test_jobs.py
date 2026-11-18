"""0B-3 deterministic contract for persisted AI job state.

State lives in the ai_jobs table. Every assertion that proves recovery re-reads through a brand-new
engine/app, so nothing can pass by leaning on process memory.
"""
import logging
import threading
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from moim.config import load_settings
from moim.db import make_engine, make_session_factory
from moim.jobs import JobError, JobStore, OperationConflict, RevisionConflict, run_job
from moim.main import create_app

ROOT = Path(__file__).resolve().parents[1]
SECRET = "PRIVATE-JOB-INPUT-MARKER-4417"


@pytest.fixture()
def db(test_environ):
    eng = create_engine(test_environ)
    with eng.begin() as c:
        c.execute(text("drop schema public cascade"))
        c.execute(text("create schema public"))
    eng.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    return test_environ


def new_store() -> tuple[JobStore, object]:
    engine = make_engine(load_settings())
    return JobStore(make_session_factory(engine)), engine


@pytest.fixture()
def store(db):
    s, engine = new_store()
    yield s
    engine.dispose()


def ok_handler(payload):
    return {"echo": payload["n"] * 2}


def boom_handler(payload):
    raise RuntimeError(f"raw failure text {SECRET}")


def typed_failure_handler(payload):
    raise JobError("RATE_LIMITED", "잠시 후 다시 시도해주세요.")


HANDLERS = {"ok": ok_handler, "boom": boom_handler, "typed": typed_failure_handler}


# ---- store: states, revisions, idempotency --------------------------------------------------

def test_submit_stores_pending_job_with_revision_1(store):
    job, created = store.submit("op-1", "ok", {"n": 2})
    assert created is True
    assert (job.state, job.revision, job.attempt) == ("PENDING", 1, 1)


def test_duplicate_operation_returns_same_job_and_one_row(store, db):
    first, c1 = store.submit("op-1", "ok", {"n": 2})
    second, c2 = store.submit("op-1", "ok", {"n": 2})
    assert (c1, c2) == (True, False) and first.id == second.id
    eng = create_engine(db)
    with eng.connect() as c:
        assert c.execute(text("select count(*) from ai_jobs")).scalar() == 1
    eng.dispose()


def test_same_operation_with_different_input_is_a_conflict(store):
    store.submit("op-1", "ok", {"n": 2})
    with pytest.raises(OperationConflict):
        store.submit("op-1", "ok", {"n": 3})
    with pytest.raises(OperationConflict):
        store.submit("op-1", "boom", {"n": 2})


def test_full_flow_increments_revision_each_step(store):
    job, _ = store.submit("op-1", "ok", {"n": 2})
    running = store.claim(job.id, job.revision)
    assert (running.state, running.revision) == ("RUNNING", 2)
    done = store.complete(job.id, running.revision, {"echo": 4})
    assert (done.state, done.revision, done.result) == ("SUCCEEDED", 3, {"echo": 4})


def test_only_one_claim_wins(store):
    job, _ = store.submit("op-1", "ok", {"n": 2})
    store.claim(job.id, job.revision)
    with pytest.raises(RevisionConflict):
        store.claim(job.id, job.revision)


def test_concurrent_claims_have_exactly_one_winner(db):
    s1, e1 = new_store()
    s2, e2 = new_store()
    job, _ = s1.submit("op-1", "ok", {"n": 2})
    outcomes, barrier = [], threading.Barrier(2)

    def attempt(s):
        barrier.wait()
        try:
            s.claim(job.id, job.revision)
            outcomes.append("won")
        except RevisionConflict:
            outcomes.append("lost")

    threads = [threading.Thread(target=attempt, args=(s,)) for s in (s1, s2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    e1.dispose(), e2.dispose()
    assert sorted(outcomes) == ["lost", "won"]


def test_stale_revision_cannot_complete_or_fail(store):
    job, _ = store.submit("op-1", "ok", {"n": 2})
    running = store.claim(job.id, job.revision)
    with pytest.raises(RevisionConflict):
        store.complete(job.id, job.revision, {"x": 1})  # stale: still thinks revision 1
    with pytest.raises(RevisionConflict):
        store.fail(job.id, job.revision, "X", "x")
    assert store.get(job.id).revision == running.revision


def test_finished_job_cannot_be_overwritten(store):
    job, _ = store.submit("op-1", "ok", {"n": 2})
    running = store.claim(job.id, job.revision)
    done = store.complete(job.id, running.revision, {"echo": 4})
    with pytest.raises(RevisionConflict):
        store.fail(job.id, done.revision, "LATE", "late failure")
    assert store.get(job.id).state == "SUCCEEDED"


def test_retry_only_from_failed_and_with_current_revision(store):
    job, _ = store.submit("op-1", "ok", {"n": 2})
    with pytest.raises(RevisionConflict):
        store.retry(job.id, job.revision)  # PENDING is not retryable
    running = store.claim(job.id, job.revision)
    failed = store.fail(job.id, running.revision, "UPSTREAM_ERROR", "x")
    with pytest.raises(RevisionConflict):
        store.retry(job.id, running.revision)  # stale revision
    again = store.retry(job.id, failed.revision)
    assert (again.state, again.attempt, again.error_code) == ("PENDING", 2, None)
    assert again.revision == failed.revision + 1


# ---- runner ---------------------------------------------------------------------------------

def test_run_job_success_is_visible_from_a_fresh_connection(db):
    s1, e1 = new_store()
    job, _ = s1.submit("op-1", "ok", {"n": 21})
    run_job(s1, HANDLERS, job.id)
    e1.dispose()
    s2, e2 = new_store()  # new engine: nothing carried in memory
    got = s2.get(job.id)
    e2.dispose()
    assert (got.state, got.result) == ("SUCCEEDED", {"echo": 42})


def test_handler_exception_becomes_failed_without_raw_text(store):
    job, _ = store.submit("op-1", "boom", {"n": 1})
    run_job(store, HANDLERS, job.id)
    got = store.get(job.id)
    assert got.state == "FAILED" and got.error_code == "HANDLER_ERROR"
    assert SECRET not in (got.error_message or "")


def test_typed_job_error_keeps_code_and_message(store):
    job, _ = store.submit("op-1", "typed", {"n": 1})
    run_job(store, HANDLERS, job.id)
    got = store.get(job.id)
    assert (got.state, got.error_code) == ("FAILED", "RATE_LIMITED")
    assert got.error_message == "잠시 후 다시 시도해주세요."


def test_run_job_does_not_rerun_a_job_someone_else_claimed(store):
    calls = []
    job, _ = store.submit("op-1", "ok", {"n": 1})
    store.claim(job.id, job.revision)
    run_job(store, {"ok": lambda p: calls.append(1) or {}}, job.id)
    assert calls == [] and store.get(job.id).state == "RUNNING"


def test_unknown_kind_at_run_time_fails_cleanly(store):
    job, _ = store.submit("op-1", "gone", {"n": 1})
    run_job(store, HANDLERS, job.id)
    assert store.get(job.id).state == "FAILED"


# ---- restart recovery -----------------------------------------------------------------------

def test_server_start_fails_interrupted_jobs_and_they_can_be_retried(db):
    s1, e1 = new_store()
    stuck, _ = s1.submit("op-running", "ok", {"n": 5})
    s1.claim(stuck.id, stuck.revision)                      # RUNNING when the process "died"
    waiting, _ = s1.submit("op-pending", "ok", {"n": 6})    # PENDING: its background task died with the process
    finished, _ = s1.submit("op-done", "ok", {"n": 7})
    run_job(s1, HANDLERS, finished.id)
    e1.dispose()

    with TestClient(create_app(job_handlers=HANDLERS)) as client:   # lifespan = server start
        for jid in (stuck.id, waiting.id):
            body = client.get(f"/api/jobs/{jid}").json()
            assert body["state"] == "FAILED" and body["error_code"] == "SERVER_RESTARTED"
        assert client.get(f"/api/jobs/{finished.id}").json()["state"] == "SUCCEEDED"
        revision = client.get(f"/api/jobs/{stuck.id}").json()["revision"]
        retry = client.post(f"/api/jobs/{stuck.id}/retry", json={"revision": revision})
        assert retry.status_code == 202
        after = client.get(f"/api/jobs/{stuck.id}").json()
        assert (after["state"], after["attempt"], after["result"]) == ("SUCCEEDED", 2, {"echo": 10})


# ---- API ------------------------------------------------------------------------------------

@pytest.fixture()
def client(db):
    with TestClient(create_app(job_handlers=HANDLERS)) as c:
        yield c


def test_post_returns_202_and_status_url(client):
    r = client.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 4}})
    assert r.status_code == 202
    body = r.json()
    assert body["status_url"] == f"/api/jobs/{body['job_id']}" and r.headers["location"] == body["status_url"]
    assert body["state"] == "PENDING"


def test_status_poll_returns_result_and_survives_a_new_app_instance(client, db):
    posted = client.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 4}}).json()
    first = client.get(posted["status_url"]).json()
    assert (first["state"], first["result"]) == ("SUCCEEDED", {"echo": 8})
    with TestClient(create_app(job_handlers=HANDLERS)) as fresh:   # a "refresh" after a restart
        again = fresh.get(posted["status_url"]).json()
    assert again == first


def test_duplicate_post_does_not_create_or_rerun(client, db):
    calls = []
    app = create_app(job_handlers={"ok": lambda p: calls.append(1) or {"echo": 1}})
    with TestClient(app) as c:
        a = c.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 1}})
        b = c.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 1}})
    assert a.status_code == 202 and b.status_code == 200
    assert a.json()["job_id"] == b.json()["job_id"] and calls == [1]


def test_conflicting_operation_is_409(client):
    client.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 1}})
    r = client.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 2}})
    assert r.status_code == 409


def test_retry_with_stale_revision_is_409(client):
    posted = client.post("/api/jobs", json={"operation_id": "op-1", "kind": "boom", "input": {"n": 1}}).json()
    failed = client.get(posted["status_url"]).json()
    assert failed["state"] == "FAILED"
    stale = client.post(f"/api/jobs/{posted['job_id']}/retry", json={"revision": failed["revision"] - 1})
    assert stale.status_code == 409
    ok = client.post(f"/api/jobs/{posted['job_id']}/retry", json={"revision": failed["revision"]})
    assert ok.status_code == 202 and client.get(posted["status_url"]).json()["attempt"] == 2


def test_retry_of_a_succeeded_job_is_409(client):
    posted = client.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 1}}).json()
    done = client.get(posted["status_url"]).json()
    r = client.post(f"/api/jobs/{posted['job_id']}/retry", json={"revision": done["revision"]})
    assert r.status_code == 409


@pytest.mark.parametrize("payload", [
    {"operation_id": "op-1", "kind": "not-registered", "input": {}},
    {"operation_id": "", "kind": "ok", "input": {}},
    {"operation_id": "x" * 129, "kind": "ok", "input": {}},
    {"operation_id": "op-1", "kind": "ok"},
    {"operation_id": "bad id with spaces", "kind": "ok", "input": {}},
])
def test_invalid_submit_is_rejected_before_anything_is_stored(client, db, payload):
    assert client.post("/api/jobs", json=payload).status_code == 422
    eng = create_engine(db)
    with eng.connect() as c:
        assert c.execute(text("select count(*) from ai_jobs")).scalar() == 0
    eng.dispose()


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.post("/api/jobs/nope/retry", json={"revision": 1}).status_code == 404


def test_status_response_never_echoes_the_input(client):
    posted = client.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 1, "note": SECRET}}).json()
    assert SECRET not in client.get(posted["status_url"]).text and SECRET not in str(posted)


def test_no_handlers_means_nothing_can_be_submitted(db):
    with TestClient(create_app()) as c:
        r = c.post("/api/jobs", json={"operation_id": "op-1", "kind": "ok", "input": {"n": 1}})
    assert r.status_code == 422


def test_job_logs_do_not_contain_input_or_raw_errors(client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/api/jobs", json={"operation_id": "op-1", "kind": "boom", "input": {"n": 1, "note": SECRET}})
    assert SECRET not in "\n".join(r.getMessage() for r in caplog.records)


def test_polling_helper_is_served_and_writes_text_only(client):
    r = client.get("/static/job-poller.js")
    assert r.status_code == 200
    assert "textContent" in r.text and "innerHTML" not in r.text and "localStorage" not in r.text
