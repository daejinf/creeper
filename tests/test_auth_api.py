"""Service-level auth tests against the DB at WEMEET_AUTH_DATABASE_URL.

Each test uses unique usernames; everything created is deleted afterwards.
"""

import datetime as dt
import os
import uuid

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

pytestmark = pytest.mark.skipif(not os.getenv("WEMEET_AUTH_DATABASE_URL"), reason="WEMEET_AUTH_DATABASE_URL not set")

PW = "test-password-1"


@pytest.fixture(scope="module")
def engine():
    from wemeet_backend import db

    db.reset_engine()
    yield db.get_engine()
    db.reset_engine()


@pytest.fixture
def conn(engine):
    """A committing connection factory scope; tracks usernames for cleanup."""
    created: list[str] = []
    with engine.connect() as c:
        c.track = created.append
        yield c
    from wemeet_backend.auth.schema import users

    try:
        with engine.begin() as c:
            c.execute(sa.delete(users).where(users.c.username.in_(created)))  # sessions cascade
    except sa.exc.DBAPIError as e:  # least-privilege role cannot delete users
        pytest.fail(f"cleanup failed, test users left behind: {created} ({type(e.orig).__name__})")


def uname() -> str:
    return "t_" + uuid.uuid4().hex[:12]


def signup(c, username, password=PW):
    from wemeet_backend.auth import service
    from wemeet_backend.auth.models import SignupRequest

    c.track(username)
    with c.begin():
        return service.signup(c, SignupRequest(username=username, password=password, nickname="닉네임"))


def login(c, username, password):
    from wemeet_backend.auth import service
    from wemeet_backend.auth.models import LoginRequest

    with c.begin():
        return service.login(c, LoginRequest(username=username, password=password))


def me(c, token):
    from wemeet_backend.auth import service

    with c.begin():
        return service.authenticate(c, token)


def test_signup_creates_user_and_live_session(conn):
    from wemeet_backend.auth.schema import auth_sessions, users

    u = uname()
    res = signup(conn, u)
    assert res.user.username == u and res.token
    assert me(conn, res.token).username == u
    row = conn.execute(sa.select(users.c.password_hash, users.c.last_login_at).where(users.c.username == u)).one()
    assert row.password_hash.startswith("scrypt$") and PW not in row.password_hash
    assert row.last_login_at is not None
    stored = conn.execute(sa.select(auth_sessions.c.session_hash)).scalars().all()
    assert res.token not in stored  # only the SHA-256 is stored
    conn.rollback()


def test_signup_normalizes_username_and_rejects_duplicates(conn):
    from wemeet_backend.auth import service
    from wemeet_backend.auth.models import SignupRequest

    u = uname()
    signup(conn, u)
    with pytest.raises(service.AuthError) as e:
        signup(conn, u)
    assert (e.value.code, e.value.status) == ("USERNAME_TAKEN", 409)
    with pytest.raises(service.AuthError) as e:  # case-insensitive duplicate
        signup(conn, "  " + u.upper() + " ")
    assert e.value.code == "USERNAME_TAKEN"
    assert SignupRequest(username=" ABC_1 ", password=PW, nickname="닉네임").username == "abc_1"


@pytest.mark.parametrize(
    "username,password",
    [("ab", PW), ("a" * 21, PW), ("bad name", PW), ("한글아이디", PW), ("valid_name", "short")],
)
def test_signup_validation(username, password):
    from wemeet_backend.auth.models import SignupRequest

    with pytest.raises(ValidationError):
        SignupRequest(username=username, password=password, nickname="닉네임")


def test_models_forbid_extra_and_hide_password():
    from wemeet_backend.auth.models import LoginRequest, SignupRequest

    with pytest.raises(ValidationError):
        LoginRequest(username="abc", password=PW, admin=True)
    assert PW not in repr(SignupRequest(username="abc", password=PW, nickname="닉네임"))
    assert PW not in repr(LoginRequest(username="abc", password=PW))


def test_login_success_issues_fresh_session_each_time(conn):
    u = uname()
    first = signup(conn, u)
    a = login(conn, u.upper(), PW)
    b = login(conn, u, PW)
    assert len({first.token, a.token, b.token}) == 3
    assert me(conn, a.token).user_id == first.user.user_id


def test_login_failures_are_indistinguishable(conn):
    from wemeet_backend.auth import service

    u = uname()
    signup(conn, u)
    errors = []
    for name, pw in [(u, "wrong-password"), (uname(), PW), ("", PW)]:
        with pytest.raises(service.AuthError) as e:
            login(conn, name, pw)
        errors.append((e.value.code, e.value.message, e.value.status))
    assert len(set(errors)) == 1 and errors[0][0] == "INVALID_CREDENTIALS" and errors[0][2] == 401


def test_unknown_user_login_still_runs_a_hash(conn, monkeypatch):
    from wemeet_backend.auth import passwords, service

    calls = []
    real = passwords.verify_password
    monkeypatch.setattr(passwords, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    with pytest.raises(service.AuthError):
        login(conn, uname(), PW)
    assert calls == [passwords.dummy_hash()]


def test_missing_or_garbage_token_is_unauthenticated(conn):
    from wemeet_backend.auth import service

    for tok in (None, "", "garbage", service.new_token()):
        with pytest.raises(service.AuthError) as e:
            me(conn, tok)
        assert (e.value.code, e.value.status) == ("UNAUTHENTICATED", 401)


def test_expired_session_rejected_even_before_purge(conn):
    from wemeet_backend.auth import service, store
    from wemeet_backend.auth.schema import auth_sessions

    u = uname()
    res = signup(conn, u)
    token = service.new_token()
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    with conn.begin():
        store.create_session(
            conn, service.hash_token(token), res.user.user_id, 7, created_at=past, expires_at=past + dt.timedelta(hours=1)
        )
    with pytest.raises(service.AuthError) as e:
        me(conn, token)
    assert e.value.code == "UNAUTHENTICATED"
    # row still exists (not purged) yet is rejected
    assert conn.execute(
        sa.select(sa.func.count()).where(auth_sessions.c.session_hash == service.hash_token(token))
    ).scalar() == 1
    conn.rollback()
    assert me(conn, res.token).username == u  # other live session unaffected


def test_logout_deletes_only_that_session_and_is_idempotent(conn):
    from wemeet_backend.auth import service

    u = uname()
    s1 = signup(conn, u)
    s2 = login(conn, u, PW)
    with conn.begin():
        service.logout(conn, s1.token)
    with pytest.raises(service.AuthError):
        me(conn, s1.token)
    assert me(conn, s2.token).username == u
    with conn.begin():  # repeated, unknown, and missing tokens: no error
        service.logout(conn, s1.token)
        service.logout(conn, "unknown")
        service.logout(conn, None)


def test_deleting_user_cascades_sessions(conn):
    from wemeet_backend.auth.schema import auth_sessions, users

    u = uname()
    res = signup(conn, u)
    with conn.begin():
        conn.execute(sa.delete(users).where(users.c.user_id == res.user.user_id))
    assert conn.execute(sa.select(sa.func.count()).where(auth_sessions.c.user_id == res.user.user_id)).scalar() == 0
    conn.rollback()
