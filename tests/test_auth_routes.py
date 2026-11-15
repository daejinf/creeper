"""HTTP-level auth tests (TestClient). Need WEMEET_AUTH_DATABASE_URL except the /health and config ones."""

import logging
import os
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from wemeet_backend.config import load_settings
from wemeet_backend.main import create_app

PW = "route-test-pass-1"
NEEDS_DB = pytest.mark.skipif(not os.getenv("WEMEET_AUTH_DATABASE_URL"), reason="WEMEET_AUTH_DATABASE_URL not set")
GENERIC = "아이디 또는 비밀번호가 올바르지 않습니다."


def uname() -> str:
    return "t_" + uuid.uuid4().hex[:12]


def make_client(**env):
    base = {"WEMEET_AUTH_DATABASE_URL": os.environ["WEMEET_AUTH_DATABASE_URL"], **env}
    app = create_app(load_settings(base))
    return TestClient(app)


@pytest.fixture
def made():
    names: list[str] = []
    yield names
    if names:
        from wemeet_backend import db
        from wemeet_backend.auth.schema import users

        db.reset_engine()
        with db.get_engine().begin() as c:
            c.execute(sa.delete(users).where(users.c.username.in_(names)))  # sessions cascade
        db.reset_engine()


@pytest.fixture
def client(made):
    with make_client() as c:
        c.made = made
        yield c


def signup(c, username=None, password=PW):
    username = username or uname()
    c.made.append(username)
    return c.post("/api/auth/signup", json={"username": username, "password": password}), username


def cookie_header(r):
    return r.headers.get_list("set-cookie")[0]


@NEEDS_DB
def test_signup_me_logout_me_flow(client):
    r, u = signup(client)
    assert r.status_code == 201
    assert r.json()["status"] == "OK" and r.json()["data"]["user"]["username"] == u
    token = client.cookies.get("wemeet_session")
    assert token and token not in r.text and PW not in r.text

    m = client.get("/api/auth/me")
    assert m.status_code == 200 and m.json()["data"]["user"]["username"] == u
    assert m.headers["cache-control"] == "no-store"

    lo = client.post("/api/auth/logout")
    assert lo.status_code == 200 and lo.json() == {"status": "OK", "data": {}}
    assert "Max-Age=0" in cookie_header(lo) or "expires" in cookie_header(lo).lower()

    # the old token itself must now be dead, not just removed from the jar
    client.cookies.set("wemeet_session", token)
    m2 = client.get("/api/auth/me")
    assert m2.status_code == 401
    assert m2.json() == {
        "status": "FAILED",
        "error": {"code": "UNAUTHENTICATED", "message": "로그인이 필요합니다."},
    }


@NEEDS_DB
def test_cookie_attributes_default(client):
    r, _ = signup(client)
    h = cookie_header(r)
    assert h.startswith("wemeet_session=")
    assert "HttpOnly" in h and "SameSite=lax" in h and "Path=/" in h and "Max-Age=604800" in h
    assert "Secure" not in h and "Domain" not in h


@NEEDS_DB
def test_cookie_attributes_from_env(made):
    env = {
        "WEMEET_AUTH_COOKIE_NAME": "__Host-wemeet_session",
        "WEMEET_AUTH_COOKIE_SECURE": "true",
        "WEMEET_AUTH_COOKIE_SAMESITE": "none",
        "WEMEET_AUTH_SESSION_DAYS": "1",
    }
    with make_client(**env) as c:
        c.made = made
        r, _ = signup(c)
        h = cookie_header(r)
        assert h.startswith("__Host-wemeet_session=")
        assert "Secure" in h and "SameSite=none" in h and "HttpOnly" in h and "Max-Age=86400" in h
    with make_client(WEMEET_AUTH_COOKIE_DOMAIN=".example.com") as c:
        c.made = made
        r, _ = signup(c)
        assert "Domain=.example.com" in cookie_header(r)


@NEEDS_DB
def test_duplicate_signup_409(client):
    _, u = signup(client)
    r, _ = signup(client, u.upper())
    assert r.status_code == 409 and r.json()["error"]["code"] == "USERNAME_TAKEN"
    assert "set-cookie" not in r.headers


