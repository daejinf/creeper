import os

"""Real PostgreSQL + separate disposable Python workers, never provider calls."""

import json, os, subprocess, sys, time
from pathlib import Path
from uuid import uuid4
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from wemeet_coach.persistence import Store
from wemeet_coach.persistence import schema as d
from conftest import members

WORKER = """
import json,sys,time
from pathlib import Path
from wemeet_coach.persistence import Store
from wemeet_coach.m0.runtime import code_result
from wemeet_coach.shared.contracts import GroupReply
p=json.load(sys.stdin);s=Store(p['url'],p['recovery'],p['key'])
if p.get('fail_commit'):
 original=s.event
 def event(c,gid,kind,*args,**kwargs):
  if kind=='AI_REPLY':raise OSError('synthetic commit failure')
  return original(c,gid,kind,*args,**kwargs)
 s.event=event
def run(payload):
 with open(p['marker'],'a',encoding='utf8') as f:f.write('called\\n')
 if p.get('sleep'):time.sleep(p['sleep'])
 return code_result('group.COACH',GroupReply(reply='복원 가능한 익명 시험 응답'))
out=s.execute(p['gid'],p['action'],'group.COACH',{},p['revision'],runner=run)
print(json.dumps(out,ensure_ascii=False),flush=True)
"""


def setup(tmp_path):
    url = os.environ.get("WEMEET_COACH_TEST_DATABASE_URL")
    key = Fernet.generate_key().decode()
    s = Store(url, tmp_path / "recovery", key)
    g = s.create("격리 프로세스 검증", members(1), purpose="VALIDATION")
    p = {
        "url": url,
        "key": key,
        "recovery": str(tmp_path / "recovery"),
        "marker": str(tmp_path / "calls.txt"),
        "gid": g["group_id"],
        "action": str(uuid4()),
        "revision": g["revision"],
    }
    return s, p


def start(p):
    proc = subprocess.Popen(
        [sys.executable, "-c", WORKER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    proc.stdin.write(json.dumps(p))
    proc.stdin.close()
    return proc


def output(proc):
    proc.wait(timeout=25)
    text = proc.stdout.read()
    assert proc.returncode == 0, proc.stderr.read()
    return json.loads(text)


def test_two_processes_claim_one_operation_and_restart_reuses_result(tmp_path):
    s, p = setup(tmp_path)
    p["sleep"] = 1
    a = start(p)
    b = start(p)
    one = output(a)
    two = output(b)
    assert Path(p["marker"]).read_text().splitlines() == ["called"]
    assert "COMPLETE" in {one["persistence"]["state"], two["persistence"]["state"]}
    assert output(start(p))["reused"]


def test_encrypted_response_save_only_recovery_in_new_process(tmp_path):
    s, p = setup(tmp_path)
    first = output(start({**p, "fail_commit": True}))
    assert first["persistence"]["state"] == "SAVE_PENDING"
    assert list((tmp_path / "recovery").glob("*.bin"))
    second = output(start(p))
    assert second["persistence"]["state"] == "COMPLETE"
    assert Path(p["marker"]).read_text().splitlines() == ["called"]
    assert not list((tmp_path / "recovery").glob("*.bin"))


def test_killed_worker_becomes_unknown_without_a_second_call(tmp_path):
    s, p = setup(tmp_path)
    proc = start({**p, "sleep": 60})
    deadline = time.monotonic() + 10
    while not Path(p["marker"]).exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert Path(p["marker"]).exists()
    proc.kill()
    proc.wait(timeout=5)
    # Advance only this test operation's lease, rather than wait ten minutes.
    with s.tx(p["gid"]) as (c, _):
        c.execute(
            d.operations.update()
            .where(
                d.operations.c.group_id == p["gid"],
                d.operations.c.action_id == p["action"],
            )
            .values(created_at=sa.func.now() - sa.text("interval '11 minutes'"))
        )
    result = output(start(p))
    assert result["persistence"]["state"] == "UNKNOWN"
    assert Path(p["marker"]).read_text().splitlines() == ["called"]


def test_database_connection_failure_precedes_any_model_call(tmp_path):
    import socket
    from sqlalchemy.engine import make_url

    url = os.environ.get("WEMEET_COACH_TEST_DATABASE_URL")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    s = Store(
        make_url(url).set(port=port).render_as_string(hide_password=False),
        tmp_path / "recovery",
        Fernet.generate_key(),
    )
    with pytest.raises(sa.exc.OperationalError):
        s.execute(
            "not-created",
            str(uuid4()),
            "group.COACH",
            {},
            0,
            runner=lambda p: pytest.fail("model must not run"),
        )
