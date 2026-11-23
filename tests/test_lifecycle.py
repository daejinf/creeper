"""Stage 6 deterministic contract: mypage, password change, leaving a group (D-D1 D6) and deleting an account (D7).

The AI provider is the stage 5 FIXTURE client (never LIVE); this stage makes no AI call of its own. Every destructive
operation is checked three ways: what it removed, what it kept on purpose (the team's records, the assignment record
as a "탈퇴한 사용자" tombstone), and that nothing outside its scope changed (a full row dump of an unrelated team is
compared before and after, and its files stay on disk).
"""
from __future__ import annotations

import json
import logging
import re
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from moim.account.lifecycle import LifecycleService
from moim.auth import passwords
from moim.db import metadata
from test_execution import ai, app, confirmed_team, ex, mk, png, submit, uploads  # noqa: F401
from test_groups import code_of, db, fake, grp_db, join, post, room_of, scalar  # noqa: F401
from test_reassign import ask
from test_role_assignment import ROOT, SECRET_CONSTRAINT, forbidden_in, rank, rows, screen, seed, team  # noqa: F401

PW = "oldpass123"
NEW_PW = "newpass456"
LEFT_NAME = "나간 팀원"


# ---- helpers ----------------------------------------------------------------------------------------------------------


def set_pw(db, u, pw=PW):
    with db.begin() as c:
        c.execute(text("update users set password_hash=:h where id=:i"), {"h": passwords.hash_password(pw), "i": u.id})


def jpost(u, path, body=None, token=...):
    return post(u, path, body if body is not None else {}, token=token)


def leave(u, gid, confirm=True, token=...):
    return jpost(u, f"/api/groups/{gid}/leave", {"confirm": confirm}, token=token)


def delete_account(u, pw=PW, confirm=True, token=...):
    return jpost(u, "/api/account/delete", {"password": pw, "confirm": confirm}, token=token)


def first_todo(u, gid):
    return ex(u, gid)["my_todos"][0]


def with_record(u, gid, files=1, done=True):
    t = first_todo(u, gid)
    r = submit(u, gid, task=t, files=[(f"proof{i}.png", png(40, 30)) for i in range(files)], title="증빙", done=done)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def group_tables():
    """Tables holding group data. `lifecycle_events` is the metadata-only audit log, kept on purpose (D7 보관)."""
    return [t for t in metadata.sorted_tables if "group_id" in t.c and t.name != "lifecycle_events"]


def group_row_count(db, gid):
    n = scalar(db, "select count(*) from groups where id=:g", g=gid)
    for t in group_tables():
        n += scalar(db, f"select count(*) from {t.name} where group_id=:g", g=gid)
    return n


def dump(db, gid, user_ids):
    """Every row that belongs to one group or its users, as text: the 'backup fixture' of an unrelated team."""
    out = {}
    with db.connect() as c:
        for t in metadata.sorted_tables:
            if "group_id" in t.c:
                q, p = f"select t::text from {t.name} t where group_id=:g order by 1", {"g": gid}
            elif t.name == "users":
                q, p = "select t::text from users t where id = any(:u) order by 1", {"u": list(user_ids)}
            elif t.name == "auth_sessions":
                # token and owner only: a request may slide `last_seen_at`, which is not data being kept or lost
                q, p = "select token_hash || user_id from auth_sessions where user_id = any(:u) order by 1", {"u": list(user_ids)}
            elif t.name == "groups":
                q, p = "select t::text from groups t where id=:g", {"g": gid}
            else:
                continue
            out[t.name] = [r[0] for r in c.execute(text(q), p)]
    return out


def db_mentions(db, needle):
    """Tables whose rows contain `needle` anywhere (row cast to text)."""
    hits = []
    with db.connect() as c:
        for t in metadata.sorted_tables:
            if c.execute(text(f"select count(*) from {t.name} t where t::text like :n"), {"n": f"%{needle}%"}).scalar():
                hits.append(t.name)
    return hits