@NEEDS_DB
def test_login_success_and_uniform_failure(client):
    _, u = signup(client)
    client.post("/api/auth/logout")
    ok = client.post("/api/auth/login", json={"username": u, "password": PW})
    assert ok.status_code == 200 and client.get("/api/auth/me").status_code == 200

    fails = [
        client.post("/api/auth/login", json={"username": u, "password": "wrong-password"}),
        client.post("/api/auth/login", json={"username": uname(), "password": PW}),
    ]
    bodies = [f.json() for f in fails]
    assert [f.status_code for f in fails] == [401, 401]
    assert bodies[0] == bodies[1]
    assert bodies[0]["error"] == {"code": "INVALID_CREDENTIALS", "message": GENERIC}
    assert all("set-cookie" not in f.headers for f in fails)


@NEEDS_DB
def test_validation_error_does_not_echo_password(client):
    secret = "pw-should-not-echo-123"
    r = client.post("/api/auth/signup", json={"username": "x", "password": secret})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"
    assert secret not in r.text
    r = client.post("/api/auth/login", json={"username": "abc", "password": secret, "extra": 1})
    assert r.status_code == 422 and secret not in r.text
    r = client.post("/api/auth/login", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


@NEEDS_DB
def test_logout_without_session_is_ok_and_me_unauthenticated(client):
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("wemeet_session", "garbage")
    assert client.get("/api/auth/me").status_code == 401


@NEEDS_DB
def test_no_password_or_token_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    r, u = signup(client)
    token = client.cookies.get("wemeet_session")
    client.get("/api/auth/me")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": u, "password": "bad-password-x"})
    text = caplog.text
    assert PW not in text and "bad-password-x" not in text and token not in text


def test_health_needs_no_db_and_no_engine():
    settings = load_settings({"WEMEET_AUTH_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/none"})
    app = create_app(settings)
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "OK"}
    assert app.state.engine is None


def test_importing_main_does_not_require_env(monkeypatch):
    import importlib

    import wemeet_backend.main as main

    monkeypatch.delenv("WEMEET_AUTH_DATABASE_URL", raising=False)
    importlib.reload(main)  # must not raise
    with pytest.raises(Exception) as e:
        main.app
    assert "WEMEET_AUTH_DATABASE_URL" in str(e.value)


DUMMY_URL = "postgresql://u:p@127.0.0.1:1/none"


@pytest.mark.parametrize("flag", [None, "", "false", "0"])
def test_docs_disabled_by_default(flag):
    env = {"WEMEET_AUTH_DATABASE_URL": DUMMY_URL}
    if flag is not None:
        env["WEMEET_ENABLE_DOCS"] = flag
    with TestClient(create_app(load_settings(env))) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_docs_enabled_by_env_and_spec_has_no_secrets():
    env = {"WEMEET_AUTH_DATABASE_URL": "postgresql://u:secretpw@127.0.0.1:1/none", "WEMEET_ENABLE_DOCS": "true"}
    with TestClient(create_app(load_settings(env))) as c:
        assert c.get("/docs").status_code == 200
        spec = c.get("/openapi.json")
        assert spec.status_code == 200 and "/api/auth/signup" in spec.json()["paths"]
        assert "secretpw" not in spec.text


def test_origin_check_and_cors():
    settings = load_settings(
        {
            "WEMEET_AUTH_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/none",
            "WEMEET_CORS_ORIGINS": "http://localhost:3000",
        }
    )
    with TestClient(create_app(settings)) as c:
        evil = c.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403 and evil.json()["error"]["code"] == "FORBIDDEN_ORIGIN"
        assert "access-control-allow-origin" not in evil.headers
        null = c.post("/api/auth/logout", headers={"Origin": "null"})
        assert null.status_code == 403
        # preflight
        ok = c.options(
            "/api/auth/login",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
        )
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert ok.headers["access-control-allow-credentials"] == "true"
        bad = c.options(
            "/api/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
        # same-origin POST (Origin host == Host) is allowed through the check
        same = c.post("/health", headers={"Origin": "http://testserver"})
        assert same.status_code != 403
