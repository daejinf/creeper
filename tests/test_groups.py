"""Stage 2 deterministic contract: invite rooms, codes, joining, ProjectBrief, activation, authorization, team goal sentence.

Nothing here calls Timely: the goal sentence uses a FIXTURE client (never reported as LIVE). Every persistence claim
re-reads through a fresh connection or a fresh client.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import dbsafety
from moim.config import load_settings
from moim.groups.goals import GoalService, check_goal, generate_goal
from moim.groups.service import GroupError, MAX_MEMBERS
from moim.groups.validation import BriefData, Milestone
from moim.main import create_app
from moim.timeutil import utcnow
from wemeet_ai.m0.client import AIError
from wemeet_ai.shared.contracts import Usage

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "moim"
NOT_JOINABLE = "이미 시작된 그룹이에요. 그룹장에게 새 초대 가능 여부를 확인해 주세요"
NEED_MEMBERS = "팀원 한 명 이상이 참여하면 그룹을 만들 수 있어요"


# ---- fixtures ---------------------------------------------------------------------------------


class FakeTimely:
    """Deterministic stand-in for the Timely client. is_fixture marks it: never a LIVE result."""

    is_fixture = True

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.script: list = []  # items: a goal string, or an Exception to raise
        self.lock = threading.Lock()

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        with self.lock:
            self.calls.append(messages)
            item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        if item is None:
            content = json.loads(messages[-1]["content"])["project_content"]
            item = "목표: " + content[:20]
        return SimpleNamespace(
            parsed_output=output_schema(goal=item),
            model="fixture-model",
            latency_ms=12,
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


@pytest.fixture(scope="module")
def grp_db(test_database_url):
    mp = pytest.MonkeyPatch()
    mp.setenv("MOIM_ENV", "test")
    mp.setenv("MOIM_DATABASE_URL", test_database_url)
    dbsafety.reset_test_schema(test_database_url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield test_database_url
    mp.undo()


@pytest.fixture()
def db(grp_db):
    eng = create_engine(grp_db)
    with eng.begin() as c:
        c.execute(text("truncate users, auth_throttle restart identity cascade"))
    yield eng
    eng.dispose()


@pytest.fixture()
def fake():
    return FakeTimely()


@pytest.fixture()
def app(db, fake):
    application = create_app(load_settings(), goal_client_factory=lambda: fake)
    yield application
    application.state.engine.dispose()


def user_row(db, app, username: str, display: str | None = None):
    """A user and a signed-in client, made without Argon2 (the auth stage tests signup itself)."""
    uid = str(uuid.uuid4())
    with db.begin() as c:
        c.execute(
            text("insert into users (id, username, display_name, password_hash, terms_accepted_at) values (:i,:u,:d,'x',now())"),
            {"i": uid, "u": username, "d": display or username},
        )
    token, _ = app.state.sessions.create(uid, False)
    client = TestClient(app, follow_redirects=False)
    client.get("/login")  # plants the CSRF cookie
    client.cookies.set("moim_session", token, domain="testserver.local")
    return SimpleNamespace(id=uid, name=username, c=client)


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


def post(u, path, body=None, token=...):
    h = {}
    if token is ...:
        token = u.c.cookies.get("moim_csrf")
    if token:
        h["X-CSRF-Token"] = token
    return u.c.post(path, json=body if body is not None else {}, headers=h)


def today():
    from moim.groups.service import today_kst

    return today_kst(utcnow())


def brief_body(**over):
    t = today()
    body = {
        "name": "캡스톤 디자인",
        "content": "팀플 협업 서비스를 만들고 최종 발표까지 마친다.",
        "start_date": t.isoformat(),
        "end_date": (t + timedelta(days=12)).isoformat(),
        "types": ["보고서", "발표"],
        "milestones": [{"name": "중간 발표", "date": (t + timedelta(days=6)).isoformat()}],
    }
    body.update(over)
    return body


def brief_data(**over) -> BriefData:
    b = brief_body(**over)
    from datetime import date

    return BriefData(
        b["name"], b["content"], date.fromisoformat(b["start_date"]), date.fromisoformat(b["end_date"]), tuple(b["types"]),
        tuple(Milestone(m["name"], date.fromisoformat(m["date"])) for m in b["milestones"]),
    )


def scalar(eng, sql, **params):
    with eng.connect() as c:
        return c.execute(text(sql), params).scalar()


def one(eng, sql, **params):
    with eng.connect() as c:
        return c.execute(text(sql), params).one()


def col(eng, sql, **params):
    with eng.connect() as c:
        return [r[0] for r in c.execute(text(sql), params)]


def room_of(u):
    r = post(u, "/api/groups")
    assert r.status_code in (200, 201), r.text
    return r.json()["group_id"]


def code_of(u, gid) -> str:
    r = u.c.get(f"/api/groups/{gid}/room")
    assert r.status_code == 200
    return r.json()["code"]


def join(u, code):
    return post(u, "/api/groups/join", {"code": code})


def forming_room(mk, n_members=2, leader="leader"):
    """A leader and (n_members - 1) joined members. Returns (gid, leader, [members])."""
    lead = mk(leader)
    gid = room_of(lead)
    code = code_of(lead, gid)
    members = []
    for i in range(n_members - 1):
        m = mk(f"{leader}m{i}")
        assert join(m, code).status_code == 201
        members.append(m)
    return gid, lead, members


def activate(u, gid, **over):
    return post(u, f"/api/groups/{gid}/activate", brief_body(**over))


def active_group(mk, fake=None, n_members=2, leader="leader", **over):
    gid, lead, members = forming_room(mk, n_members, leader)
    r = activate(lead, gid, **over)
    assert r.status_code == 201, r.text
    return gid, lead, members


# ---- migration and database guards ------------------------------------------------------------


def test_migration_0004_down_and_up(grp_db):
    cfg = Config(str(ROOT / "alembic.ini"))
    eng = create_engine(grp_db)
    tables = ("groups", "group_members", "invite_codes", "project_briefs", "activation_cohorts", "cohort_members", "goal_displays")
    try:
        command.downgrade(cfg, "0003")
        for t in tables:
            assert scalar(eng, f"select to_regclass('public.{t}')") is None
        assert scalar(eng, "select to_regclass('public.users')") is not None
        command.upgrade(cfg, "head")
        for t in tables:
            assert scalar(eng, f"select to_regclass('public.{t}')") is not None
    finally:
        eng.dispose()


def test_db_rejects_second_forming_room_for_one_owner(db, mk):
    u = mk("owner01")
    room_of(u)
    with pytest.raises(Exception) as exc:
        with db.begin() as c:
            c.execute(text("insert into groups (id, owner_id, status) values (:i, :o, 'FORMING')"), {"i": str(uuid.uuid4()), "o": u.id})
    assert "uq_groups_owner_forming" in str(exc.value)


def test_db_rejects_active_group_without_brief_and_second_live_code(db, mk):
    u = mk("owner02")
    gid = room_of(u)
    with pytest.raises(Exception) as exc:
        with db.begin() as c:
            c.execute(text("update groups set status='ACTIVE' where id=:g"), {"g": gid})
    assert "active_consistency" in str(exc.value)
    with pytest.raises(Exception) as exc:
        with db.begin() as c:
            c.execute(
                text("insert into invite_codes (id, group_id, code, expires_at) values (:i,:g,'123456', now() + interval '1 day')"),
                {"i": str(uuid.uuid4()), "g": gid},
            )
    assert "uq_invite_codes_live" in str(exc.value)


# ---- creation only through a POST --------------------------------------------------------------


def test_get_pages_never_create_a_group_or_code(db, mk):
    u = mk("viewer1")
    for _ in range(3):
        assert u.c.get("/invite").status_code == 200
        assert u.c.get("/dashboard").status_code == 200
    assert scalar(db, "select count(*) from groups") == 0
    assert scalar(db, "select count(*) from invite_codes") == 0
    assert "초대코드 입력" in u.c.get("/invite").text
    assert 'id="genCode"' not in u.c.get("/invite").text  # no code is shown to someone without a room


def test_post_creates_forming_room_and_reuses_it(db, mk):
    u = mk("owner03")
    r1 = post(u, "/api/groups")
    assert r1.status_code == 201 and r1.json()["reused"] is False and r1.json()["redirect"] == "/invite"
    gid = r1.json()["group_id"]
    code = code_of(u, gid)
    r2 = post(u, "/api/groups")
    assert r2.status_code == 200 and r2.json()["reused"] is True and r2.json()["group_id"] == gid
    assert scalar(db, "select count(*) from groups") == 1
    assert code_of(u, gid) == code  # reuse never reissues the code
    assert u.c.get("/invite").status_code == 200 and code_of(u, gid) == code  # nor does a plain view
    row = one(db, "select status, owner_id from groups")
    assert row.status == "FORMING" and row.owner_id == u.id
    assert scalar(db, "select role from group_members where group_id=:g and user_id=:u", g=gid, u=u.id) == "LEADER"
    assert len(code) == 6 and code.isdigit()


def test_concurrent_creates_make_exactly_one_room(db, app, mk):
    u = mk("owner04")
    results, errors = [], []

    def go():
        try:
            results.append(app.state.groups.create_or_reuse_room(u.id))
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len({gid for gid, _ in results}) == 1 and sum(1 for _, reused in results if not reused) == 1
    assert scalar(db, "select count(*) from groups where status='FORMING'") == 1


def test_create_requires_login_and_csrf(db, app, mk):
    anon = TestClient(app, follow_redirects=False)
    anon.get("/login")
    r = anon.post("/api/groups", json={}, headers={"X-CSRF-Token": anon.cookies.get("moim_csrf")})
    assert r.status_code == 401
    u = mk("owner05")
    assert post(u, "/api/groups", token=None).status_code == 403
    assert post(u, "/api/groups", token="x" * 43).status_code == 403
    assert scalar(db, "select count(*) from groups") == 0


# ---- dashboard / group entry points ------------------------------------------------------------


def test_new_group_buttons_post_and_never_link_to_group_create(db, mk, fake):
    gid, lead, _ = active_group(mk)
    for path in ("/dashboard", f"/groups/{gid}"):
        html = lead.c.get(path).text
        assert 'id="newGroup"' in html and "<button" in html
        assert "moim-group-create" not in html and "/create" not in html and "group-create" not in html
    js = (SRC / "static" / "shell.js").read_text(encoding="utf-8")
    assert "'/api/groups'" in js and "MoimAuth.post" in js and "/create" not in js
    assert lead.c.get("/group-create").status_code == 404 and lead.c.get("/groups/new").status_code in (404, 405)


def test_project_brief_page_cannot_be_entered_directly(db, mk):
    u = mk("owner06")
    assert u.c.get("/groups/" + str(uuid.uuid4()) + "/create").status_code == 404
    gid = room_of(u)
    r = u.c.get(f"/groups/{gid}/create")  # only one member
    assert r.status_code == 303 and r.headers["location"] == "/invite"
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"
    assert scalar(db, "select count(*) from project_briefs") == 0


def test_invite_page_has_no_demo_behaviours():
    html = (SRC / "templates" / "invite.html").read_text(encoding="utf-8")
    js = (SRC / "static" / "invite.js").read_text(encoding="utf-8")
    for token in ("그룹 생성 완료", "doneTitle", "localStorage", "moimLoggedIn", "?login=", "Math.random() * Math.pow", "PARK", "NAMES"):
        assert token not in html and token not in js, token
    for path in (SRC / "static" / "group-create.js", SRC / "templates" / "group_create.html"):
        assert "group-create-position" not in path.read_text(encoding="utf-8")


# ---- joining ----------------------------------------------------------------------------------


def test_join_with_code_adds_member_and_waiting_state(db, mk):
    lead = mk("lead01")
    gid = room_of(lead)
    code = code_of(lead, gid)
    m = mk("mem001", "민수")
    r = join(m, code)
    assert r.status_code == 201 and r.json() == {"group_id": gid, "joined": True, "redirect": "/invite"}
    assert scalar(db, "select role from group_members where group_id=:g and user_id=:u", g=gid, u=m.id) == "MEMBER"
    again = join(m, code)
    assert again.status_code == 200 and again.json()["joined"] is False
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=gid) == 2
    page = m.c.get("/invite").text
    assert "그룹장이 프로젝트를 설정하면 시작돼요" in page and 'id="genCode"' not in page and code not in page
    room = m.c.get(f"/api/groups/{gid}/room").json()
    assert room["is_leader"] is False and room["code"] is None and room["can_create"] is False
    leader_room = lead.c.get(f"/api/groups/{gid}/room").json()
    assert leader_room["member_count"] == 2 and leader_room["code"] == code


def test_waiting_member_cannot_reach_chat_or_group_screens(db, mk):
    gid, lead, (m,) = forming_room(mk, 2)
    for u in (lead, m):
        assert u.c.get(f"/groups/{gid}/chat").headers["location"] == "/invite"
        assert u.c.get(f"/groups/{gid}").headers["location"] == "/invite"
        assert u.c.get(f"/api/groups/{gid}/brief").status_code == 409
        assert u.c.get(f"/api/groups/{gid}/goal").status_code == 409


def test_join_rejects_unknown_malformed_and_expired_codes(db, mk):
    lead = mk("lead02")
    gid = room_of(lead)
    code = code_of(lead, gid)
    other = mk("other1")
    wrong = "000000" if code != "000000" else "000001"
    r = join(other, wrong)
    assert r.status_code == 404 and r.json()["error"]["code"] == "INVALID_CODE"
    for bad in ("12345", "1234567", "abcdef", "12 456", "", "１２３４５６"):
        assert join(other, bad).status_code == 404
    with db.begin() as c:
        c.execute(text("update invite_codes set expires_at = now() - interval '1 second' where group_id=:g"), {"g": gid})
    r = join(other, code)
    assert r.status_code == 410 and r.json()["error"]["code"] == "CODE_EXPIRED"
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=gid) == 1
    assert lead.c.get(f"/api/groups/{gid}/room").json()["code_expired"] is True


def test_code_guessing_is_rate_limited_and_the_lock_hides_the_answer(db, mk):
    lead = mk("lead03")
    gid = room_of(lead)
    code = code_of(lead, gid)
    guesser = mk("guess1")
    wrong = "000000" if code != "000000" else "000001"
    statuses = [join(guesser, wrong).status_code for _ in range(12)]
    assert statuses[:9] == [404] * 9 and 429 in statuses
    r = join(guesser, code)  # even the right code is refused while locked
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=gid) == 1


def test_member_of_another_room_cannot_join_a_second_one(db, mk):
    g1, lead1, (m,) = forming_room(mk, 2, leader="aaaa1")
    lead2 = mk("bbbb2")
    g2 = room_of(lead2)
    r = join(m, code_of(lead2, g2))
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_IN_ROOM"
    assert scalar(db, "select count(*) from group_members where user_id=:u", u=m.id) == 1
    assert post(m, "/api/groups").status_code == 409  # nor open a room of their own


def test_joining_elsewhere_cancels_only_an_empty_own_room(db, mk):
    lead_a = mk("aaaa3")
    ga = room_of(lead_a)
    code_a = code_of(lead_a, ga)
    lead_b = mk("bbbb3")
    gb = room_of(lead_b)
    r = join(lead_b, code_a)  # lead_b's own room has nobody else: it is closed automatically
    assert r.status_code == 201
    assert scalar(db, "select status from groups where id=:g", g=gb) == "CANCELLED"
    assert scalar(db, "select revoke_reason from invite_codes where group_id=:g", g=gb) == "CANCELLED"
    assert scalar(db, "select count(*) from group_members where group_id=:g and user_id=:u", g=ga, u=lead_b.id) == 1
    assert scalar(db, "select status from groups where id=:g", g=ga) == "FORMING"


def test_joining_elsewhere_never_discards_a_room_that_has_members(db, mk):
    g_target, lead_t, _ = forming_room(mk, 1, leader="tttt1")
    code_t = code_of(lead_t, g_target)
    g_own, lead_o, (member,) = forming_room(mk, 2, leader="oooo1")
    r = join(lead_o, code_t)
    assert r.status_code == 409 and r.json()["error"]["code"] == "OWN_ROOM_HAS_MEMBERS"
    assert scalar(db, "select status from groups where id=:g", g=g_own) == "FORMING"
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=g_own) == 2
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=g_target) == 1
    # explicit cleanup is the leader's own action and needs confirmation
    assert post(lead_o, f"/api/groups/{g_own}/cancel", {"confirm": False}).status_code == 422
    assert scalar(db, "select status from groups where id=:g", g=g_own) == "FORMING"
    assert post(member, f"/api/groups/{g_own}/cancel", {"confirm": True}).status_code == 403
    assert post(lead_o, f"/api/groups/{g_own}/cancel", {"confirm": True}).status_code == 200
    assert scalar(db, "select status from groups where id=:g", g=g_own) == "CANCELLED"
    assert post(lead_o, f"/api/groups/{g_own}/cancel", {"confirm": True}).status_code == 200  # idempotent
    assert join(lead_o, code_t).status_code == 201


def test_cancelled_room_rejects_its_old_code_and_frees_the_creator(db, mk):
    lead = mk("lead04")
    gid = room_of(lead)
    code = code_of(lead, gid)
    assert post(lead, f"/api/groups/{gid}/cancel", {"confirm": True}).status_code == 200
    other = mk("other2")
    assert join(other, code).status_code == 410
    again = post(lead, "/api/groups")  # a cancelled room is not reused: a new one opens
    assert again.status_code == 201 and again.json()["group_id"] != gid
    assert scalar(db, "select count(*) from groups where status='FORMING'") == 1


def test_room_capacity_and_concurrent_joins(db, app, mk):
    lead = mk("lead05")
    gid = room_of(lead)
    code = code_of(lead, gid)
    users = [mk(f"racer{i}") for i in range(MAX_MEMBERS + 2)]
    out = []

    def go(u):
        try:
            out.append(("ok", app.state.groups.join_by_code(u.id, code)))
        except GroupError as exc:
            out.append((exc.code, None))

    threads = [threading.Thread(target=go, args=(u,)) for u in users]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for k, _ in out if k == "ok") == MAX_MEMBERS - 1
    assert sum(1 for k, _ in out if k == "ROOM_FULL") == 3
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=gid) == MAX_MEMBERS


def test_concurrent_joins_of_one_user_to_two_rooms_leave_one_membership(db, app, mk):
    l1, l2 = mk("lead06"), mk("lead07")
    g1, g2 = room_of(l1), room_of(l2)
    c1, c2 = code_of(l1, g1), code_of(l2, g2)
    u = mk("floater")
    out = []

    def go(code):
        try:
            out.append(app.state.groups.join_by_code(u.id, code))
        except GroupError as exc:
            out.append(exc.code)

    threads = [threading.Thread(target=go, args=(c,)) for c in (c1, c2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert scalar(db, "select count(*) from group_members where user_id=:u", u=u.id) == 1
    assert sorted(map(str, out)).count("ALREADY_IN_ROOM") == 1


# ---- code reissue -----------------------------------------------------------------------------


def test_regenerate_keeps_the_group_and_discards_the_old_code(db, mk):
    lead = mk("lead08")
    gid = room_of(lead)
    old = code_of(lead, gid)
    r = post(lead, f"/api/groups/{gid}/code")
    assert r.status_code == 200
    new = r.json()["code"]
    assert new != old and r.json()["group_id"] == gid
    assert scalar(db, "select count(*) from groups") == 1
    assert scalar(db, "select count(*) from group_members") == 1
    assert scalar(db, "select revoke_reason from invite_codes where code=:c", c=old) == "REISSUED"
    assert scalar(db, "select count(*) from invite_codes where group_id=:g and revoked_at is null", g=gid) == 1
    other = mk("other3")
    assert join(other, old).status_code == 410
    assert join(other, new).status_code == 201


def test_only_the_leader_reissues_and_only_while_forming(db, mk):
    gid, lead, (m,) = forming_room(mk, 2)
    assert post(m, f"/api/groups/{gid}/code").status_code == 403
    outsider = mk("outsider")
    assert post(outsider, f"/api/groups/{gid}/code").status_code == 404
    assert activate(lead, gid).status_code == 201
    assert post(lead, f"/api/groups/{gid}/code").status_code == 409


def test_codes_are_never_reused(db, mk):
    lead = mk("lead09")
    gid = room_of(lead)
    seen = {code_of(lead, gid)}
    for _ in range(15):
        seen.add(post(lead, f"/api/groups/{gid}/code").json()["code"])
    assert len(seen) == 16
    assert scalar(db, "select count(distinct code) from invite_codes") == scalar(db, "select count(*) from invite_codes")


# ---- the minimum of two and the create step ---------------------------------------------------


def test_create_button_needs_two_people_and_only_navigates(db, mk):
    lead = mk("lead10")
    gid = room_of(lead)
    r = lead.c.get(f"/api/groups/{gid}/room").json()
    assert r["can_create"] is False and r["need_members_message"] == NEED_MEMBERS
    page = lead.c.get("/invite").text
    assert NEED_MEMBERS in page and 'id="create" type="button" disabled' in page
    assert lead.c.get(f"/groups/{gid}/create").headers["location"] == "/invite"
    m = mk("mem002")
    assert join(m, code_of(lead, gid)).status_code == 201
    r = lead.c.get(f"/api/groups/{gid}/room").json()
    assert r["can_create"] is True
    assert NEED_MEMBERS not in lead.c.get("/invite").text
    before = one(db, "select status, brief_revision, cohort_revision from groups")
    page = lead.c.get(f"/groups/{gid}/create")
    assert page.status_code == 200 and "새 그룹 만들기" in page.text
    after = one(db, "select status, brief_revision, cohort_revision from groups")
    assert tuple(before) == tuple(after) == ("FORMING", 0, 0)
    assert scalar(db, "select count(*) from project_briefs") == 0 and scalar(db, "select count(*) from activation_cohorts") == 0
    assert scalar(db, "select count(*) from invite_codes where revoked_at is null") == 1  # the code is still live
    assert m.c.get(f"/groups/{gid}/create").status_code == 403  # members cannot open the leader's step


def test_activation_needs_two_members_even_when_called_directly(db, mk):
    lead = mk("lead11")
    gid = room_of(lead)
    r = activate(lead, gid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ENOUGH_MEMBERS"
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"
    assert scalar(db, "select count(*) from project_briefs") == 0


# ---- activation -------------------------------------------------------------------------------


def test_activation_is_atomic_and_complete(db, mk, fake):
    gid, lead, (m,) = forming_room(mk, 2)
    code = code_of(lead, gid)
    r = activate(lead, gid)
    assert r.status_code == 201
    assert r.json() == {"status": "ACTIVE", "brief_revision": 1, "redirect": f"/groups/{gid}/chat"}
    g = one(db, "select status, cohort_revision, brief_revision, activated_at from groups where id=:g", g=gid)
    assert (g.status, g.cohort_revision, g.brief_revision) == ("ACTIVE", 1, 1) and g.activated_at is not None
    b = one(db, "select revision, name, content, deliverable_types, milestones, created_by from project_briefs")
    assert b.revision == 1 and b.name == "캡스톤 디자인" and b.deliverable_types == ["보고서", "발표"] and b.created_by == lead.id
    assert [x["name"] for x in b.milestones] == ["중간 발표"]
    cohort = set(col(db, "select user_id from cohort_members where group_id=:g and cohort_revision=1", g=gid))
    assert cohort == {lead.id, m.id}
    assert scalar(db, "select count(*) from activation_cohorts where group_id=:g", g=gid) == 1
    row = one(db, "select revoke_reason, revoked_at from invite_codes where code=:c", c=code)
    assert row.revoke_reason == "ACTIVATED" and row.revoked_at is not None
    assert scalar(db, "select count(*) from invite_codes where revoked_at is null") == 0
    # both cohort members reach their chat
    for u in (lead, m):
        assert u.c.get(f"/groups/{gid}/chat").status_code == 200
    # the waiting member's poll tells the page where to go
    assert m.c.get(f"/api/groups/{gid}/room").json()["next"] == f"/groups/{gid}/chat"


def test_failed_activation_rolls_everything_back(db, app, mk, monkeypatch):
    gid, lead, _ = forming_room(mk, 2)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(type(app.state.groups), "_revoke_live_code", staticmethod(boom))
    with pytest.raises(RuntimeError):
        app.state.groups.activate(lead.id, gid, brief_data())
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"
    for table in ("project_briefs", "activation_cohorts", "cohort_members", "goal_displays"):
        assert scalar(db, f"select count(*) from {table}") == 0, table
    assert scalar(db, "select count(*) from invite_codes where revoked_at is null") == 1


def test_only_the_leader_activates_and_a_brief_is_required(db, mk):
    gid, lead, (m,) = forming_room(mk, 2)
    r = activate(m, gid)
    assert r.status_code == 403
    outsider = mk("outsider2")
    assert activate(outsider, gid).status_code == 404
    for body in ({}, {"name": "x"}, brief_body(name="  "), brief_body(types=[])):
        r = post(lead, f"/api/groups/{gid}/activate", body)
        assert r.status_code in (400, 422), body
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"
    assert scalar(db, "select count(*) from activation_cohorts") == 0


def test_duplicate_and_concurrent_activation_make_one_cohort(db, app, mk):
    gid, lead, (m,) = forming_room(mk, 2)
    out = []

    def go():
        try:
            out.append(("ok", app.state.groups.activate(lead.id, gid, brief_data())))
        except GroupError as exc:
            out.append((exc.code, None))

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [k for k, _ in out].count("ok") == 1 and [k for k, _ in out].count("ALREADY_ACTIVE") == 5
    assert scalar(db, "select count(*) from activation_cohorts") == 1
    assert scalar(db, "select count(*) from cohort_members") == 2
    assert scalar(db, "select count(*) from project_briefs") == 1
    r = activate(lead, gid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_ACTIVE"
    assert scalar(db, "select count(*) from activation_cohorts") == 1


def test_start_date_must_not_be_in_the_past(db, mk):
    gid, lead, _ = forming_room(mk, 2)
    past = (today() - timedelta(days=1)).isoformat()
    r = activate(lead, gid, start_date=past, milestones=[])
    assert r.status_code == 422 and r.json()["error"]["field"] == "period"
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"


def test_active_group_rejects_late_joiners_with_the_approved_message(db, mk):
    gid, lead, _ = forming_room(mk, 2)
    code = code_of(lead, gid)
    assert activate(lead, gid).status_code == 201
    late = mk("late01")
    r = join(late, code)
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "GROUP_NOT_JOINABLE", "message": NOT_JOINABLE}
    assert scalar(db, "select count(*) from group_members where user_id=:u", u=late.id) == 0
    assert scalar(db, "select count(*) from cohort_members") == 2
    assert join(late, code).status_code == 409


def test_chat_is_closed_to_everyone_outside_the_cohort(db, mk):
    gid, lead, (m,) = active_group(mk)
    outsider = mk("outsider3")
    assert outsider.c.get(f"/groups/{gid}/chat").status_code == 404
    assert outsider.c.get(f"/groups/{gid}").status_code == 404
    anon = TestClient(lead.c.app, follow_redirects=False)
    r = anon.get(f"/groups/{gid}/chat")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    # a member added to the live list after activation is not in the cohort
    late = mk("late02")
    with db.begin() as c:
        c.execute(text("insert into group_members (group_id, user_id, role) values (:g, :u, 'MEMBER')"), {"g": gid, "u": late.id})
    assert late.c.get(f"/groups/{gid}/chat").status_code == 403
    assert late.c.get(f"/groups/{gid}").status_code == 404


def test_group_survives_a_new_session_and_a_new_process(db, mk, fake):
    gid, lead, (m,) = active_group(mk)
    fresh_app = create_app(load_settings(), goal_client_factory=lambda: fake)
    try:
        token, _ = fresh_app.state.sessions.create(lead.id, False)
        c = TestClient(fresh_app, follow_redirects=False)
        c.get("/login")
        c.cookies.set("moim_session", token, domain="testserver.local")
        page = c.get(f"/groups/{gid}")
        assert page.status_code == 200 and "캡스톤 디자인" in page.text and "D-12" in page.text
        brief = c.get(f"/api/groups/{gid}/brief").json()
        assert brief["revision"] == 1 and brief["types"] == ["보고서", "발표"] and brief["is_leader"] is True
        assert [x["name"] for x in brief["milestones"]] == ["중간 발표"]
        assert "캡스톤 디자인" in c.get("/dashboard").text
    finally:
        fresh_app.state.engine.dispose()


# ---- authorization matrix ---------------------------------------------------------------------


def test_object_authorization_matrix(db, mk):
    gid, lead, (member,) = active_group(mk)
    other_gid, other_lead, _ = active_group(mk, leader="second", name="다른 그룹")  # a second, unrelated group
    outsider = mk("nobody")
    stale = {"base_revision": 1, **brief_body(name="새 이름")}
    api_get = [f"/api/groups/{gid}/room", f"/api/groups/{gid}/brief", f"/api/groups/{gid}/goal"]
    pages = [f"/groups/{gid}", f"/groups/{gid}/chat", f"/groups/{gid}/create", f"/groups/{gid}/brief/edit"]
    # people from another group and people with no group at all: indistinguishable from a missing object
    for stranger in (outsider, other_lead):
        for url in api_get:
            assert stranger.c.get(url).status_code == 404, url
        for url in pages:
            assert stranger.c.get(url).status_code == 404, url
        for path, body in (
            (f"/api/groups/{gid}/code", {}), (f"/api/groups/{gid}/cancel", {"confirm": True}),
            (f"/api/groups/{gid}/activate", brief_body()), (f"/api/groups/{gid}/brief", stale),
            (f"/api/groups/{gid}/goal/retry", {"brief_revision": 1}),
        ):
            assert post(stranger, path, body).status_code == 404, path
    # a plain member may read, never lead
    for url in (f"/api/groups/{gid}/brief", f"/api/groups/{gid}/goal", f"/groups/{gid}", f"/groups/{gid}/chat"):
        assert member.c.get(url).status_code == 200, url
    assert member.c.get(f"/groups/{gid}/brief/edit").status_code == 403
    assert member.c.get(f"/groups/{gid}/create").status_code == 303 or member.c.get(f"/groups/{gid}/create").status_code == 403
    assert post(member, f"/api/groups/{gid}/brief", stale).status_code == 403
    assert post(member, f"/api/groups/{gid}/goal/retry", {"brief_revision": 1}).status_code == 403
    assert post(member, f"/api/groups/{gid}/cancel", {"confirm": True}).status_code == 403
    assert post(member, f"/api/groups/{gid}/code").status_code == 403
    # the leader's own group is untouched by all of the above
    assert scalar(db, "select brief_revision from groups where id=:g", g=gid) == 1
    assert scalar(db, "select status from groups where id=:g", g=gid) == "ACTIVE"
    assert scalar(db, "select name from project_briefs where group_id=:g and revision=1", g=gid) == "캡스톤 디자인"


def test_unauthenticated_access_is_refused_everywhere(db, app, mk):
    gid, lead, _ = active_group(mk)
    anon = TestClient(app, follow_redirects=False)
    anon.get("/login")
    token = anon.cookies.get("moim_csrf")
    for page in ("/dashboard", "/invite", f"/groups/{gid}", f"/groups/{gid}/chat", f"/groups/{gid}/create", f"/groups/{gid}/brief/edit"):
        r = anon.get(page)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), page
    for url in (f"/api/groups/{gid}/room", f"/api/groups/{gid}/brief", f"/api/groups/{gid}/goal"):
        assert anon.get(url).status_code == 401
    for path in ("/api/groups", "/api/groups/join", f"/api/groups/{gid}/code", f"/api/groups/{gid}/activate", f"/api/groups/{gid}/brief"):
        assert anon.post(path, json={}, headers={"X-CSRF-Token": token}).status_code == 401, path


def test_every_state_change_needs_a_csrf_token(db, mk):
    gid, lead, _ = active_group(mk)
    g2, lead2, _ = forming_room(mk, 2, leader="csrf")
    for path, body, u in (
        ("/api/groups", {}, lead), ("/api/groups/join", {"code": "123456"}, lead), (f"/api/groups/{g2}/code", {}, lead2),
        (f"/api/groups/{g2}/cancel", {"confirm": True}, lead2), (f"/api/groups/{g2}/activate", brief_body(), lead2),
        (f"/api/groups/{gid}/brief", {"base_revision": 1, **brief_body()}, lead), (f"/api/groups/{gid}/goal/retry", {"brief_revision": 1}, lead),
    ):
        assert post(u, path, body, token=None).status_code == 403, path
        assert post(u, path, body, token="y" * 43).status_code == 403, path
        r = u.c.post(path, json=body, headers={"X-CSRF-Token": u.c.cookies.get("moim_csrf"), "Origin": "https://evil.example"})
        assert r.status_code == 403, path
    assert scalar(db, "select status from groups where id=:g", g=g2) == "FORMING"


def test_responses_do_not_leak_ids_or_usernames(db, mk):
    gid, lead, (m,) = forming_room(mk, 2)
    room = lead.c.get(f"/api/groups/{gid}/room")
    blob = room.text
    assert lead.id not in blob and m.id not in blob and "mem" not in json.dumps(room.json()["members"]).replace("leadermem0", "")
    assert set(room.json()["members"][0]) == {"name", "shape", "is_me", "role"}


def test_oversized_and_malformed_bodies(db, mk):
    gid, lead, _ = forming_room(mk, 2)
    big = brief_body(content="가" * 12000)
    r = post(lead, f"/api/groups/{gid}/activate", big)
    assert r.status_code == 413
    r = lead.c.post(f"/api/groups/{gid}/activate", content=b"{not json", headers={"X-CSRF-Token": lead.c.cookies.get("moim_csrf"), "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"
    r = post(lead, f"/api/groups/{gid}/activate", {**brief_body(), "role": "ADMIN"})
    assert r.status_code == 400
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"


# ---- ProjectBrief validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    "over, field",
    [
        ({"name": ""}, "name"),
        ({"name": "가" * 31}, "name"),
        ({"name": "a\u0000b"}, "name"),
        ({"content": "가" * 1001}, "content"),
        ({"end_date": (today() - timedelta(days=3)).isoformat()}, "period"),
        ({"start_date": "2026-13-45"}, "period"),
        ({"types": []}, "types"),
        ({"types": ["가" * 13]}, "types"),
        ({"types": [f"t{i}" for i in range(11)]}, "types"),
        ({"milestones": [{"name": "", "date": today().isoformat()}]}, "milestones"),
        ({"milestones": [{"name": "x" * 21, "date": today().isoformat()}]}, "milestones"),
        ({"milestones": [{"name": "밖", "date": (today() + timedelta(days=99)).isoformat()}]}, "milestones"),
    ],
)
def test_brief_validation_rejects_bad_input(db, mk, over, field):
    gid, lead, _ = forming_room(mk, 2)
    r = activate(lead, gid, **over)
    assert r.status_code in (400, 422), (over, r.text)
    if r.status_code == 422:
        assert r.json()["error"]["field"] == field
    assert scalar(db, "select status from groups where id=:g", g=gid) == "FORMING"


def test_brief_text_is_trimmed_and_types_deduplicated(db, mk):
    gid, lead, _ = forming_room(mk, 2)
    r = activate(lead, gid, name="  앱 개발  ", types=["PPT", "ppt", " 발표 "], content="  내용  ")
    assert r.status_code == 201
    row = one(db, "select name, content, deliverable_types from project_briefs")
    assert (row.name, row.content, row.deliverable_types) == ("앱 개발", "내용", ["PPT", "발표"])


# ---- ProjectBrief revisions -------------------------------------------------------------------


def update(u, gid, base, **over):
    return post(u, f"/api/groups/{gid}/brief", {"base_revision": base, **brief_body(**over)})


def test_leader_revises_the_brief_and_each_revision_is_kept(db, mk, fake):
    gid, lead, (m,) = active_group(mk)
    r = update(lead, gid, 1, name="캡스톤 2", content="두 번째 내용")
    assert r.status_code == 200 and r.json()["brief_revision"] == 2 and r.json()["changed"] is True
    assert scalar(db, "select brief_revision from groups where id=:g", g=gid) == 2
    names = col(db, "select name from project_briefs order by revision")
    assert names == ["캡스톤 디자인", "캡스톤 2"]  # revision 1 is never overwritten
    assert scalar(db, "select count(*) from activation_cohorts") == 1  # a revision does not touch the cohort
    assert m.c.get(f"/api/groups/{gid}/brief").json()["revision"] == 2
    same = update(lead, gid, 2, name="캡스톤 2", content="두 번째 내용")
    assert same.status_code == 200 and same.json() == {"brief_revision": 2, "changed": False, "redirect": f"/groups/{gid}"}
    assert scalar(db, "select count(*) from project_briefs") == 2


def test_stale_base_revision_is_a_conflict_not_an_overwrite(db, mk):
    gid, lead, _ = active_group(mk)
    assert update(lead, gid, 1, name="두 번째").status_code == 200
    r = update(lead, gid, 1, name="낡은 화면에서 저장")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_CONFLICT"
    assert scalar(db, "select name from project_briefs where revision=2") == "두 번째"
    assert scalar(db, "select count(*) from project_briefs") == 2
    assert update(lead, gid, 99, name="미래").status_code == 409


def test_concurrent_brief_updates_have_one_winner(db, app, mk):
    gid, lead, _ = active_group(mk)
    out = []

    def go(i):
        try:
            out.append(app.state.groups.update_brief(lead.id, gid, 1, brief_data(name=f"동시 {i}")))
        except GroupError as exc:
            out.append(exc.code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for o in out if isinstance(o, tuple)) == 1 and out.count("REVISION_CONFLICT") == 4
    assert scalar(db, "select count(*) from project_briefs") == 2
    assert scalar(db, "select brief_revision from groups where id=:g", g=gid) == 2


def test_brief_update_needs_an_active_group(db, mk):
    gid, lead, _ = forming_room(mk, 2)
    r = update(lead, gid, 1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "GROUP_NOT_ACTIVE"
    assert lead.c.get(f"/groups/{gid}/brief/edit").headers["location"] == "/invite"


# ---- team goal sentence -----------------------------------------------------------------------


def goal_view(u, gid):
    return u.c.get(f"/api/groups/{gid}/goal").json()


def test_goal_is_generated_from_the_brief_content_and_stored_with_its_revision(db, mk, fake):
    gid, lead, (m,) = active_group(mk, content="우리는 협업 앱의 MVP를 완성한다")
    assert len(fake.calls) == 1
    sent = json.loads(fake.calls[0][-1]["content"])
    assert sent == {"project_content": "우리는 협업 앱의 MVP를 완성한다"}  # nothing but the content: no name, dates or members
    assert "한국어 한 문장" in fake.calls[0][0]["content"]
    v = goal_view(m, gid)
    assert v["state"] == "SUCCEEDED" and v["text"] == "목표: 우리는 협업 앱의 MVP를 완성한다"[:23] and v["brief_revision"] == 1
    row = one(db, "select state, attempt, model, latency_ms, prompt_tokens, completion_tokens, total_tokens, brief_revision from goal_displays")
    assert tuple(row) == ("SUCCEEDED", 1, "fixture-model", 12, 10, 5, 15, 1)
    page = lead.c.get(f"/groups/{gid}").text
    assert 'id="goalText"' in page and v["text"] in page
    assert "AI 요약" not in page and "AI가" not in page  # no label beyond the original's 팀 목표
    assert page.count("팀 목표") >= 1
    # there is no goal input anywhere
    for path in (f"/groups/{gid}/create", f"/groups/{gid}/brief/edit"):
        assert 'name="goal"' not in lead.c.get(path).text and 'id="goal' not in lead.c.get(path).text
    for table in ("goals", "team_goals", "shared_goals"):
        assert scalar(db, "select to_regclass(:t)", t=f"public.{table}") is None


def test_new_revision_regenerates_and_the_old_sentence_is_never_current(db, mk, fake):
    gid, lead, (m,) = active_group(mk, content="첫 번째 프로젝트 내용입니다")
    first = goal_view(m, gid)["text"]
    fake.script = [AIError("TIMEOUT", "Timely request timed out", True)]
    r = update(lead, gid, 1, content="완전히 다른 두 번째 내용")
    assert r.status_code == 200
    v = goal_view(m, gid)
    assert v["state"] == "FAILED" and v["brief_revision"] == 2 and v["text"] is None and v["error_code"] == "TIMEOUT"
    page = m.c.get(f"/groups/{gid}").text
    assert first not in page and "팀 목표를 만들지 못했어요" in page
    assert json.loads(fake.calls[1][-1]["content"]) == {"project_content": "완전히 다른 두 번째 내용"}  # the new revision only
    assert "첫 번째" not in json.dumps(fake.calls[1], ensure_ascii=False)
    # the first revision's own row is still there, but it is not what anyone sees now
    assert scalar(db, "select state from goal_displays where brief_revision=1") == "SUCCEEDED"


def test_goal_failure_is_stored_visible_and_never_retried_by_itself(db, mk, fake):
    fake.script = [AIError("UPSTREAM_ERROR", "Timely returned HTTP 502", True)]
    gid, lead, (m,) = active_group(mk)
    assert len(fake.calls) == 1  # one call, no automatic second attempt
    v = goal_view(lead, gid)
    assert v["state"] == "FAILED" and v["text"] is None and v["error_code"] == "UPSTREAM_ERROR" and v["can_retry"] is True
    assert goal_view(m, gid)["can_retry"] is False
    assert "Timely" not in json.dumps(v, ensure_ascii=False) and "502" not in v["error_message"]  # no provider detail
    page = lead.c.get(f"/groups/{gid}").text
    assert "팀 목표를 만들지 못했어요. 다시 시도해주세요." in page and 'data-state="FAILED"' in page
    assert len(fake.calls) == 1
    # a member cannot retry, the leader can, and a retry is one new attempt
    assert post(m, f"/api/groups/{gid}/goal/retry", {"brief_revision": 1}).status_code == 403
    r = post(lead, f"/api/groups/{gid}/goal/retry", {"brief_revision": 1})
    assert r.status_code == 202
    assert len(fake.calls) == 2
    v = goal_view(lead, gid)
    assert v["state"] == "SUCCEEDED" and v["attempt"] == 2 and v["can_retry"] is False
    assert post(lead, f"/api/groups/{gid}/goal/retry", {"brief_revision": 1}).status_code == 409  # nothing left to retry
    assert len(fake.calls) == 2


def test_goal_retry_for_an_old_revision_is_refused(db, mk, fake):
    fake.script = [AIError("TIMEOUT", "t", True)]
    gid, lead, _ = active_group(mk)
    assert update(lead, gid, 1, content="두 번째 내용이에요 충분히 길게").status_code == 200
    r = post(lead, f"/api/groups/{gid}/goal/retry", {"brief_revision": 1})
    assert r.status_code == 409
    assert goal_view(lead, gid)["brief_revision"] == 2


def test_empty_content_has_no_goal_and_makes_no_call(db, mk, fake):
    gid, lead, (m,) = active_group(mk, content="   ")
    assert fake.calls == []
    v = goal_view(m, gid)
    assert v["state"] == "EMPTY" and v["text"] is None
    assert "프로젝트 내용을 입력하면 팀 목표가 여기에 표시돼요." in lead.c.get(f"/groups/{gid}").text
    # filling the content later creates a goal for the new revision; clearing it again drops the sentence
    assert update(lead, gid, 1, content="이제 내용이 생겼어요 정말로").status_code == 200
    assert len(fake.calls) == 1 and goal_view(m, gid)["state"] == "SUCCEEDED"
    assert update(lead, gid, 2, content="").status_code == 200
    assert len(fake.calls) == 1 and goal_view(m, gid)["state"] == "EMPTY" and goal_view(m, gid)["text"] is None


@pytest.mark.parametrize("bad", ["", "   ", "줄\n바꿈이 있는 목표 문장입니다", "가" * 81, "네"])
def test_unusable_goal_output_is_a_failure(db, mk, fake, bad):
    fake.script = [bad]
    gid, lead, _ = active_group(mk)
    v = goal_view(lead, gid)
    assert v["state"] == "FAILED" and v["text"] is None and v["error_code"] in ("GOAL_INVALID", "HANDLER_ERROR")


def test_goal_check_and_generator_unit_behaviour():
    assert check_goal("  팀플 앱을 완성하고 발표하기  ") == "팀플 앱을 완성하고 발표하기"
    with pytest.raises(Exception):
        check_goal("a\nb c d e")

    class Boom:
        def complete_json(self, *a, **k):
            raise AIError("AUTH_FAILED", "Timely rejected the API credentials")

    with pytest.raises(Exception) as exc:
        generate_goal(Boom(), "내용")
    assert exc.value.code == "AUTH_FAILED" and "credentials" not in str(exc.value)


def test_interrupted_goals_become_retryable_failures(db, app, mk, fake):
    gid, lead, _ = active_group(mk)
    with db.begin() as c:
        c.execute(text("update goal_displays set state='RUNNING', text=null"))
    assert app.state.goals.fail_interrupted() == 1
    v = goal_view(lead, gid)
    assert v["state"] == "FAILED" and v["error_code"] == "SERVER_RESTARTED" and v["can_retry"] is True
    assert app.state.goals.run(gid, 1) is None  # a FAILED goal is not started again by a stray task
    assert len(fake.calls) == 1


def test_goal_run_is_claimed_once(db, app, mk, fake):
    gid, lead, _ = active_group(mk)
    with db.begin() as c:
        c.execute(text("update goal_displays set state='PENDING', text=null"))
    threads = [threading.Thread(target=app.state.goals.run, args=(gid, 1)) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(fake.calls) == 2  # the activation call plus exactly one claimed rerun
    assert goal_view(lead, gid)["state"] == "SUCCEEDED"


def test_goal_without_timely_credentials_fails_visibly(db, mk, monkeypatch):
    """The real client with no key: a visible FAILED state, never a made-up sentence."""
    from moim.main import create_app as make

    monkeypatch.delenv("TIMELY_API_KEY", raising=False)
    monkeypatch.delenv("WEMEET_OFFLINE", raising=False)
    application = make(load_settings())
    try:
        u1 = user_row(db, application, "real01")
        u2 = user_row(db, application, "real02")
        gid = room_of(u1)
        assert join(u2, code_of(u1, gid)).status_code == 201
        assert activate(u1, gid).status_code == 201
        v = goal_view(u1, gid)
        assert v["state"] == "FAILED" and v["error_code"] == "AUTH_FAILED" and v["text"] is None
    finally:
        application.state.engine.dispose()


# ---- screens ----------------------------------------------------------------------------------


def test_dashboard_lists_only_my_live_groups_with_real_data(db, mk, fake):
    gid, lead, (m,) = active_group(mk, name="내 프로젝트")
    forming_gid = room_of(mk("solo01"))
    other = mk("solo02")
    assert "아직 참여한 그룹이 없어요" in other.c.get("/dashboard").text
    html = lead.c.get("/dashboard").text
    assert "내 프로젝트" in html and "D-12" in html and "2명" in html and f'href="/groups/{gid}"' in html
    assert forming_gid not in html
    solo = user_row(db, lead.c.app, "solo03")
    gid2 = room_of(solo)
    page = solo.c.get("/dashboard").text
    assert "초대 대기" in page and 'href="/invite"' in page and "진행 중인 그룹 <b>0</b>" in page
    assert post(solo, f"/api/groups/{gid2}/cancel", {"confirm": True}).status_code == 200
    assert "초대 대기" not in solo.c.get("/dashboard").text  # a closed room is gone
    assert "아직 참여한 그룹이 없어요" in solo.c.get("/dashboard").text
    assert "내 프로젝트" not in other.c.get("/dashboard").text


def test_user_text_is_escaped_in_every_screen(db, mk, fake):
    payload = '<img src=x onerror=alert(1)>'
    lead = mk("xss001", payload[:30])
    gid = room_of(lead)
    m = mk("xss002", '"><script>alert(2)</script>')
    assert join(m, code_of(lead, gid)).status_code == 201
    assert activate(lead, gid, name="<b>굵게</b>", content="<script>alert(3)</script> 목표").status_code == 201
    pages = [lead.c.get(p).text for p in ("/dashboard", f"/groups/{gid}", f"/groups/{gid}/brief/edit")] + [m.c.get(f"/groups/{gid}").text]
    for html in pages:
        assert "<img src=x" not in html and "<script>alert" not in html and "<b>굵게</b>" not in html
    assert "&lt;b&gt;굵게&lt;/b&gt;" in pages[0]
    edit = pages[2]
    assert "</script><script>" not in edit  # the JSON seed cannot break out of its tag
    seed = edit.split('id="briefData">')[1].split("</script>")[0]
    assert json.loads(seed)["name"] == "<b>굵게</b>" or "\\u003c" in seed


def test_group_page_matches_the_original_structure(db, mk):
    gid, lead, (m,) = active_group(mk)
    html = lead.c.get(f"/groups/{gid}").text
    for needle in ('class="g-head"', 'class="dday"', 'class="g-goal"', 'class="goal-tag"', 'class="members"', 'class="you"'):
        assert needle in html, needle
    # Stage 5 builds the group's execution areas (original structure); the dashboard still has none of them.
    # (Originally "no execution areas before stage 5"; the original's CSS comments are not content.)
    import re

    def body_of(page):
        return re.sub(r"<style>.*?</style>", "", page, flags=re.S)

    dash = body_of(lead.c.get("/dashboard").text)
    for area in ("목표 진척도", "팀 활동", "내 할 일", "업로드", "완료한 할 일", "팀원 완료 기록"):
        assert area in body_of(html), area
    for banned in ("목표 진척도", "팀 활동", "내 할 일", "업로드", "완료한 할 일"):
        assert banned not in dash, banned
    for area in ("최근 팀 소식", "오늘의 할 일", "그룹 마감"):  # D-H1: the original dashboard's news and right panel
        assert area in dash, area
    member_html = m.c.get(f"/groups/{gid}").text
    assert "프로젝트 정보 수정" in html and "프로젝트 정보 수정" not in member_html
    assert "그룹장" in html and "팀원" in html


def test_generated_pages_have_no_inline_scripts_or_storage():
    for name in ("dashboard", "invite", "group_create", "group", "chat", "notice"):
        html = (SRC / "templates" / f"{name}.html").read_text(encoding="utf-8")
        import re

        for m in re.finditer(r"<script([^>]*)>", html):
            attrs = m.group(1)
            assert "src=" in attrs or "application/json" in attrs, (name, attrs)
        assert "innerHTML" not in html and "localStorage" not in html
    for js in ("shell.js", "dashboard.js", "invite.js", "group.js", "group-exec.js"):
        text_ = (SRC / "static" / js).read_text(encoding="utf-8")
        assert "innerHTML" not in text_ and "localStorage" not in text_ and "sessionStorage" not in text_, js
    gc = (SRC / "static" / "group-create.js").read_text(encoding="utf-8")
    assert "localStorage" not in gc and "sessionStorage" not in gc


def test_original_htmls_are_untouched_by_this_stage():
    import subprocess

    out = subprocess.run(
        ["git", "status", "--porcelain", "--", "moim-*.html"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == ""