def other_team(mk, uploads):
    """An unrelated confirmed team with a record and a file: must not change at all."""
    gid, people = confirmed_team(mk, 2, leader="bystand")
    with_record(people[0], gid)
    files = sorted(p.name for p in (uploads / gid).iterdir())
    return gid, people, files


def login(app, username, pw):
    c = TestClient(app, follow_redirects=False)
    c.get("/login")
    return c.post("/api/auth/login", json={"username": username, "password": pw},
                  headers={"X-CSRF-Token": c.cookies.get("moim_csrf")})


# ---- mypage -------------------------------------------------------------------------------------------------------


def test_mypage_needs_login_and_shows_only_my_info(app, mk, db):
    anon = TestClient(app, follow_redirects=False)
    r = anon.get("/mypage")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert anon.get("/api/account").status_code == 401
    u = mk("mypageuser", "<script>x</script>")
    other = mk("someoneelse", "남의이름")
    r = u.c.get("/mypage")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    html = r.text
    assert "mypageuser" in html and "&lt;script&gt;x&lt;/script&gt;" in html and "<script>x</script>" not in html
    assert "이메일" not in html and "park@moim.kr" not in html and "PARK" not in html  # D-F1: no e-mail row, no demo data
    assert "someoneelse" not in html and "남의이름" not in html and other.id not in html
    assert "참여 중인 그룹이 없어요." in html
    assert u.c.get("/api/account").json() == {"username": "mypageuser", "name": "<script>x</script>", "groups": []}


def test_mypage_groups_tab_shows_my_own_progress(app, mk, db):
    gid, (lead, m0) = confirmed_team(mk, 2)
    total = len(ex(lead, gid)["my_todos"])
    with_record(lead, gid, files=1)  # D-H1: completion needs material (the fixture AI passes it)
    g = lead.c.get("/api/account").json()["groups"]
    assert len(g) == 1 and g[0]["members"] == 2 and g[0]["done"] == 1 and g[0]["total"] == total
    html = lead.c.get("/mypage").text
    assert f"내 투두 <b>1/{total}</b> 완료" in html and "2명" in html
    # dashboard and group sidebars link to the mypage again (D-A9 condition)
    assert 'href="/mypage"' in lead.c.get("/dashboard").text and 'href="/mypage"' in lead.c.get(f"/groups/{gid}").text


def test_name_change_needs_csrf_and_follows_the_rules(app, mk, db, caplog):
    u = mk("namechange")
    assert jpost(u, "/api/account/profile", {"name": "새이름"}, token=None).status_code == 403
    assert jpost(u, "/api/account/profile", {"name": "가"}).json()["error"]["message"] == "이름은 2자 이상으로 정해 주세요."
    assert jpost(u, "/api/account/profile", {"name": "a​b"}).status_code == 422  # format character
    assert jpost(u, "/api/account/profile", {"name": "x" * 31}).status_code == 422
    assert jpost(u, "/api/account/profile", {"name": "a", "user_id": "x"}).status_code == 400  # no other fields, no IDOR
    caplog.set_level(logging.INFO)
    r = jpost(u, "/api/account/profile", {"name": "  새   이름 "})
    assert r.status_code == 200 and r.json() == {"name": "새 이름"}
    assert scalar(db, "select display_name from users where id=:i", i=u.id) == "새 이름"
    assert "새 이름" in u.c.get("/dashboard").text
    assert rows(db, "select kind, detail from lifecycle_events where user_id=:i", i=u.id) == [("PROFILE_UPDATED", None)]
    assert "새 이름" not in caplog.text and "namechange" not in caplog.text


# ---- password -----------------------------------------------------------------------------------------------------


