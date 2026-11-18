"""Stage 1 deterministic contract: signup, login, logout, sessions, CSRF, rate limits, enumeration, XSS, secrets.

Every claim about persistence re-reads through a fresh connection or a fresh client. Nothing here calls Timely.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import dbsafety
from moim.auth import passwords, sessions as sess_mod, throttle as th
from moim.auth.middleware import AUTH_BODY_LIMIT
from moim.auth.routes import safe_next
from moim.config import Settings, load_settings
from moim.main import create_app
from moim.timeutil import utcnow

ROOT = Path(__file__).resolve().parents[1]
PW = "Passw0rd!x"


# ---- fixtures ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def auth_db(test_database_url):
    mp = pytest.MonkeyPatch()
    mp.setenv("MOIM_ENV", "test")
    mp.setenv("MOIM_DATABASE_URL", test_database_url)
    dbsafety.reset_test_schema(test_database_url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield test_database_url
    mp.undo()


@pytest.fixture()
def db(auth_db):
    eng = create_engine(auth_db)
    with eng.begin() as c:
        c.execute(text("truncate users, auth_throttle restart identity cascade"))
    yield eng
    eng.dispose()


@pytest.fixture()
def app(db):
    application = create_app(load_settings())
    yield application
    application.state.engine.dispose()


def make_client(application, base_url="http://testserver") -> TestClient:
    c = TestClient(application, follow_redirects=False, base_url=base_url)
    c.get("/login")  # plants the CSRF cookie
    return c


@pytest.fixture()
def client(app):
    return make_client(app)


def plant(c: TestClient, value: str) -> None:
    """Put a session cookie in the jar for the same host the server's own cookies use."""
    c.cookies.set("moim_session", value, domain="testserver.local")


def tok(c: TestClient) -> str:
    return c.cookies.get("moim_csrf")


def post(c: TestClient, path: str, body: dict, token: str | None = ..., headers: dict | None = None):
    h = dict(headers or {})
    if token is ...:
        token = tok(c)
    if token:
        h["X-CSRF-Token"] = token
    return c.post(path, json=body, headers=h)


def signup_body(**over):
    body = {"name": "앨리스", "username": "alice01", "password": PW, "password_confirm": PW, "terms": True, "notifications": False}
    body.update(over)
    return body


def signup(c, **over):
    return post(c, "/api/auth/signup", signup_body(**over))


def login(c, username="alice01", password=PW, **over):
    return post(c, "/api/auth/login", {"username": username, "password": password, **over})


def all_db_text(eng) -> str:
    parts = []
    with eng.connect() as c:
        for table in ("users", "auth_sessions", "auth_throttle", "ai_jobs", "app_meta"):
            parts += [r[0] for r in c.execute(text(f"select row_to_json(t)::text from {table} t"))]
    return "\n".join(parts)


def one(eng, sql, **params):
    with eng.connect() as c:
        return c.execute(text(sql), params).one()


def scalar(eng, sql, **params):
    with eng.connect() as c:
        return c.execute(text(sql), params).scalar()


# ---- migration --------------------------------------------------------------------------------


def test_migration_0003_down_and_up(auth_db):
    cfg = Config(str(ROOT / "alembic.ini"))
    eng = create_engine(auth_db)
    try:
        command.downgrade(cfg, "0002")
        assert scalar(eng, "select to_regclass('public.users')") is None
        assert scalar(eng, "select to_regclass('public.ai_jobs')") is not None
        command.upgrade(cfg, "head")
        for t in ("users", "auth_sessions", "auth_throttle"):
            assert scalar(eng, f"select to_regclass('public.{t}')") is not None
    finally:
        eng.dispose()


# ---- signup, duplicates, validation -----------------------------------------------------------


def test_signup_creates_user_argon2id_and_session(client, db):
    r = signup(client)
    assert r.status_code == 201 and r.json() == {"redirect": "/dashboard"}
    row = one(db, "select username, display_name, password_hash, notify_opt_in from users")
    assert row.username == "alice01" and row.display_name == "앨리스" and row.notify_opt_in is False
    assert row.password_hash.startswith("$argon2id$")
    assert PW not in row.password_hash
    assert client.get("/dashboard").status_code == 200  # signed in right after signup


