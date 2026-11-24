"""Stage 7 restore rehearsal on a local PostgreSQL cluster. Development machines only; no AI call (fixture AI, not LIVE).

  MOIM_ADMIN_URL=<superuser url of the LOCAL cluster>  MOIM_BACKUP_KEY=<base64 32 bytes>  python tools/rehearse_restore.py

What it does, end to end, with its own throwaway objects only (database names `moim_rh` / `moim_rh_restore`, roles
`moim_rh_owner` / `moim_rh_app`, a temporary upload directory) - anything else is refused:
  1. provision a database with a separate owner (migrations) role and a restricted app role, migrate as the owner;
  2. prove the app role cannot create, drop or alter tables, disable the immutability triggers, or create roles;
  3. seed a full team as the app role (the screen-capture fixture: ranking -> assignment -> interview -> confirmed
     todos -> records with files) and a real signed-up account with an Argon2id password;
  4. run the production app (`MOIM_ENV=production`, HTTPS test client, Secure `__Host-` cookies) as the app role:
     the production preflight must pass;
  5. encrypted backup -> restore into an empty database -> re-grant -> verify (every file hash, row counts);
  6. on the RESTORED database, as the app role in production mode: log in with the real password, read the team
     screen, download an attachment byte-for-byte, check private boundaries (non-member 404, no other member's private
     interview text or hidden criteria in any response), and write a new record;
  7. deletion replay (D-G1): after the backup, one member leaves and the canary owner deletes their account on the
     ORIGINAL database (written to the deletion ledger). The restore brings both back; `replay-deletions` as the app
     role removes them again (canary gone everywhere, the leaver out of the group), and a second run changes nothing;
  8. drop every rehearsal object. Evidence is printed as JSON (no passwords, keys or user content).
Passwords for the rehearsal roles are random per run and never printed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from tools import ops_db  # noqa: E402

DB, DB_RESTORE = "moim_rh", "moim_rh_restore"
OWNER, APP = "moim_rh_owner", "moim_rh_app"
CANARY = "RHCANARY" + secrets.token_hex(8)
PASSWORD = "Rehearse" + secrets.token_hex(6) + "9"  # the signed-up account's password, random per run


def url_for(admin: str, user: str, pw: str, db: str) -> str:
    return make_url(admin).set(username=user, password=pw, database=db).render_as_string(hide_password=False)


def admin_exec(admin: str, *stmts) -> None:
    with psycopg.connect(**ops_db._conninfo(admin), autocommit=True) as c:
        for s in stmts:
            c.execute(s)


def cleanup(admin: str) -> None:
    for db in (DB, DB_RESTORE):
        admin_exec(admin, sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(db)))
    with psycopg.connect(**ops_db._conninfo(admin), autocommit=True) as c:
        for role in (APP, OWNER):
            if c.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone():
                c.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))


def privilege_probe(app_url: str) -> dict:
    """Each statement must fail with a permission error when run as the app role."""
    probes = {
        "create_table": "create table rh_probe (x int)",
        "drop_table": "drop table users",
        "alter_table": "alter table users add column rh_probe int",
        "disable_trigger": "alter table todo_confirmed_items disable trigger all",
        "truncate": "truncate lifecycle_events",
        "create_role": "create role rh_probe_role",
        "create_schema": "create schema rh_probe",
    }
    out = {}
    with psycopg.connect(**ops_db._conninfo(app_url)) as c:
        for name, stmt in probes.items():
            try:
                c.execute(stmt)
                out[name] = "ALLOWED"
            except psycopg.errors.InsufficientPrivilege:
                out[name] = "denied"
            except psycopg.Error as exc:
                out[name] = "error:" + type(exc).__name__
            c.rollback()
        c.execute("select count(*) from users")
        out["select_rows"] = "allowed"
    return out


def integrity_probe(app_url: str) -> dict:
    """SECURITY.md 6-7, on SEEDED confirmed todos (a row trigger only fires on real rows), as the app role: the purge
    switch must not let an UPDATE through, and skipping triggers must need more than the app role."""
    probes = {
        "purge_switch_update_criteria": ["set local moim.lifecycle_purge = 'on'",
                                         "update todo_confirmed_items set deliverable = deliverable || 'x'"],
        "purge_switch_update_signature": ["set local moim.lifecycle_purge = 'on'",
                                          "update todo_confirmations set signing_kid = '0000000000000000'"],
        "update_criteria": ["update todo_confirmed_items set deliverable = deliverable || 'x'"],
        "skip_triggers": ["set local session_replication_role = replica"],
    }
    out = {}
    with psycopg.connect(**ops_db._conninfo(app_url)) as c:
        rows_ = c.execute("select count(*) from todo_confirmed_items").fetchone()[0]
        out["confirmed_items"] = rows_
        for name, stmts in probes.items():
            try:
                for stmt in stmts:
                    c.execute(stmt)
                out[name] = "ALLOWED"
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException) as exc:
                out[name] = "denied:" + type(exc).__name__  # RaiseException = the immutability trigger refused it
            except psycopg.Error as exc:
                out[name] = "error:" + type(exc).__name__
            c.rollback()
    return out


def signatures(app_url: str) -> dict:
    """tools/verify_signatures.py with public keys only (derived from this run's MOIM_SIGNING_KEY)."""
    sys.path.insert(0, str(ROOT))
    from moim.signing import Verifier, public_key_b64
    from tools import verify_signatures

    result = verify_signatures.run(app_url, Verifier.from_b64([public_key_b64(os.environ["MOIM_SIGNING_KEY"])]))
    return result["counts"]


def run(cmd: list[str], env: dict) -> None:
    subprocess.run(cmd, env=env, check=True, capture_output=True)


def main() -> int:
    admin = os.environ.get("MOIM_ADMIN_URL", "")
    host = make_url(admin).host if admin else ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("error: MOIM_ADMIN_URL must point to the local cluster", file=sys.stderr)
        return 1
    ops_db._key()  # fail early without a backup key
    if not os.environ.get("MOIM_SIGNING_KEY"):  # SECURITY.md 6: one random key per run, for the original and the restore
        from moim.signing import new_private_key_b64
        os.environ["MOIM_SIGNING_KEY"] = new_private_key_b64()
    evidence: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    owner_pw, app_pw = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    work = Path(tempfile.mkdtemp(prefix="moim-rh-"))
    up_a, up_b, backup_file = work / "uploads-a", work / "uploads-b", work / "moim.bak"
    ledger_dir = work / "ledger"
    ledger_dir.mkdir()
    cleanup(admin)
    try:
        os.environ.update({"MOIM_OWNER_PASSWORD": owner_pw, "MOIM_APP_PASSWORD": app_pw})
        ops_db.provision(admin, DB, OWNER, APP)
        owner_url, app_url = url_for(admin, OWNER, owner_pw, DB), url_for(admin, APP, app_pw, DB)
        t0 = time.perf_counter()
        env = {**os.environ, "MOIM_ENV": "development", "MOIM_DATABASE_URL": owner_url}
        run([sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"], env)
        evidence["migrate_seconds"] = round(time.perf_counter() - t0, 1)
        evidence["app_role_probe"] = privilege_probe(app_url)

        # ---- seed as the app role (development mode: the fixture seed refuses production) ----
        os.environ.update({"MOIM_ENV": "development", "MOIM_DATABASE_URL": app_url, "MOIM_UPLOAD_DIR": str(up_a)})
        gid = seed_all()
        evidence["seed"] = "fixture team seeded (fixture AI, not LIVE)"
        evidence["integrity_probe"] = integrity_probe(app_url)
        evidence["signatures_original"] = signatures(app_url)

        # ---- production app on the original database ----
        prod_env = {"MOIM_ENV": "production", "MOIM_DATABASE_URL": app_url, "MOIM_UPLOAD_DIR": str(up_a),
                    "TIMELY_API_KEY": "rehearsal-not-used", "MOIM_DELETION_LEDGER_DIR": str(ledger_dir)}
        os.environ.update(prod_env)
        before = production_checks(gid, signup=True)
        evidence["before_backup"] = before

        # ---- backup / restore / verify ----
        t0 = time.perf_counter()
        evidence["backup"] = ops_db.backup(owner_url, str(up_a), str(backup_file))
        evidence["backup_seconds"] = round(time.perf_counter() - t0, 1)
        evidence["backup_is_encrypted"] = b"PGDMP" not in backup_file.read_bytes()
        # ---- deletions AFTER the backup, on the original database (they must not come back for good) ----
        leaver, deleted = deletion_targets(owner_url, gid)
        evidence["deletions_after_backup"] = perform_deletions(gid, leaver, deleted)
        ops_db.provision(admin, DB_RESTORE, OWNER, APP)
        r_owner, r_app = url_for(admin, OWNER, owner_pw, DB_RESTORE), url_for(admin, APP, app_pw, DB_RESTORE)
        t0 = time.perf_counter()
        ops_db.restore(str(backup_file), r_owner, str(up_b))
        ops_db.grant(r_owner, APP)
        evidence["restore_seconds"] = round(time.perf_counter() - t0, 1)
        evidence["verify"] = ops_db.verify(r_owner, str(up_b), str(backup_file))
        evidence["restored_app_role_probe"] = privilege_probe(r_app)
        evidence["signatures_restored"] = signatures(r_app)
        evidence["restored_integrity_probe"] = integrity_probe(r_app)

        # ---- production app on the RESTORED database ----
        os.environ.update({**prod_env, "MOIM_DATABASE_URL": r_app, "MOIM_UPLOAD_DIR": str(up_b)})
        after = production_checks(gid, signup=False, expect=before)
        evidence["after_restore"] = after

        # ---- D-G1: the restore brought the deletions back; replay them as the app role ----
        evidence["resurrected_before_replay"] = deletion_facts(r_owner, gid, leaver, deleted)
        evidence["replay"] = ops_db.replay_deletions(r_app, str(up_b), str(ledger_dir), dry_run=False)
        evidence["after_replay"] = deletion_facts(r_owner, gid, leaver, deleted)
        evidence["replay_again"] = ops_db.replay_deletions(r_app, str(up_b), str(ledger_dir), dry_run=False)
        rb, ar, ra = evidence["resurrected_before_replay"], evidence["after_replay"], evidence["replay_again"]
        replay_ok = (rb["deleted_user_rows"] == 1 and rb["leaver_is_member"] and rb["canary_rows"] >= 2
                     and evidence["replay"]["applied"] == 2 and ar["deleted_user_rows"] == 0
                     and ar["tables_mentioning_deleted_id"] == 0 and not ar["leaver_is_member"]
                     and ar["leaver_private_turns"] == 0 and ar["canary_rows"] == 0
                     and ra["applied"] == 0 and ra["already"] == 2)
        evidence["replay_passed"] = replay_ok
        ok = (all(v == "denied" for k, v in evidence["app_role_probe"].items() if k != "select_rows")
              and all(v == "denied" for k, v in evidence["restored_app_role_probe"].items() if k != "select_rows")
              and not evidence["verify"]["missing_files"] and not evidence["verify"]["hash_mismatch"]
              and not evidence["verify"]["count_differences"] and evidence["verify"]["schema_matches"]
              and evidence["backup_is_encrypted"] and after["all_passed"] and before["all_passed"]
              and all(str(v).startswith("denied") for p in ("integrity_probe", "restored_integrity_probe")
                      for k, v in evidence[p].items() if k != "confirmed_items")
              and evidence["integrity_probe"]["confirmed_items"] > 0
              and evidence["signatures_original"]["VALID"] > 0 and evidence["signatures_original"]["INVALID"] == 0
              and evidence["signatures_original"]["UNSIGNED"] == 0
              and evidence["signatures_restored"] == evidence["signatures_original"]
              and evidence["deletions_after_backup"]["ledger_lines"] == 2 and replay_ok)
        evidence["result"] = "PASS" if ok else "FAIL"
    finally:
        for k in ("MOIM_OWNER_PASSWORD", "MOIM_APP_PASSWORD", "MOIM_DATABASE_URL"):
            os.environ.pop(k, None)
        _dispose_engines()
        cleanup(admin)
        shutil.rmtree(work, ignore_errors=True)
        evidence["cleanup"] = "rehearsal databases, roles and files removed"
    print(json.dumps(evidence, ensure_ascii=False, indent=1))
    return 0 if evidence.get("result") == "PASS" else 2


_ENGINES: list = []


def deletion_targets(url: str, gid: str) -> tuple[str, str]:
    """(member who will leave, member who will delete the account). The deleted one is the canary owner (the last
    cohort member by display name, as in `production_checks`); the leaver is the second, never the first (the first
    member writes the post-restore record)."""
    with psycopg.connect(**ops_db._conninfo(url)) as c:
        ids = [r[0] for r in c.execute(
            "select u.id from cohort_members m join users u on u.id=m.user_id where m.group_id=%s order by u.display_name",
            (gid,))]
    if len(ids) < 3:
        raise RuntimeError("the rehearsal team needs at least 3 members")
    return ids[1], ids[-1]


def perform_deletions(gid: str, leaver: str, deleted: str) -> dict:
    """Through the production app's own lifecycle service (the routes call the same methods after the password and
    confirmation checks), so the deletion ledger is written exactly as in production."""
    from moim.config import load_settings
    from moim.main import create_app

    app = create_app(load_settings(), goal_client_factory=lambda: None)
    _ENGINES.append(app)
    lc = app.state.lifecycle
    lc.leave_group(leaver, gid, confirm=True)
    lc.delete_account(deleted)
    return {"leave": 1, "account_deleted": 1, "ledger_lines": len(lc.ledger.entries())}


def deletion_facts(url: str, gid: str, leaver: str, deleted: str) -> dict:
    with psycopg.connect(**ops_db._conninfo(url)) as c:
        tables = [r[0] for r in c.execute("select tablename from pg_tables where schemaname='public'")]
        mentions = 0
        for t in tables:
            q = sql.SQL("select count(*) from {} t where t::text like %s").format(sql.Identifier(t))
            if c.execute(q, (f"%{deleted}%",)).fetchone()[0]:
                mentions += 1
        one = lambda q, *a: c.execute(q, a).fetchone()[0]  # noqa: E731
        return {
            "deleted_user_rows": one("select count(*) from users where id=%s", deleted),
            "tables_mentioning_deleted_id": mentions,
            "leaver_is_member": bool(one("select count(*) from group_members where group_id=%s and user_id=%s", gid, leaver)),
            "leaver_private_turns": one("select count(*) from role_interview_turns where group_id=%s and user_id=%s", gid, leaver),
            "canary_rows": one("select (select count(*) from role_interview_turns where user_message like %s) + "
                               "(select count(*) from role_member_states where state::text like %s)",
                               f"%{CANARY}%", f"%{CANARY}%"),
        }


def seed_all() -> str:
    """The screen-capture fixture team (fixture AI, never LIVE), seeded through the app role."""
    from tools.capture_screens import apply_dev_seed
    from tools.seed_execution import seed_execution

    seed = apply_dev_seed()
    if "vars" not in seed:
        raise RuntimeError("seed failed: " + str(seed.get("note")))
    seed_execution(seed)
    return seed["vars"]["active_id"]


def _dispose_engines() -> None:
    for app in _ENGINES:
        try:
            app.state.engine.dispose()
        except Exception:  # noqa: BLE001
            pass
    _ENGINES.clear()


def production_checks(gid: str, signup: bool, expect: dict | None = None) -> dict:
    """The production app as the app role. Returns facts; `all_passed` is the conjunction of every check."""
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from moim.config import load_settings
    from moim.main import create_app
    from test_execution import ExecAI

    settings = load_settings()
    assert settings.env == "production" and settings.secure_cookies
    app = create_app(settings, goal_client_factory=lambda: None, ai_client_factory=ExecAI)
    _ENGINES.append(app)
    checks: dict = {}
    base = "https://moim.rehearsal.localhost"
    with TestClient(app, base_url=base, follow_redirects=False) as anon:  # lifespan runs the production preflight
        checks["preflight_passed"] = True
        eng = app.state.engine
        with eng.connect() as c:
            members = c.execute(text("select u.id, u.display_name from cohort_members m join users u on u.id=m.user_id "
                                     "where m.group_id=:g order by u.display_name"), {"g": gid}).all()
            criteria = [r[0] for r in c.execute(text("select deliverable from todo_confirmed_items where group_id=:g"),
                                                {"g": gid})]
            private = c.execute(text("select user_id, user_message from role_interview_turns where group_id=:g "
                                     "and user_message is not null"), {"g": gid}).all()
            records = c.execute(text("select count(*) from execution_records where group_id=:g"), {"g": gid}).scalar_one()
            files = c.execute(text("select id, sha256 from execution_files where group_id=:g order by id"), {"g": gid}).all()
        # A unique canary in ONE member's private interview message and MemberState (written before the backup, so
        # the restored database carries it too). No other member's response may ever contain it.
        canary_owner = members[-1][0]
        if signup:
            with eng.begin() as c:
                c.execute(text("update role_interview_turns set user_message = user_message || :k where group_id=:g "
                               "and user_id=:u and user_message is not null"), {"k": " " + CANARY, "g": gid, "u": canary_owner})
                c.execute(text("update role_member_states set state = state || jsonb_build_object('rh_canary', cast(:k as text)) "
                               "where group_id=:g and user_id=:u"), {"k": CANARY, "g": gid, "u": canary_owner})
        with eng.connect() as c:
            checks["canary_rows"] = c.execute(text(
                "select (select count(*) from role_interview_turns where group_id=:g and user_message like :p) + "
                "(select count(*) from role_member_states where group_id=:g and state::text like :p)"),
                {"g": gid, "p": f"%{CANARY}%"}).scalar_one()
        checks["records"] = records
        checks["files"] = len(files)

        def client_for(uid):
            c = TestClient(app, base_url=base, follow_redirects=False)
            c.get("/login")
            c.cookies.set("__Host-moim_session", app.state.sessions.create(uid, False)[0])
            return c

        # signup + login with the real password (Argon2id hash must survive the restore)
        user = TestClient(app, base_url=base, follow_redirects=False)
        user.get("/signup")
        csrf = lambda c: c.cookies.get("__Host-moim_csrf")  # noqa: E731
        if signup:
            r = user.post("/api/auth/signup", json={"name": "복원확인", "username": "rhcheck01", "password": PASSWORD,
                                                    "password_confirm": PASSWORD, "terms": True},
                          headers={"X-CSRF-Token": csrf(user)})
            checks["signup_status"] = r.status_code
            cookie_hdr = " ".join(r.headers.get_list("set-cookie"))
            checks["cookies_secure_httponly_host_prefix"] = all(
                s in cookie_hdr for s in ("__Host-moim_session", "Secure", "HttpOnly", "SameSite=lax"))
            checks["hsts"] = "strict-transport-security" in r.headers
            user.post("/api/auth/logout", headers={"X-CSRF-Token": csrf(user)})
        user.get("/login")
        r = user.post("/api/auth/login", json={"username": "rhcheck01", "password": PASSWORD, "keep": False},
                      headers={"X-CSRF-Token": csrf(user)})
        checks["login_status"] = r.status_code
        checks["dashboard_status"] = user.get("/dashboard").status_code
        # boundary: the outsider sees the group as not existing
        checks["outsider_execution_status"] = user.get(f"/api/groups/{gid}/execution").status_code
        checks["outsider_group_page_status"] = user.get(f"/groups/{gid}").status_code
        if files:
            checks["outsider_file_status"] = user.get(f"/api/groups/{gid}/files/{files[0][0]}").status_code

        # members: team screen, private boundary, download
        leaks = 0
        details = []
        member_clients = {uid: client_for(uid) for uid, _ in members}
        paths = (f"/api/groups/{gid}/execution", f"/api/groups/{gid}/roles", f"/api/groups/{gid}/todos",
                 f"/groups/{gid}", f"/groups/{gid}/chat")
        for uid, cl in member_clients.items():
            own = {msg for owner, msg in private if owner == uid}  # the fixture answers are the same text for everyone
            for p in paths:
                body = cl.get(p).text
                if uid != canary_owner and CANARY in body:
                    leaks += 1
                    details.append({"kind": "canary", "path": p.replace(gid, "{g}")})
                for d in criteria:
                    if d and d in body:
                        leaks += 1
                        details.append({"kind": "criteria", "path": p.replace(gid, "{g}"), "len": len(d), "sample": d[:30]})
                for owner, msg in private:
                    if (owner != uid and msg and len(msg) >= 6 and msg in body
                            and not any(msg in mine for mine in own if mine)):  # identical fixture text is not a leak
                        leaks += 1
                        details.append({"kind": "private", "path": p.replace(gid, "{g}"), "len": len(msg), "sample": msg[:30]})
        checks["private_or_hidden_leaks"] = leaks
        if details and os.environ.get("RH_DEBUG") == "1":  # fixture text only; for diagnosing the check itself
            checks["leak_details"] = details[:12]
        first = member_clients[members[0][0]]
        ex = first.get(f"/api/groups/{gid}/execution")
        checks["team_screen_status"] = ex.status_code
        if files:
            d = first.get(f"/api/groups/{gid}/files/{files[0][0]}")
            checks["download_status"] = d.status_code
            checks["download_hash_matches"] = hashlib.sha256(d.content).hexdigest() == files[0][1]
            checks["download_is_attachment"] = d.headers.get("content-disposition", "").startswith("attachment")
        # write path as the app role: a new completion record with a file
        task = next((t for t in ex.json().get("my_todos", []) if not t.get("done_on")), None)
        if task:
            from PIL import Image
            img = io.BytesIO()
            Image.new("RGB", (64, 48), (10, 120, 200)).save(img, format="PNG")
            data = {"action_id": uuid.uuid4().hex, "task_id": task["task_id"],
                    "confirmation_revision": str(task["confirmation_revision"]), "marks_done": "true"}
            w = first.post(f"/api/groups/{gid}/records", data=data, files=[("files", ("proof.png", img.getvalue(), "image/png"))],
                           headers={"X-CSRF-Token": first.cookies.get("__Host-moim_csrf")})
            checks["write_record_status"] = w.status_code
            if w.status_code != 201:
                checks["write_record_error"] = (w.json().get("error") or {}).get("code") if w.headers.get(
                    "content-type", "").startswith("application/json") else w.text[:80]
                if os.environ.get("RH_DEBUG") == "1":
                    checks["write_task_keys"] = sorted(task)
        for cl in member_clients.values():
            cl.close()
        user.close()
    passed = [checks.get("preflight_passed"), checks.get("login_status") == 200, checks.get("dashboard_status") == 200,
              checks.get("outsider_execution_status") == 404, checks.get("outsider_group_page_status") in (303, 404),
              checks.get("private_or_hidden_leaks") == 0, checks.get("team_screen_status") == 200,
              checks.get("write_record_status") == 201, checks.get("records", 0) > 0,
              checks.get("canary_rows", 0) >= 2]
    if files:
        passed += [checks.get("outsider_file_status") == 404, checks.get("download_status") == 200,
                   checks.get("download_hash_matches"), checks.get("download_is_attachment")]
    if signup:
        passed += [checks.get("signup_status") in (200, 201), checks.get("cookies_secure_httponly_host_prefix"),
                   checks.get("hsts")]
    if expect:
        # the restored database has exactly what was there at backup time (the pre-backup write added one record)
        passed += [checks["records"] == expect["records"] + 1, checks["files"] == expect["files"] + 1]
    checks["all_passed"] = all(bool(p) for p in passed)
    return checks


if __name__ == "__main__":
    sys.exit(main())