def test_password_change_checks_the_current_password_and_rotates_every_session(app, mk, db, caplog):
    u = mk("pwchanger")
    set_pw(db, u)
    old_token = u.c.cookies.get("moim_session")
    other_device, _ = app.state.sessions.create(u.id, True)
    body = {"current_password": PW, "new_password": NEW_PW, "new_password_confirm": NEW_PW}
    assert jpost(u, "/api/account/password", body, token=None).status_code == 403
    r = jpost(u, "/api/account/password", {**body, "current_password": "wrongpass1"})
    assert r.status_code == 401 and r.json()["error"] == {"code": "WRONG_PASSWORD", "message": "현재 비밀번호가 맞지 않아요.",
                                                          "field": "current_password"}
    assert jpost(u, "/api/account/password", {**body, "new_password_confirm": "different9"}).status_code == 422
    r = jpost(u, "/api/account/password", {**body, "new_password": "short1", "new_password_confirm": "short1"})
    assert r.status_code == 422 and r.json()["error"]["field"] == "new_password"
    stored = scalar(db, "select password_hash from users where id=:i", i=u.id)
    assert passwords.verify_password(stored, PW)  # nothing changed so far
    caplog.set_level(logging.INFO)
    r = jpost(u, "/api/account/password", body)
    assert r.status_code == 200 and re.fullmatch(r"[A-Za-z0-9_-]{43}", r.json()["csrf_token"])
    new_hash = scalar(db, "select password_hash from users where id=:i", i=u.id)
    assert new_hash.startswith("$argon2id$") and passwords.verify_password(new_hash, NEW_PW)
    assert not passwords.verify_password(new_hash, PW) and not passwords.needs_rehash(new_hash)
    # every earlier session ended, this device got a fresh one, and the returned CSRF token is the live one
    assert u.c.cookies.get("moim_session") != old_token
    assert app.state.sessions.lookup(old_token) is None and app.state.sessions.lookup(other_device) is None
    assert scalar(db, "select count(*) from auth_sessions where user_id=:i", i=u.id) == 1
    assert u.c.cookies.get("moim_csrf") == r.json()["csrf_token"]
    assert u.c.get("/api/account").status_code == 200
    assert login(app, "pwchanger", PW).status_code == 401 and login(app, "pwchanger", NEW_PW).status_code == 200
    for secret in (PW, NEW_PW, "pwchanger"):
        assert secret not in caplog.text
    assert ("PASSWORD_CHANGED", u.id) in rows(db, "select kind, user_id from lifecycle_events")


def test_wrong_current_password_is_rate_limited_and_the_lock_is_no_oracle(app, mk, db):
    u = mk("pwlocked")
    set_pw(db, u)
    body = {"current_password": "wrongpass1", "new_password": NEW_PW, "new_password_confirm": NEW_PW}
    codes = [jpost(u, "/api/account/password", body).status_code for _ in range(10)]
    assert codes[:9] == [401] * 9 and codes[9] == 429
    r = jpost(u, "/api/account/password", {**body, "current_password": PW})
    assert r.status_code == 429 and "Retry-After" in r.headers  # even the right password is refused while locked
    assert delete_account(u).status_code == 429  # the same counter guards the deletion
    assert passwords.verify_password(scalar(db, "select password_hash from users where id=:i", i=u.id), PW)


# ---- D6: leaving a group --------------------------------------------------------------------------------------------


def test_leave_without_confirmation_changes_nothing_and_shows_the_impact(app, mk, db, uploads):
    gid, (lead, m0) = confirmed_team(mk, 2)
    with_record(m0, gid)
    before = dump(db, gid, [lead.id, m0.id])
    stranger = mk("stranger")
    for g in (gid, str(uuid.uuid4()), "not-a-uuid"):  # someone else's group, a missing one, junk: all the same 404
        assert stranger.c.get(f"/api/groups/{g}/leave").status_code == 404
        assert leave(stranger, g).status_code == 404
    assert leave(m0, gid, token=None).status_code == 403
    r = leave(m0, gid, confirm=False)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFIRM_REQUIRED"
    impact = r.json()["error"]["impact"]
    assert impact == m0.c.get(f"/api/groups/{gid}/leave").json()["impact"]
    assert impact["is_leader"] is False and impact["deletes_group"] is False and impact["successor_name"] is None
    assert impact["kept_records"] == 1 and impact["kept_files"] == 1 and impact["removed_todos"] >= 1
    lead_view = lead.c.get(f"/api/groups/{gid}/leave").json()["impact"]
    assert lead_view["is_leader"] and lead_view["successor_name"] == m0.name
    assert dump(db, gid, [lead.id, m0.id]) == before