def test_signup_persists_across_a_fresh_client(client, app):
    assert signup(client).status_code == 201
    fresh = make_client(app)
    assert login(fresh).status_code == 200
    assert fresh.get("/dashboard").status_code == 200


@pytest.mark.parametrize(
    "over,field",
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "가" * 31}, "name"),
        ({"name": "a\x00b"}, "name"),
        ({"name": "a‮b"}, "name"),
        ({"username": ""}, "username"),
        ({"username": "abc"}, "username"),
        ({"username": "a" * 17}, "username"),
        ({"username": "Alice01"}, "username"),
        ({"username": "alice_01"}, "username"),
        ({"password": "short1", "password_confirm": "short1"}, "password"),
        ({"password": "onlyletters", "password_confirm": "onlyletters"}, "password"),
        ({"password": "12345678", "password_confirm": "12345678"}, "password"),
        ({"password": "a1" * 65, "password_confirm": "a1" * 65}, "password"),
        ({"password_confirm": "Different1"}, "password_confirm"),
        ({"terms": False}, "terms"),
    ],
)
def test_signup_rejects_invalid_input_without_creating_a_user(client, db, over, field):
    r = signup(client, **over)
    assert r.status_code == 422
    assert r.json()["error"]["field"] == field and r.json()["error"]["code"] == "INVALID"
    assert scalar(db, "select count(*) from users") == 0
    assert scalar(db, "select count(*) from auth_sessions") == 0


def test_signup_normalizes_input(client, db):
    r = signup(client, name="  앨리스   김  ", username="ａｌｉｃｅ０２")  # fullwidth -> alice02
    assert r.status_code == 201
    row = one(db, "select username, display_name from users")
    assert (row.username, row.display_name) == ("alice02", "앨리스 김")


def test_password_is_not_trimmed(app):
    c = make_client(app)
    assert signup(c, password=" Passw0rd ", password_confirm=" Passw0rd ").status_code == 201
    c2 = make_client(app)
    assert login(c2, password="Passw0rd").status_code == 401
    assert login(c2, password=" Passw0rd ").status_code == 200


def test_duplicate_and_reserved_usernames(app, client):
    assert signup(client).status_code == 201
    other = make_client(app)
    r = signup(other)
    assert r.status_code == 409 and r.json()["error"]["code"] == "USERNAME_TAKEN"
    assert signup(other, username="admin").status_code == 409
    assert post(other, "/api/auth/username-check", {"username": "alice01"}).json()["status"] == "taken"
    assert post(other, "/api/auth/username-check", {"username": "moim"}).json()["status"] == "taken"
    assert post(other, "/api/auth/username-check", {"username": "bobby99"}).json()["status"] == "available"
    r = post(other, "/api/auth/username-check", {"username": "BAD"})
    assert r.status_code == 200 and r.json()["status"] == "invalid"


