"""Stage 7 incident drill: Timely hangs, the server is killed mid-call, Timely then fails. Local machine only, NOT LIVE.

  MOIM_ADMIN_URL=<superuser url of the LOCAL cluster>  python tools/drill_ai_failure.py

A throwaway database `moim_drill` and a local fake Timely endpoint (the real M0 TimelyClient talks to it over HTTP;
nothing reaches the real provider and no call is reported as LIVE). Checks:
  1. a real server process (uvicorn, JSON logs) activates a group; the first of its AI calls hangs;
  2. the process is killed while the calls are in flight, then started again: start-up marks the lost work FAILED
     (SERVER_RESTARTED) and the screen shows the failure with a retry option - nothing is stuck RUNNING;
  3. Timely now answers HTTP 500: pressing retry makes exactly ONE provider call (no automatic retry, no fallback),
     the screen shows FAILED/UPSTREAM_ERROR, and the provider_call log line carries the retry request's X-Request-ID;
  4. no log line contains the ProjectBrief text, the fake API key or a session token.
Everything is removed at the end; evidence is printed as JSON.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from tools import ops_db  # noqa: E402

DB = "moim_drill"
BRIEF_TEXT = "드릴 전용 프로젝트 본문 문장 7c1e"
FAKE_KEY = "drill-fake-key-" + uuid.uuid4().hex[:8]


class Fake:
    mode = "hang"
    hits = 0
    release = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length") or 0))
        Fake.hits += 1
        if Fake.mode == "hang":
            Fake.release.wait(30)
        body = b'{"error":"fake upstream failure"}'
        self.send_response(500)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, timeout=20.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(step)
    return None


def find(obj, key):
    """First dict (depth-first) that has `key`."""
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for v in obj.values():
            hit = find(v, key)
            if hit:
                return hit
    if isinstance(obj, list):
        for v in obj:
            hit = find(v, key)
            if hit:
                return hit
    return None


def main() -> int:
    admin = os.environ.get("MOIM_ADMIN_URL", "")
    if not admin or make_url(admin).host not in ("127.0.0.1", "localhost", "::1"):
        print("error: MOIM_ADMIN_URL must point to the local cluster", file=sys.stderr)
        return 1
    drill_url = make_url(admin).set(database=DB).render_as_string(hide_password=False)
    fake_port, app_port = free_port(), free_port()
    fake = ThreadingHTTPServer(("127.0.0.1", fake_port), Handler)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="moim-drill-"))
    log_path = work / "server.log"
    ev: dict = {}

    def admin_exec(stmt):
        with psycopg.connect(**ops_db._conninfo(admin), autocommit=True) as c:
            c.execute(stmt)

    admin_exec(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(DB)))
    admin_exec(sql.SQL("create database {}").format(sql.Identifier(DB)))
    env = {**os.environ, "MOIM_ENV": "development", "MOIM_DATABASE_URL": drill_url, "MOIM_LOG_FORMAT": "json",
           "TIMELY_BASE_URL": f"http://127.0.0.1:{fake_port}/v1", "TIMELY_API_KEY": FAKE_KEY,
           "MOIM_UPLOAD_DIR": str(work / "uploads"), "PYTHONIOENCODING": "utf-8"}
    env.pop("WEMEET_OFFLINE", None)
    procs = []
    try:
        subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
                       env=env, check=True, capture_output=True)

        def start():
            log = open(log_path, "ab")
            p = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "moim.main:app_factory", "--host",
                                  "127.0.0.1", "--port", str(app_port), "--no-access-log"],
                                 env={**env, "PYTHONPATH": str(ROOT / "src")}, stdout=log, stderr=log)
            procs.append(p)
            ok = wait_for(lambda: _healthy(app_port), 30)
            if not ok:
                raise RuntimeError("server did not start")
            return p

        # users and sessions straight in the drill database (no Argon2 needed for this drill)
        os.environ.update({"MOIM_ENV": "development", "MOIM_DATABASE_URL": drill_url})
        from moim.auth.sessions import SessionStore
        from moim.config import load_settings
        from moim.db import make_engine, make_session_factory
        from sqlalchemy import text

        eng = make_engine(load_settings())
        ids = [str(uuid.uuid4()) for _ in range(2)]
        with eng.begin() as c:
            for i, uid in enumerate(ids):
                c.execute(text("insert into users (id, username, display_name, password_hash, terms_accepted_at) "
                               "values (:i, :u, :d, 'drill-not-loginable', now())"),
                          {"i": uid, "u": f"drill{i}{uid[:6]}", "d": f"드릴{i}"})
        tokens = [SessionStore(make_session_factory(eng)).create(uid, False)[0] for uid in ids]
        eng.dispose()

        start()
        base = f"http://127.0.0.1:{app_port}"
        clients = []
        for t in tokens:
            c = httpx.Client(base_url=base, follow_redirects=False, timeout=10)
            c.cookies.set("moim_session", t)
            c.get("/dashboard")  # a signed-in page sets the CSRF cookie
            clients.append(c)
        lead, member = clients

        def post(c, path, body):
            return c.post(path, json=body, headers={"X-CSRF-Token": c.cookies.get("moim_csrf"), "Origin": base})

        gid = post(lead, "/api/groups", {}).json()["group_id"]
        code = lead.get(f"/api/groups/{gid}/room").json()["code"]
        assert post(member, "/api/groups/join", {"code": code}).status_code == 201
        t = date.today() + timedelta(days=1)
        r = post(lead, f"/api/groups/{gid}/activate", {
            "name": "드릴 그룹", "content": BRIEF_TEXT, "start_date": t.isoformat(),
            "end_date": (t + timedelta(days=10)).isoformat(), "types": ["보고서"], "milestones": []})
        ev["activate_status"] = r.status_code
        # The goal and candidate calls of one activation run one after the other (same request's background
        # tasks), so exactly one is hanging when the process is killed; the other has not started yet.
        ev["calls_in_flight"] = wait_for(lambda: Fake.hits >= 1 and Fake.hits, 20)
        roles = member.get(f"/api/groups/{gid}/roles").json()
        ev["before_kill_candidate_state"] = (find(roles, "candidate_revision") or {}).get("state")

        # ---- kill mid-call, restart ----
        procs[-1].kill()
        procs[-1].wait(10)
        Fake.release.set()
        Fake.mode = "error500"
        start()
        roles = member.get(f"/api/groups/{gid}/roles").json()
        cand = find(roles, "candidate_revision") or {}
        ev["after_restart_candidate"] = {k: cand.get(k) for k in ("state", "error_code", "attempt")}
        goal = lead.get(f"/api/groups/{gid}/goal").json()
        ev["after_restart_goal"] = {k: (find(goal, "state") or {}).get(k) for k in ("state", "error_code")}

        # ---- retry against a failing provider: exactly one call ----
        hits_before = Fake.hits
        rr = post(member, f"/api/groups/{gid}/roles/candidates/retry", {
            "action_id": uuid.uuid4().hex, "candidate_revision": cand.get("candidate_revision"),
            "expected_attempt": cand.get("attempt") or 1})
        ev["retry_status"] = rr.status_code
        retry_rid = rr.headers.get("x-request-id")

        def failed():
            c2 = find(member.get(f"/api/groups/{gid}/roles").json(), "candidate_revision") or {}
            return c2 if c2.get("state") == "FAILED" and (c2.get("attempt") or 0) > (cand.get("attempt") or 0) else None

        after = wait_for(failed, 20) or {}
        time.sleep(1.0)
        ev["after_retry_candidate"] = {k: after.get(k) for k in ("state", "error_code", "attempt")}
        ev["provider_calls_for_one_retry"] = Fake.hits - hits_before

        for p in procs:
            if p.poll() is None:
                p.terminate()
                p.wait(10)
        lines = []
        for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                lines.append(json.loads(raw))
            except ValueError:
                pass
        ev["json_log_lines"] = len(lines)
        prov = [e for e in lines if e.get("logger") == "moim.provider" and "provider_call" in e.get("msg", "")]
        ev["provider_call_lines"] = len(prov)
        ev["retry_call_logged_with_request_id"] = any(
            e["request_id"] == retry_rid and "ok=false" in e["msg"] and "error=UPSTREAM_ERROR" in e["msg"] for e in prov)
        ev["restart_marked_lines"] = sum(1 for e in lines if "SERVER_RESTARTED" in e.get("msg", ""))
        raw_text = log_path.read_text(encoding="utf-8", errors="replace")
        ev["log_contains_brief_text"] = BRIEF_TEXT in raw_text
        ev["log_contains_fake_key"] = FAKE_KEY in raw_text
        ev["log_contains_session_token"] = any(t in raw_text for t in tokens)
        ok = (ev["activate_status"] == 201 and ev["calls_in_flight"]
              and ev["after_restart_candidate"]["state"] == "FAILED"
              and ev["after_restart_candidate"]["error_code"] == "SERVER_RESTARTED"
              and ev["after_restart_goal"]["state"] == "FAILED"
              and ev["retry_status"] == 202 and ev["after_retry_candidate"]["error_code"] == "UPSTREAM_ERROR"
              and ev["provider_calls_for_one_retry"] == 1 and ev["retry_call_logged_with_request_id"]
              and ev["restart_marked_lines"] >= 1
              and not (ev["log_contains_brief_text"] or ev["log_contains_fake_key"] or ev["log_contains_session_token"]))
        ev["result"] = "PASS" if ok else "FAIL"
        for c in clients:
            c.close()
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        Fake.release.set()
        fake.shutdown()
        admin_exec(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(DB)))
        ev["cleanup"] = "drill database and server removed"
    print(json.dumps(ev, ensure_ascii=False, indent=1))
    return 0 if ev.get("result") == "PASS" else 2


def _healthy(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


if __name__ == "__main__":
    sys.exit(main())