def test_a_member_who_leaves_loses_access_and_their_private_data_while_the_team_keeps_their_records(app, mk, db, uploads, caplog):
    gid, (lead, m0, m1) = confirmed_team(mk, 3)
    rid = with_record(m0, gid)
    fid = next(f["files"][0]["id"] for f in ex(m0, gid)["feed"] if f["id"] == rid)
    lead_before = rows(db, "select * from role_assignment_members where group_id=:g and user_id<>:u order by user_id", g=gid, u=m0.id)
    caplog.set_level(logging.INFO)
    r = leave(m0, gid)
    assert r.status_code == 200 and r.json() == {"left": True, "group_deleted": False, "redirect": "/dashboard"}
    # access ends at once: page, every API, files; leaving again is a 404 too
    assert m0.c.get(f"/groups/{gid}").status_code == 404
    for path in (f"/api/groups/{gid}/execution", f"/api/groups/{gid}/roles", f"/api/groups/{gid}/todos",
                 f"/api/groups/{gid}/files/{fid}", f"/api/groups/{gid}/brief", f"/api/groups/{gid}/leave"):
        assert m0.c.get(path).status_code == 404, path
    assert leave(m0, gid).status_code == 404
    assert submit(m0, gid, title="몰래", memo="x").status_code == 404
    # private data deleted; the snapshot key stays with its content erased; the cohort row is history only
    for t in ("role_interview_turns", "role_member_states", "todo_ai_steps", "todo_actions", "member_progress",
              "interview_turns", "member_states"):
        assert scalar(db, f"select count(*) from {t} where group_id=:g and user_id=:u", g=gid, u=m0.id) == 0, t
    snap = rows(db, "select provider_input, private_constraints, deadline_limits, purged_at from role_interview_snapshots"
                    " where group_id=:g and user_id=:u", g=gid, u=m0.id)
    assert snap and all(s[0] == {} and s[1] == {} and s[2] == {} and s[3] is not None for s in snap)
    assert "피그마로 시안 두 번" not in json.dumps([list(map(str, x)) for x in rows(
        db, "select t::text from role_interview_snapshots t where group_id=:g and user_id=:u", g=gid, u=m0.id)])
    assert scalar(db, "select count(*) from cohort_members where group_id=:g and user_id=:u", g=gid, u=m0.id) == 1
    assert scalar(db, "select count(*) from group_members where group_id=:g and user_id=:u", g=gid, u=m0.id) == 0
    # the record and its file stay, shown as "나간 팀원" without name, id or key; their open todos leave the screen
    v = ex(lead, gid)
    left = [f for f in v["feed"] if f["who"] == "left"]
    assert len(left) == 1 and left[0]["id"] is None and len(left[0]["files"]) == 1
    assert (uploads / gid / fid).is_file()
    assert lead.c.get(f"/api/groups/{gid}/files/{fid}").status_code == 200
    assert len(v["members"]) == 2 and m0.name not in json.dumps(v, ensure_ascii=False)
    assert len(lead.c.get(f"/api/groups/{gid}/todos").json()["assignments"]) == 2
    forbidden_in(json.dumps(v, ensure_ascii=False) + lead.c.get(f"/api/groups/{gid}/roles").text, [m0])
    # nobody else's assignment changed (no re-draw)
    assert rows(db, "select * from role_assignment_members where group_id=:g and user_id<>:u order by user_id",
                g=gid, u=m0.id) == lead_before
    assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 1
    assert ("LEAVE", m0.id, gid) in rows(db, "select kind, user_id, group_id from lifecycle_events")
    assert m0.name not in caplog.text