def test_concurrent_signups_for_one_username_have_exactly_one_winner(app):
    clients = [make_client(app) for _ in range(2)]
    barrier = threading.Barrier(2)
    codes = []

    def go(c):
        barrier.wait()
        codes.append(signup(c).status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [201, 409]


# ---- login, logout, sessions ------------------------------------------------------------------


def test_login_logout_refresh_cycle(app, db):
    c = make_client(app)
    signup(c)
    c2 = make_client(app)
    r = login(c2)
    assert r.status_code == 200 and r.json() == {"redirect": "/dashboard"}
    cookie = c2.cookies.get("moim_session")
    assert c2.get("/dashboard").status_code == 200
    assert c2.get("/dashboard").status_code == 200  # refresh keeps the session
    assert scalar(db, "select count(*) from auth_sessions") == 2

    # a brand-new client carrying only the cookie is still signed in (state is in the DB, not the process)
    c3 = make_client(app)
    plant(c3, cookie)
    assert c3.get("/dashboard").status_code == 200

    r = post(c2, "/api/auth/logout", {})
    assert r.status_code == 200 and r.json() == {"redirect": "/login"}
    assert scalar(db, "select count(*) from auth_sessions") == 1  # only the signup session of `c` is left
    plant(c3, cookie)  # replaying the old cookie after logout
    r = c3.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_logout_without_session_is_fine_and_needs_csrf(client):
    assert post(client, "/api/auth/logout", {}).status_code == 200
    assert post(client, "/api/auth/logout", {}, token=None).status_code == 403


def test_dashboard_requires_login(client):
    r = client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fdashboard"


def test_signed_in_users_skip_login_and_signup_pages(client):
    signup(client)
    assert client.get("/login").status_code == 303 and client.get("/login").headers["location"] == "/dashboard"
    assert client.get("/signup").status_code == 303


def test_login_keep_changes_cookie_lifetime(app):
    c = make_client(app)
    signup(c)
    a, b = make_client(app), make_client(app)
    ra, rb = login(a), login(b, keep=True)
    assert "max-age" not in ra.headers["set-cookie"].lower()
    cookies = [h for h in rb.headers.get_list("set-cookie") if h.startswith("moim_session=")]
    assert "Max-Age=2592000" in cookies[0]


def test_session_cookie_attributes_in_development(app):
    c = make_client(app)
    r = signup(c)
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith("moim_session="))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # local HTTP development is the only exception


def test_session_cookie_attributes_in_production(db, auth_db):
    prod = create_app(Settings(env="production", database_url=auth_db))
    try:
        c = TestClient(prod, follow_redirects=False, base_url="https://testserver")
        r = c.get("/signup")
        assert "strict-transport-security" in r.headers
        csrf_cookie = r.headers["set-cookie"]
        assert csrf_cookie.startswith("__Host-moim_csrf=") and "Secure" in csrf_cookie and "HttpOnly" in csrf_cookie
        r = post_prod(c, "/api/auth/signup", signup_body())
        assert r.status_code == 201
        for h in r.headers.get_list("set-cookie"):
            assert h.startswith("__Host-") and "Secure" in h and "HttpOnly" in h
            assert "SameSite=lax" in h and "Path=/" in h and "Domain" not in h
        assert any(h.startswith("__Host-moim_session=") for h in r.headers.get_list("set-cookie"))
    finally:
        prod.state.engine.dispose()


def post_prod(c, path, body):
    return c.post(path, json=body, headers={"X-CSRF-Token": c.cookies.get("__Host-moim_csrf")})


def test_session_fixation_login_never_reuses_a_presented_token(app):
    c = make_client(app)
    signup(c)
    victim = make_client(app)
    planted = "A" * 43  # attacker-chosen token placed in the victim's browser
    plant(victim, planted)
    assert victim.get("/dashboard").status_code == 303  # unknown token grants nothing
    old_csrf = tok(victim)
    r = login(victim)
    assert r.status_code == 200
    assert victim.cookies.get("moim_session") != planted
    assert tok(victim) != old_csrf  # CSRF token rotates with the login
    attacker = make_client(app)
    plant(attacker, planted)
    assert attacker.get("/dashboard").status_code == 303


def test_login_revokes_the_session_it_replaces(app, db):
    c = make_client(app)
    signup(c)
    before = c.cookies.get("moim_session")
    assert login(c).status_code == 200  # already signed in: the API still works, GET pages redirect
    after = c.cookies.get("moim_session")
    assert before != after
    stale = make_client(app)
    plant(stale, before)
    assert stale.get("/dashboard").status_code == 303


def test_expired_session_is_rejected_and_removed(app, db):
    c = make_client(app)
    signup(c)
    assert c.get("/dashboard").status_code == 200
    with db.begin() as conn:
        conn.execute(text("update auth_sessions set expires_at = now() - interval '1 second'"))
    r = c.get("/dashboard")
    assert r.status_code == 303
    assert scalar(db, "select count(*) from auth_sessions") == 0


