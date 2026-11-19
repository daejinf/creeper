"""Stage 7 load sample on this machine. NOT a capacity test of the production host and NOT LIVE (no AI call).

  MOIM_ADMIN_URL=<superuser url of the LOCAL cluster>  python tools/load_sample.py [--vus 40] [--seconds 60]

One uvicorn process (one worker, the way the MVP runs) on a throwaway database `moim_load` seeded with the fixture
team. Virtual users loop over what an open group screen really does: the execution API (the screen polls it), the
role screen API, the group page; every 10th iteration also loads the dashboard. A separate phase measures real
Argon2id logins in parallel. Reports latency percentiles per route, error rate, throughput, the database connection
count seen during the run, and server-side errors in the log. Everything is dropped at the end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from tools import ops_db  # noqa: E402

DB = "moim_load"


def pct(values, p):
    if not values:
        return None
    v = sorted(values)
    k = min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))
    return round(v[k], 1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def vu(base, cookies, gid, until, samples, errors):
    async with httpx.AsyncClient(base_url=base, cookies=cookies, follow_redirects=False, timeout=30) as c:
        i = 0
        while time.perf_counter() < until:
            paths = [("execution", f"/api/groups/{gid}/execution"), ("roles", f"/api/groups/{gid}/roles"),
                     ("group_page", f"/groups/{gid}")]
            if i % 10 == 0:
                paths.append(("dashboard", "/dashboard"))
            for name, path in paths:
                t0 = time.perf_counter()
                try:
                    r = await c.get(path)
                    ok = r.status_code == 200
                except httpx.HTTPError:
                    ok = False
                samples.setdefault(name, []).append((time.perf_counter() - t0) * 1000)
                if not ok:
                    errors[name] = errors.get(name, 0) + 1
            i += 1
            await asyncio.sleep(0.5)  # a user is not a tight loop; the real screen polls every few seconds


async def login_phase(base, n, password, usernames):
    lat, bad = [], 0

    async def one(u):
        nonlocal bad
        async with httpx.AsyncClient(base_url=base, follow_redirects=False, timeout=60) as c:
            await c.get("/login")
            t0 = time.perf_counter()
            r = await c.post("/api/auth/login", json={"username": u, "password": password, "keep": False},
                             headers={"X-CSRF-Token": c.cookies.get("moim_csrf"), "Origin": base})
            lat.append((time.perf_counter() - t0) * 1000)
            bad += r.status_code != 200

    await asyncio.gather(*(one(usernames[i % len(usernames)]) for i in range(n)))
    return lat, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vus", type=int, default=40)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--logins", type=int, default=20)
    a = ap.parse_args()
    admin = os.environ.get("MOIM_ADMIN_URL", "")
    if not admin or make_url(admin).host not in ("127.0.0.1", "localhost", "::1"):
        print("error: MOIM_ADMIN_URL must point to the local cluster", file=sys.stderr)
        return 1
    url = make_url(admin).set(database=DB).render_as_string(hide_password=False)
    work = Path(tempfile.mkdtemp(prefix="moim-load-"))

    def admin_exec(stmt):
        with psycopg.connect(**ops_db._conninfo(admin), autocommit=True) as c:
            c.execute(stmt)

    admin_exec(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(DB)))
    admin_exec(sql.SQL("create database {}").format(sql.Identifier(DB)))
    env = {**os.environ, "MOIM_ENV": "development", "MOIM_DATABASE_URL": url, "MOIM_UPLOAD_DIR": str(work / "up"),
           "WEMEET_OFFLINE": "1", "PYTHONIOENCODING": "utf-8", "MOIM_LOG_FORMAT": "json"}
    proc = None
    out: dict = {"vus": a.vus, "seconds": a.seconds, "machine": f"{os.cpu_count()} logical CPUs (developer PC)"}
    try:
        subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
                       env=env, check=True, capture_output=True)
        os.environ.update({k: env[k] for k in ("MOIM_ENV", "MOIM_DATABASE_URL", "MOIM_UPLOAD_DIR")})
        from tools.capture_screens import apply_dev_seed
        from tools.seed_execution import seed_execution
        seed = apply_dev_seed()
        seed_execution(seed)
        gid = seed["vars"]["active_id"]
        from moim.auth import passwords
        from moim.auth.sessions import SessionStore
        from moim.config import load_settings
        from moim.db import make_engine, make_session_factory
        from sqlalchemy import text
        eng = make_engine(load_settings())
        pw = "LoadSample123"
        with eng.begin() as c:
            members = [r[0] for r in c.execute(text("select user_id from cohort_members where group_id=:g"), {"g": gid})]
            h = passwords.hash_password(pw)
            c.execute(text("update users set password_hash=:h where id = any(:ids)"), {"h": h, "ids": members})
            usernames = [r[0] for r in c.execute(text("select username from users where id = any(:ids)"), {"ids": members})]
        store = SessionStore(make_session_factory(eng))
        tokens = [store.create(members[i % len(members)], False)[0] for i in range(a.vus)]
        eng.dispose()

        port = free_port()
        log = open(work / "server.log", "wb")
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "moim.main:app_factory", "--host",
                                 "127.0.0.1", "--port", str(port), "--no-access-log"],
                                env={**env, "PYTHONPATH": str(ROOT / "src")}, stdout=log, stderr=log)
        base = f"http://127.0.0.1:{port}"
        for _ in range(150):
            try:
                if httpx.get(base + "/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)

        samples: dict = {}
        errors: dict = {}
        peak = {"connections": 0}

        async def watch(until):
            while time.perf_counter() < until:
                with psycopg.connect(**ops_db._conninfo(admin)) as c:
                    n = c.execute("select count(*) from pg_stat_activity where datname=%s", (DB,)).fetchone()[0]
                peak["connections"] = max(peak["connections"], n)
                await asyncio.sleep(1)

        async def run():
            until = time.perf_counter() + a.seconds
            await asyncio.gather(watch(until), *(vu(base, {"moim_session": t}, gid, until, samples, errors) for t in tokens))

        t0 = time.perf_counter()
        asyncio.run(run())
        wall = time.perf_counter() - t0
        total = sum(len(v) for v in samples.values())
        out["requests"] = total
        out["throughput_rps"] = round(total / wall, 1)
        out["error_rate"] = round(sum(errors.values()) / total, 4) if total else None
        out["errors"] = errors
        out["routes"] = {k: {"n": len(v), "p50_ms": pct(v, 50), "p95_ms": pct(v, 95), "max_ms": round(max(v), 1),
                             "mean_ms": round(statistics.mean(v), 1)} for k, v in samples.items()}
        out["peak_db_connections"] = peak["connections"]
        lat, bad = asyncio.run(login_phase(base, a.logins, pw, usernames))
        out["login"] = {"n": len(lat), "concurrent": a.logins, "failed": bad, "p50_ms": pct(lat, 50),
                        "p95_ms": pct(lat, 95), "max_ms": round(max(lat), 1)}
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(10)
        log_text = (work / "server.log").read_text(encoding="utf-8", errors="replace") if (work / "server.log").exists() else ""
        out["server_error_lines"] = sum(1 for line in log_text.splitlines() if '"level": "ERROR"' in line or "Traceback" in line)
        out["pool_timeouts"] = log_text.count("QueuePool limit")
        admin_exec(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(DB)))
        out["cleanup"] = "load database and server removed"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