def test_the_leader_hands_over_to_the_earliest_joined_remaining_member(app, mk, db):
    gid, (lead, m0, m1) = confirmed_team(mk, 3)
    assert m0.c.get(f"/groups/{gid}/brief/edit").status_code == 403
    page = m0.c.get(f"/groups/{gid}").text
    assert "그룹 나가기" in page and "프로젝트 정보 수정" not in page
    assert leave(lead, gid).status_code == 200
    assert rows(db, "select user_id, role from group_members where group_id=:g order by role", g=gid) == [
        (m0.id, "LEADER"), (m1.id, "MEMBER")]
    assert scalar(db, "select owner_id from groups where id=:g", g=gid) == m0.id
    assert m0.c.get(f"/groups/{gid}/brief/edit").status_code == 200 and "프로젝트 정보 수정" in m0.c.get(f"/groups/{gid}").text
    assert ("LEADER_HANDOFF", m0.id) in rows(db, "select kind, user_id from lifecycle_events where group_id=:g", g=gid)
    assert next(m for m in ex(m1, gid)["members"] if m["name"] == m0.name)["leader"] is True


def test_the_last_member_leaving_deletes_the_whole_group_and_nothing_else(app, mk, db, uploads):
    bgid, bpeople, bfiles = other_team(mk, uploads)
    backup = dump(db, bgid, [p.id for p in bpeople])
    gid, (lead, m0) = confirmed_team(mk, 2)
    with_record(lead, gid)
    with_record(m0, gid)
    assert leave(m0, gid).status_code == 200
    r = leave(lead, gid)
    assert r.status_code == 200 and r.json()["group_deleted"] is True
    assert group_row_count(db, gid) == 0 and not (uploads / gid).exists()
    assert ("GROUP_DELETED", gid, "LAST_MEMBER_LEFT") in rows(db, "select kind, group_id, detail from lifecycle_events")
    # out of scope: the unrelated team and both accounts
    assert dump(db, bgid, [p.id for p in bpeople]) == backup
    assert sorted(p.name for p in (uploads / bgid).iterdir()) == bfiles
    assert scalar(db, "select count(*) from users where id = any(:u)", u=[lead.id, m0.id]) == 2


def test_leaving_before_the_assignment_completes_the_cohort_instead_of_waiting(app, mk, db):
    gid, (lead, m0, m1) = team(mk, 3)
    assert rank(lead, gid, ["r1", "r2", "r3"]).status_code == 201
    assert rank(m0, gid, ["r2", "r1", "r3"]).status_code == 201
    assert screen(lead, gid)["progress"] == {"submitted": 2, "total": 3}
    assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 0
    assert leave(m1, gid).status_code == 200
    assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 1
    assert sorted(rows(db, "select user_id from role_assignment_members where group_id=:g", g=gid)) == sorted(
        [(lead.id,), (m0.id,)])
    assert scalar(db, "select count(*) from preference_submissions where group_id=:g and user_id=:u", g=gid, u=m1.id) == 0
    assert screen(lead, gid)["phase"] == "ROLE_TODOS"


def test_an_open_reassignment_request_involving_the_leaver_is_superseded(app, mk, db):
    gid, (lead, m0, m1) = confirmed_team(mk, 3)
    r = ask(lead, gid, m1.id)
    assert r.status_code == 201, r.text
    assert leave(m1, gid).status_code == 200
    assert scalar(db, "select state from role_reassignments where group_id=:g", g=gid) == "SUPERSEDED"
    # the leaver can never be a reassignment target again
    assert ask(lead, gid, m1.id).status_code == 422


# ---- D7: deleting the account ----------------------------------------------------------------------------------------


def test_deletion_needs_the_password_and_a_confirmation(app, mk, db):
    gid, (lead, m0) = confirmed_team(mk, 2)
    set_pw(db, m0)
    before = dump(db, gid, [lead.id, m0.id])
    assert delete_account(m0, token=None).status_code == 403
    r = delete_account(m0, pw="wrongpass1")
    assert r.status_code == 401 and r.json()["error"]["message"] == "비밀번호가 맞지 않아요."
    r = delete_account(m0, confirm=False)
    assert r.status_code == 409 and r.json()["error"]["impact"] == m0.c.get("/api/account/delete").json()["impact"]
    assert r.json()["error"]["impact"]["groups"] == 1
    after = dump(db, gid, [lead.id, m0.id])
    assert {k: v for k, v in after.items() if k != "auth_throttle"} == {k: v for k, v in before.items() if k != "auth_throttle"}
    assert scalar(db, "select count(*) from users where id=:i", i=m0.id) == 1