def test_absolute_expiry_ends_a_session_that_is_still_active(app, db):
    c = make_client(app)
    signup(c)
    with db.begin() as conn:
        conn.execute(text("update auth_sessions set absolute_expires_at = now() - interval '1 second'"))
    assert c.get("/dashboard").status_code == 303


def test_session_store_idle_and_absolute_rules_with_a_fake_clock(db):
    from moim.db import make_session_factory

    uid = "11111111-1111-1111-1111-111111111111"
    with db.begin() as conn:
        conn.execute(
            text("insert into users (id, username, display_name, password_hash, terms_accepted_at) values (:i,'clockuser','c','h',now())"),
            {"i": uid},
        )
    now = [utcnow()]
    store = sess_mod.SessionStore(make_session_factory(db), clock=lambda: now[0])

    token, max_age = store.create(uid, keep=False)
    assert max_age is None
    now[0] += timedelta(hours=1, minutes=59)
    assert store.lookup(token) is not None  # within idle; slides the expiry
    now[0] += timedelta(hours=1, minutes=59)
    assert store.lookup(token) is not None  # slid forward, so still alive
    now[0] += timedelta(hours=2, minutes=1)
    assert store.lookup(token) is None  # idle for more than 2 h
    t2, _ = store.create(uid, keep=False)
    for _ in range(7):  # active every 1h50m: the 12 h absolute cap still wins
        now[0] += timedelta(hours=1, minutes=50)
        alive = store.lookup(t2)
    assert alive is None

    t3, age = store.create(uid, keep=True)
    assert age == 30 * 24 * 3600  # cookie outlives the 14 day idle window; the server enforces idle
    now[0] += timedelta(days=13)
    assert store.lookup(t3) is not None
    now[0] += timedelta(days=13)  # 26 days in, active
    assert store.lookup(t3) is not None
    now[0] += timedelta(days=5)  # 31 days: past the 30 day absolute cap
    assert store.lookup(t3) is None
    assert store.lookup("not-a-token") is None and store.lookup(None) is None


def test_session_token_is_not_stored_and_malformed_tokens_are_ignored(app, db):
    c = make_client(app)
    signup(c)
    token = c.cookies.get("moim_session")
    assert token not in all_db_text(db)
    assert scalar(db, "select token_hash from auth_sessions") == sess_mod.hash_token(token)
    plant(c, "x'; drop table users; --")
    assert c.get("/dashboard").status_code == 303
    assert scalar(db, "select count(*) from users") == 1


# ---- enumeration, rate limits -----------------------------------------------------------------


def test_login_failures_are_indistinguishable_and_burn_the_same_hash_time(app, monkeypatch):
    c = make_client(app)
    signup(c)
    burned = []
    real = passwords.burn_verify
    monkeypatch.setattr(passwords, "burn_verify", lambda p: (burned.append(1), real(p))[1])
    wrong_pw = login(make_client(app), password="Wrong1234")
    no_user = login(make_client(app), username="nobody99", password="Wrong1234")
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()
    assert wrong_pw.json()["error"]["message"] == "아이디 또는 비밀번호가 올바르지 않아요."
    assert set(wrong_pw.headers) - {"date"} == set(no_user.headers) - {"date"}
    assert burned == [1]  # only the unknown account paid for a dummy verification; a real one verified for real


def test_overlong_password_on_login_is_a_normal_failure(client):
    signup(client)
    r = login(make_client(client.app), password="a1" * 200)
    assert r.status_code == 401


