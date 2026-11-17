"""nickname: validation (no DB), HTTP behaviour, and DB constraint (need the 0006 migration applied)."""

import os
import unicodedata
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from wemeet_backend.auth.models import NICKNAME_MESSAGE, SignupRequest
from wemeet_backend.config import load_settings
from wemeet_backend.main import create_app

PW = "nick-test-pass-1"
DUMMY_URL = "postgresql://u:p@127.0.0.1:1/none"
NEEDS_DB = pytest.mark.skipif(not os.getenv("WEMEET_AUTH_DATABASE_URL"), reason="WEMEET_AUTH_DATABASE_URL not set")


def uname() -> str:
    return "t_" + uuid.uuid4().hex[:12]


def req(nickname, **kw):
    return SignupRequest(username="abc", password=PW, nickname=nickname, **kw)


# ---- validation (pure) -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nick",
    ["앨리스", "ab", "1234567890", "홍길동1", "a.b-c_1", "Alice", "가나다라마바사아자차", "  앨리스  "],
)
def test_valid_nicknames(nick):
    assert req(nick).nickname == nick.strip()


@pytest.mark.parametrize(
    "nick",
    [
        "",  # empty
        "a",  # too short
        "가",
        "12345678901",  # 11 chars
        "가나다라마바사아자차카",  # 11 Hangul
        "   ",  # whitespace only
        "앨 리스",  # inner space
        "앨\n리스",  # newline
        "앨리스😀",  # emoji
        "앨리스!",  # other symbol
        "<b>hi</b>",
        "ㅋㅋ",  # lone jamo
        "___",  # symbols only
        "...",
        "-_-",
    ],
)
def test_invalid_nicknames(nick):
    with pytest.raises(ValidationError) as e:
        req(nick)
    assert NICKNAME_MESSAGE in str(e.value)


def test_trim_then_length_boundaries():
    assert req("  ab  ").nickname == "ab"
    with pytest.raises(ValidationError):
        req("  a  ")  # 1 char after trim
    assert req(" 1234567890 ").nickname == "1234567890"


def test_nfc_normalization_for_decomposed_hangul():
    decomposed = unicodedata.normalize("NFD", "앨리스")
    assert len(decomposed) > 3  # would break the length rule if not normalized
    assert req(decomposed).nickname == unicodedata.normalize("NFC", "앨리스")
    ten_composed = "가나다라마바사아자차"
    assert req(unicodedata.normalize("NFD", ten_composed)).nickname == ten_composed


def test_case_is_preserved():
    assert req("AbC").nickname == "AbC"


def test_nickname_is_required_in_model_and_openapi():
    with pytest.raises(ValidationError):
        SignupRequest(username="abc", password=PW)
    env = {"WEMEET_AUTH_DATABASE_URL": DUMMY_URL, "WEMEET_ENABLE_DOCS": "true"}
    with TestClient(create_app(load_settings(env))) as c:
        schemas = c.get("/openapi.json").json()["components"]["schemas"]
    assert "nickname" in schemas["SignupRequest"]["required"]
    assert "nickname" in schemas["UserProfileOut"]["required"]
    assert "nickname" not in schemas["UserOut"]["properties"]  # login response shape is unchanged


# ---- HTTP, validation only (422 happens before any DB access) ----------------------------------


@pytest.fixture
def offline_client():
    with TestClient(create_app(load_settings({"WEMEET_AUTH_DATABASE_URL": DUMMY_URL}))) as c:
        yield c


def test_missing_nickname_422_uniform_format(offline_client):
    r = offline_client.post("/api/auth/signup", json={"username": "abc", "password": PW})
    assert r.status_code == 422
    assert r.json() == {
        "status": "FAILED",
        "error": {"code": "INVALID_REQUEST", "message": "필수 항목이 없습니다: nickname"},
    }
    assert PW not in r.text


@pytest.mark.parametrize("nick", ["a", "가나다라마바사아자차카", "   ", "앨 리스", "😀😀"])
def test_bad_nickname_422_uniform_format_and_no_echo(offline_client, nick):
    r = offline_client.post("/api/auth/signup", json={"username": "abc", "password": PW, "nickname": nick})
    assert r.status_code == 422
    assert r.json() == {
        "status": "FAILED",
        "error": {"code": "INVALID_REQUEST", "message": NICKNAME_MESSAGE},
    }
    assert PW not in r.text
    if len(nick.strip()) >= 3:  # a 1-char value would match unrelated letters in the JSON keys
        assert nick.strip() not in r.text


# ---- HTTP + DB ---------------------------------------------------------------------------------


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
    app = create_app(load_settings({"WEMEET_AUTH_DATABASE_URL": os.environ["WEMEET_AUTH_DATABASE_URL"]}))
    with TestClient(app) as c:
        c.made = made
        yield c


def signup(c, nickname="앨리스", username=None):
    username = username or uname()
    c.made.append(username)
    r = c.post("/api/auth/signup", json={"username": username, "password": PW, "nickname": nickname})
    return r, username


@NEEDS_DB
def test_signup_response_and_me_include_nickname(client):
    r, u = signup(client, "  앨리스  ")
    assert r.status_code == 201
    user = r.json()["data"]["user"]
    assert set(user) == {"user_id", "username", "nickname"}
    assert user["username"] == u and user["nickname"] == "앨리스"  # trimmed
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["data"]["user"] == user


@NEEDS_DB
def test_login_response_is_unchanged_but_me_has_nickname(client):
    _, u = signup(client, "Nick.1")
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"username": u, "password": PW})
    assert r.status_code == 200
    assert set(r.json()["data"]["user"]) == {"user_id", "username"}  # exactly as before
    assert r.json()["status"] == "OK" and set(r.json()) == {"status", "data"}
    assert client.get("/api/auth/me").json()["data"]["user"]["nickname"] == "Nick.1"


@NEEDS_DB
def test_login_still_uses_username_not_nickname(client):
    _, u = signup(client, "별명이름")
    client.post("/api/auth/logout")
    bad = client.post("/api/auth/login", json={"username": "별명이름", "password": PW})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert client.post("/api/auth/login", json={"username": u, "password": PW}).status_code == 200


@NEEDS_DB
def test_duplicate_nicknames_are_allowed(client):
    r1, _ = signup(client, "같은이름")
    client.post("/api/auth/logout")
    r2, _ = signup(client, "같은이름")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["data"]["user"]["user_id"] != r2.json()["data"]["user"]["user_id"]


@NEEDS_DB
def test_duplicate_username_is_still_409(client):
    _, u = signup(client)
    r, _ = signup(client, "다른이름", username=u)
    assert r.status_code == 409 and r.json()["error"]["code"] == "USERNAME_TAKEN"


@NEEDS_DB
def test_db_constraint_rejects_bad_nicknames_and_null(made):
    from wemeet_backend import db
    from wemeet_backend.auth.schema import users

    db.reset_engine()
    engine = db.get_engine()
    base = {"password_hash": "x"}
    try:
        for nick in ("a", "12345678901", " ab", "ab ", None):
            name = uname()
            made.append(name)
            with pytest.raises(sa.exc.IntegrityError):
                with engine.begin() as c:
                    c.execute(sa.insert(users).values(user_id=uuid.uuid4(), username=name, nickname=nick, **base))
        name = uname()
        made.append(name)
        with engine.begin() as c:  # boundary values are accepted
            c.execute(sa.insert(users).values(user_id=uuid.uuid4(), username=name, nickname="ab", **base))
    finally:
        db.reset_engine()