def test_deleting_an_account_removes_it_everywhere_and_tombstones_the_assignment_record(app, mk, db, uploads, caplog):
    bgid, bpeople, bfiles = other_team(mk, uploads)
    backup = dump(db, bgid, [p.id for p in bpeople])
    gid, (lead, gone, m1) = confirmed_team(mk, 3, leader="tomb")
    set_pw(db, gone)
    rid = with_record(gone, gid, files=2)
    gone_files = [f["id"] for f in next(f for f in ex(gone, gid)["feed"] if f["id"] == rid)["files"]]
    with_record(lead, gid)
    slot = scalar(db, "select slot_id from role_assignment_members where group_id=:g and user_id=:u", g=gid, u=gone.id)
    impact = gone.c.get("/api/account/delete").json()["impact"]
    assert impact == {"groups": 1, "handoffs": [], "deleted_groups": [], "records": 1, "files": 2}
    caplog.set_level(logging.INFO)
    r = delete_account(gone)
    assert r.status_code == 200 and r.json() == {"deleted": True, "redirect": "/"}
    # the account, its sessions and its username are gone at once
    assert scalar(db, "select count(*) from users where id=:i", i=gone.id) == 0
    assert gone.c.get("/api/account").status_code == 401 and gone.c.get("/mypage").status_code == 303
    assert login(app, gone.name, PW).status_code == 401
    assert mk(gone.name).id  # the username can be taken again right away
    # nothing anywhere points at the deleted id; the kept assignment record points at a fresh tombstone
    assert db_mentions(db, gone.id) == []
    tomb = rows(db, "select user_id from role_assignment_members where group_id=:g and slot_id=:s", g=gid, s=slot)[0][0]
    assert tomb != gone.id
    assert rows(db, "select username, display_name, deleted_at is not null from users where id=:t", t=tomb) == [
        (None, "탈퇴한 사용자", True)]
    assert scalar(db, "select count(*) from cohort_members where group_id=:g and user_id=:t", g=gid, t=tomb) == 1
    assert scalar(db, "select count(*) from execution_records where user_id=:t", t=tomb) == 0
    assert all(not (uploads / gid / f).exists() for f in gone_files)
    assert ("ACCOUNT_DELETED", tomb) in rows(db, "select kind, user_id from lifecycle_events")
    # the team keeps working and shows nothing of the deleted account
    v = ex(lead, gid)
    assert len(v["members"]) == 2 and all(f["who"] != "left" for f in v["feed"]) and len(v["feed"]) == 1
    blob = json.dumps(v, ensure_ascii=False) + lead.c.get(f"/api/groups/{gid}/roles").text + lead.c.get(
        f"/api/groups/{gid}/todos").text + m1.c.get("/mypage").text
    assert gone.id not in blob and tomb not in blob and "탈퇴한 사용자" not in blob
    assert not re.search(rf"(?<![0-9a-z]){gone.name}(?![0-9a-z])", blob)
    assert gone.name not in caplog.text and PW not in caplog.text
    # out of scope: the unrelated team, row for row, and its files
    assert dump(db, bgid, [p.id for p in bpeople]) == backup
    assert sorted(p.name for p in (uploads / bgid).iterdir()) == bfiles


def test_deleting_an_account_also_removes_records_left_behind_in_groups_left_earlier(app, mk, db, uploads):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="earlier")
    set_pw(db, m0)
    with_record(m0, gid)
    assert leave(m0, gid).status_code == 200
    assert [f["who"] for f in ex(lead, gid)["feed"]] == ["left"]
    assert delete_account(m0).status_code == 200
    assert ex(lead, gid)["feed"] == [] and scalar(db, "select count(*) from execution_files where group_id=:g", g=gid) == 0
    assert db_mentions(db, m0.id) == []