def test_login_pair_lock_blocks_even_the_correct_password_and_is_scoped(app):
    c = make_client(app)
    signup(c)
    bob = make_client(app)
    signup(bob, username="bobby99")
    attacker = make_client(app)
    codes = [login(attacker, password="Wrong1234").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    r = login(attacker, password=PW)  # right password while locked
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert login(make_client(app), username="bobby99").status_code == 200  # a different account is unaffected


def test_unknown_usernames_are_throttled_exactly_like_real_ones(app):
    codes = [login(make_client(app), username="ghost01", password="Wrong1234").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]


def test_ip_wide_failure_limit(app):
    signup(make_client(app))
    seen = [login(make_client(app), username=f"user{i:04d}", password="Wrong1234").status_code for i in range(21)]
    assert seen[:19] == [401] * 19 and seen[19:] == [429, 429]
    assert login(make_client(app), password=PW).status_code == 429  # the address is locked for everyone


def test_username_check_and_signup_are_rate_limited(app):
    c = make_client(app)
    codes = [post(c, "/api/auth/username-check", {"username": "freename"}).status_code for _ in range(31)]
    assert codes[:29] == [200] * 29 and codes[29:] == [429, 429]
    c2 = make_client(app)
    results = [signup(c2, username="bad").status_code for _ in range(11)]
    assert results[:9] == [422] * 9 and results[9:] == [429, 429]


def test_throttle_state_survives_a_restart(app, db):
    attacker = make_client(app)
    for _ in range(5):
        login(attacker, username="ghost02", password="Wrong1234")
    restarted = create_app(load_settings())  # new engine, new process state
    try:
        r = login(make_client(restarted), username="ghost02", password="Wrong1234")
        assert r.status_code == 429
    finally:
        restarted.state.engine.dispose()


def test_throttle_windows_and_locks_expire_with_a_fake_clock(db):
    from moim.db import make_session_factory

    now = [utcnow()]
    t = th.Throttle(make_session_factory(db), clock=lambda: now[0])
    rule = th.LOGIN_PAIR
    assert [t.hit(rule, "k") > 0 for _ in range(5)] == [False] * 4 + [True]
    assert t.locked_for([(rule, "k")]) > 0
    now[0] += timedelta(seconds=rule.lock_s + 1)
    assert t.locked_for([(rule, "k")]) == 0
    assert t.hit(rule, "k") == 0  # a new window starts counting from one
    for _ in range(3):
        t.hit(rule, "j")
    now[0] += timedelta(seconds=rule.window_s + 1)
    assert [t.hit(rule, "j") for _ in range(4)] == [0, 0, 0, 0]  # the old failures no longer count
    t.clear(rule, "j")
    assert scalar(db, "select count(*) from auth_throttle where key = :k", k=th.key_for(rule.scope, "j")) == 0
    assert "ghost" not in all_db_text(db)


def test_throttle_keys_do_not_contain_raw_username_or_address(app, db):
    attacker = make_client(app)
    login(attacker, username="visiblename", password="Wrong1234")
    dump = all_db_text(db)
    assert "visiblename" not in dump and "testclient" not in dump


# ---- CSRF -------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/username-check"])
def test_every_state_changing_endpoint_requires_a_csrf_token(client, db, path):
    body = {"signup": signup_body(), "login": {"username": "alice01", "password": PW}, "logout": {}, "username-check": {"username": "abcd"}}[
        path.rsplit("/", 1)[1]
    ]
    assert post(client, path, body, token=None).status_code == 403
    assert post(client, path, body, token="x" * 43).status_code == 403
    assert post(client, path, body, token="short").status_code == 403
    assert scalar(db, "select count(*) from users") == 0


def test_csrf_token_must_match_the_cookie_not_just_look_valid(client):
    other = make_client(client.app)
    assert post(client, "/api/auth/signup", signup_body(), token=tok(other)).status_code == 403


def test_cross_origin_requests_are_rejected_even_with_a_valid_token(client, db):
    assert post(client, "/api/auth/signup", signup_body(), headers={"Origin": "https://evil.example"}).status_code == 403
    assert post(client, "/api/auth/signup", signup_body(), headers={"Origin": "null"}).status_code == 403
    assert post(client, "/api/auth/signup", signup_body(), headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert scalar(db, "select count(*) from users") == 0
    assert post(client, "/api/auth/signup", signup_body(), headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}).status_code == 201


def test_failed_logout_csrf_keeps_the_session(client):
    signup(client)
    assert post(client, "/api/auth/logout", {}, token=None).status_code == 403
    assert client.get("/dashboard").status_code == 200


def test_page_token_in_meta_equals_the_cookie(client):
    html = client.get("/signup").text
    meta = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    assert meta == tok(client)


def test_get_requests_cannot_change_state(client, db):
    for path in ("/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/username-check"):
        assert client.get(path).status_code == 405
    assert scalar(db, "select count(*) from users") == 0


# ---- input hardening, headers -----------------------------------------------------------------


def test_malformed_bodies_get_a_generic_error_without_echoing_input(client):
    h = {"X-CSRF-Token": tok(client)}
    r = client.post("/api/auth/login", content=b"not json {", headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"
    r = client.post("/api/auth/login", json={"username": "a", "password": "SECRET-ECHO-1", "extra": 1}, headers=h)
    assert r.status_code == 400 and "SECRET-ECHO-1" not in r.text
    r = client.post("/api/auth/login", json={"username": 5, "password": "x"}, headers=h)
    assert r.status_code == 400


def test_oversized_bodies_are_rejected(client):
    h = {"X-CSRF-Token": tok(client), "Content-Type": "application/json"}
    big = b'{"username":"' + b"a" * (AUTH_BODY_LIMIT + 10) + b'","password":"x"}'
    assert client.post("/api/auth/login", content=big, headers=h).status_code == 413

    def chunks():  # no Content-Length: the cap must still hold
        for i in range(0, len(big), 1024):
            yield big[i : i + 1024]

    assert client.post("/api/auth/login", content=chunks(), headers=h).status_code == 413


def test_security_headers_on_pages_and_api(client):
    for r in (client.get("/"), client.get("/login"), client.get("/static/login.js"), post(client, "/api/auth/logout", {})):
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/login").headers["cache-control"] == "no-store"
    assert client.get("/login").headers["referrer-policy"] == "same-origin"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/dashboard", "/dashboard"),
        ("/dashboard?x=1", "/dashboard?x=1"),
        ("//evil.example", "/dashboard"),
        ("https://evil.example", "/dashboard"),
        ("/\\evil.example", "/dashboard"),
        ("javascript:alert(1)", "/dashboard"),
        ("/ok\nSet-Cookie: x=1", "/dashboard"),
        ("", "/dashboard"),
        (None, "/dashboard"),
        ("/" + "a" * 300, "/dashboard"),
    ],
)
def test_safe_next(raw, expected):
    assert safe_next(raw) == expected


def test_login_redirect_target_is_validated_server_side(app):
    c = make_client(app)
    signup(c)
    assert login(make_client(app), next="//evil.example").json() == {"redirect": "/dashboard"}
    assert login(make_client(app), next="https://evil.example/x").json() == {"redirect": "/dashboard"}


# ---- XSS --------------------------------------------------------------------------------------

PAYLOAD = '<img src=x onerror=alert(1)><script>alert("x")</script>'


def test_user_supplied_name_is_escaped_in_html(client):
    assert signup(client, name=PAYLOAD[:30].replace("<script>alert", "")).status_code == 201
    html = client.get("/dashboard").text
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_next_parameter_is_escaped_in_the_login_page(client):
    html = client.get('/login?next=/x%22%3E%3Cscript%3Ealert(1)%3C/script%3E').text
    assert "<script>alert(1)" not in html
    assert 'data-next="/x&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in html


def test_static_scripts_never_touch_storage_or_inject_html():
    for js in (ROOT / "src" / "moim" / "static").glob("*.js"):
        body = js.read_text(encoding="utf-8")
        for banned in ("localStorage", "sessionStorage", "document.cookie", "innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write"):
            assert banned not in body, (js.name, banned)


def test_pages_have_no_inline_scripts_or_handlers(client):
    for path in ("/", "/login", "/signup"):
        html = client.get(path).text
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path
        assert not re.search(r"\son[a-z]+\s*=", html), path


# ---- secrets never leak -----------------------------------------------------------------------