def test_deleting_an_account_deletes_groups_with_nobody_left_and_leaves_waiting_rooms(app, mk, db, uploads):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="solo")
    set_pw(db, lead)
    with_record(lead, gid)
    assert leave(m0, gid).status_code == 200       # lead is alone in the group now
    room_owner = mk("roomowner")                   # and waits in someone else's invite room
    room = room_of(room_owner)
    assert join(lead, code_of(room_owner, room)).status_code == 201
    impact = lead.c.get("/api/account/delete").json()["impact"]
    name = scalar(db, "select name from project_briefs where group_id=:g order by revision desc limit 1", g=gid)
    assert impact["deleted_groups"] == [name] and impact["groups"] == 2 and impact["handoffs"] == []
    assert delete_account(lead).status_code == 200
    assert group_row_count(db, gid) == 0 and not (uploads / gid).exists()
    assert rows(db, "select user_id from group_members where group_id=:g", g=room) == [(room_owner.id,)]
    assert db_mentions(db, lead.id) == []
    assert scalar(db, "select count(*) from users where id=:i", i=m0.id) == 1  # the one who left earlier is untouched


def test_deleting_the_owner_of_an_invite_room_hands_it_to_the_waiting_member(app, mk, db):
    owner = mk("roomboss")
    set_pw(db, owner)
    room = room_of(owner)
    code = code_of(owner, room)
    waiting = mk("roomwaiter")
    assert join(waiting, code).status_code == 201
    lone = mk("lonely")
    set_pw(db, lone)
    lone_room = room_of(lone)
    closed_owner = mk("closer")
    set_pw(db, closed_owner)
    closed = room_of(closed_owner)
    assert jpost(closed_owner, f"/api/groups/{closed}/cancel", {"confirm": True}).status_code in (200, 204)
    imp = owner.c.get("/api/account/delete").json()["impact"]
    assert imp["handoffs"] == [{"group_name": "초대방", "successor_name": "roomwaiter"}]
    assert delete_account(owner).status_code == 200
    assert rows(db, "select owner_id, status from groups where id=:g", g=room) == [(waiting.id, "FORMING")]
    assert rows(db, "select role from group_members where group_id=:g", g=room) == [("LEADER",)]
    assert waiting.c.get(f"/api/groups/{room}/room").json()["code"]  # the waiting member now leads the room
    assert delete_account(lone).status_code == 200 and group_row_count(db, lone_room) == 0
    assert delete_account(closed_owner).status_code == 200 and group_row_count(db, closed) == 0


def test_a_failure_in_the_middle_changes_nothing_and_is_not_reported_as_done(app, mk, db, uploads, monkeypatch):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="halfway")
    set_pw(db, m0)
    rid = with_record(m0, gid)
    fid = next(f for f in ex(m0, gid)["feed"] if f["id"] == rid)["files"][0]["id"]
    before = dump(db, gid, [lead.id, m0.id])

    def boom(*a, **k):
        raise RuntimeError("simulated failure after the deletes")

    monkeypatch.setattr(LifecycleService, "_tombstone", staticmethod(boom))
    with pytest.raises(RuntimeError):
        delete_account(m0)
    monkeypatch.setattr(LifecycleService, "_delete_private", staticmethod(boom))
    with pytest.raises(RuntimeError):
        leave(lead, gid)
    after = dump(db, gid, [lead.id, m0.id])
    assert {k: v for k, v in after.items() if k != "auth_throttle"} == {k: v for k, v in before.items() if k != "auth_throttle"}
    assert (uploads / gid / fid).is_file()                    # files are removed only after a commit
    assert m0.c.get("/api/account").status_code == 200 and m0.c.get(f"/api/groups/{gid}/execution").status_code == 200


# ---- database guards -------------------------------------------------------------------------------------------------


def test_confirmed_todos_stay_immutable_outside_the_lifecycle_purge(app, mk, db):
    gid, _ = confirmed_team(mk, 2, leader="immut")
    with pytest.raises(Exception, match="immutable"):
        with db.begin() as c:
            c.execute(text("delete from todo_confirmed_items where group_id=:g"), {"g": gid})
    with pytest.raises(Exception, match="immutable"):
        with db.begin() as c:
            c.execute(text("update todo_confirmations set action_id='x' where group_id=:g"), {"g": gid})
    with db.connect() as c:  # the switch is transaction-scoped: it works inside one transaction and is gone after it
        tx = c.begin()
        c.execute(text("set local moim.lifecycle_purge = 'on'"))
        assert c.execute(text("delete from todo_confirmed_items where group_id=:g"), {"g": gid}).rowcount > 0
        tx.rollback()
    with pytest.raises(Exception, match="immutable"):
        with db.begin() as c:
            c.execute(text("delete from todo_confirmed_items where group_id=:g"), {"g": gid})


def test_static_scripts_and_template_render_only_text(app):
    from moim.main import STATIC_DIR, TEMPLATES_DIR

    for name in ("mypage.js", "group-leave.js", "portfolio.js"):
        js = (STATIC_DIR / name).read_text(encoding="utf-8")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                    "sessionStorage", "document.cookie"):
            assert bad not in js, (name, bad)
    html = (TEMPLATES_DIR / "mypage.html").read_text(encoding="utf-8")
    assert "<script>" not in html and "|safe" not in html and "innerHTML" not in html


def test_original_mypage_is_untouched_by_the_template(app):
    original = (ROOT / "moim-mypage.html").read_text(encoding="utf-8")
    from moim.main import TEMPLATES_DIR

    tpl = (TEMPLATES_DIR / "mypage.html").read_text(encoding="utf-8")
    # the template keeps the original's markup structure: same tabs, panels, ids and copy
    for needle in ('id="t-info"', 'id="t-pw"', 'id="t-grp"', 'id="t-acc"', "모든 그룹에서 나가고, 내 투두와 기록이 지워져요.",
                   "탈퇴하면 되돌릴 수 없어요. 본인 확인을 위해 현재 비밀번호를 입력해 주세요.", "로그아웃 하시겠습니까?",
                   "그룹마다 내 투두를 얼마나 끝냈는지 볼 수 있어요."):
        assert needle in original and needle in tpl, needle
    assert original[:original.index("</style>")].replace(
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n', '') == tpl[:tpl.index("</style>")].replace(
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="csrf-token" content="{{ csrf_token }}">\n', '')


def test_migration_0012_down_and_up(grp_db):
    cfg = Config(str(ROOT / "alembic.ini"))
    eng = create_engine(grp_db)
    try:
        with eng.begin() as c:
            c.execute(text("truncate users, auth_throttle restart identity cascade"))
        command.downgrade(cfg, "0011")
        assert scalar(eng, "select to_regclass('public.lifecycle_events')") is None
        assert scalar(eng, "select count(*) from information_schema.columns where table_name='users' and column_name='deleted_at'") == 0
        command.upgrade(cfg, "head")
        assert scalar(eng, "select to_regclass('public.lifecycle_events')") is not None
        tid = str(uuid.uuid4())
        with eng.begin() as c:
            c.execute(text("insert into users (id, username, display_name, password_hash, terms_accepted_at, deleted_at)"
                           " values (:i, null, '탈퇴한 사용자', '!', now(), now())"), {"i": tid})
        with pytest.raises(RuntimeError, match="tombstones"):
            command.downgrade(cfg, "0011")
        assert scalar(eng, "select to_regclass('public.lifecycle_events')") is not None  # refused, nothing dropped
        with pytest.raises(Exception):
            with eng.begin() as c:  # a live account must have a username; a tombstone must not
                c.execute(text("insert into users (id, username, display_name, password_hash, terms_accepted_at)"
                               " values (:i, null, 'x', 'x', now())"), {"i": str(uuid.uuid4())})
    finally:
        with eng.begin() as c:
            c.execute(text("truncate users, auth_throttle restart identity cascade"))
        command.upgrade(cfg, "head")
        eng.dispose()