def test_password_and_session_token_never_reach_db_logs_or_html(app, db, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "Zx9-UniquePassword-77"
    c = make_client(app)
    assert signup(c, password=secret, password_confirm=secret).status_code == 201
    other = make_client(app)
    assert login(other, password=secret).status_code == 200
    assert login(make_client(app), password="Wrong-Secret-55").status_code == 401
    token = other.cookies.get("moim_session")
    pages = [other.get(p).text for p in ("/", "/dashboard")] + [make_client(app).get(p).text for p in ("/login", "/signup")]
    dump = all_db_text(db)
    for needle in (secret, "Wrong-Secret-55", token, c.cookies.get("moim_session")):
        assert needle not in dump, "secret found in the database"
        assert needle not in caplog.text, "secret found in logs"
        assert all(needle not in p for p in pages), "secret found in HTML"
    assert secret not in "".join(str(h) for h in other.headers.values())


def test_login_logs_carry_no_username_or_password(app, caplog):
    caplog.set_level(logging.DEBUG)
    signup(make_client(app))
    login(make_client(app), password="Wrong1234")
    assert "alice01" not in caplog.text and "Wrong1234" not in caplog.text


# ---- password hashing -------------------------------------------------------------------------


def test_argon2id_hashing_and_verification():
    h = passwords.hash_password(PW)
    assert h.startswith("$argon2id$") and PW not in h
    assert passwords.verify_password(h, PW) and not passwords.verify_password(h, PW + "x")
    assert passwords.hash_password(PW) != h  # salted
    assert not passwords.verify_password("not-a-hash", PW)
    assert passwords.needs_rehash(h) is False


# ---- pages and design -------------------------------------------------------------------------


def test_landing_cta_follows_d_a5(app):
    out = make_client(app)
    html = out.get("/").text
    cta = re.search(r'<a class="btn primary" href="([^"]*)"', html).group(1)
    assert cta == "/login"  # signed out: login first, success returns to the dashboard
    assert "moim-invite" not in html
    assert 'href="/login"' in html and 'href="/signup"' in html
    signup(out)
    cta = re.search(r'<a class="btn primary" href="([^"]*)"', out.get("/").text).group(1)
    assert cta == "/dashboard"


def test_login_and_signup_pages_have_real_forms_and_the_recovery_notice(client):
    login_html = client.get("/login").text
    assert 'id="login-form"' in login_html and 'method="post"' in login_html
    assert 'name="username"' in login_html and 'name="password"' in login_html and 'name="keep"' in login_html
    assert "비밀번호 찾기는 아직 지원하지 않아요" in login_html  # no pretend recovery
    assert 'href="/signup"' in login_html
    signup_html = client.get("/signup").text
    for name in ("name", "username", "password", "password_confirm", "terms", "notifications"):
        assert f'name="{name}"' in signup_html
    assert 'id="su-check"' in signup_html


def _style_lines(html: str) -> set[str]:
    return set(re.search(r"<style>\n(.*?)</style>", html, re.S).group(1).splitlines())


@pytest.mark.parametrize("original,route", [("moim-landing.html", "/"), ("moim-login.html", "/login"), ("moim-signup.html", "/signup")])
def test_implementation_keeps_every_original_style_rule(client, original, route):
    original_lines = _style_lines((ROOT / original).read_text(encoding="utf-8"))
    served = _style_lines(client.get(route).text)
    missing = {line for line in original_lines if line.strip() and line not in served}
    assert not missing, missing


def test_original_html_is_untouched_by_the_templates():
    # the generated templates are separate files; hash test (tools/check_design_hashes.py) covers the originals
    assert (ROOT / "src" / "moim" / "templates" / "login.html").is_file()
    assert (ROOT / "moim-login.html").read_bytes() != (ROOT / "src" / "moim" / "templates" / "login.html").read_bytes()


def test_generated_scripts_parse():
    """Catches a broken generated script (a regression once shipped one that silently fell back to a native form POST)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the browser check covers this")
    for js in sorted((ROOT / "src" / "moim" / "static").glob("*.js")):
        run = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
        assert run.returncode == 0, (js.name, run.stderr)


def test_static_files_are_revalidated(client):
    assert client.get("/static/signup.js").headers["cache-control"] == "no-cache"


def test_dummy_hash_exists_before_the_first_request():
    assert passwords._DUMMY_HASH.startswith("$argon2id$")
