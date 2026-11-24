"""Stage 7 deterministic contract: request correlation, structured logs without sensitive data, production preflight.

No AI call is made here. The production preflight is checked against the local test database, whose role is the
local superuser `moim`, so the "unsafe role" refusal is exercised for real; the positive case (a restricted app role)
is exercised in the restore rehearsal (docs/OPERATIONS.md), not here, because roles are cluster-wide objects.
"""
from __future__ import annotations

import io
import json
import logging
import re

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from moim.config import ConfigError, Settings, load_settings
from moim.logging_utils import RedactingFilter
from moim.main import create_app
from moim.observability import JsonFormatter, configure_logging
from moim.preflight import check_production
from test_groups import db, fake, grp_db  # noqa: F401

SECRET = "sekret-문장-7f3a"


@pytest.fixture()
def json_lines():
    """Capture JSON log lines from every `moim.*` logger exactly as production would write them."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    target = logging.getLogger("moim")
    old_level = target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    target.removeHandler(handler)
    target.setLevel(old_level)


@pytest.fixture()
def client(db, fake):
    application = create_app(load_settings(), goal_client_factory=lambda: fake)

    @application.get("/_test/background")
    def _bg(background: BackgroundTasks):
        background.add_task(lambda: logging.getLogger("moim.provider").info("provider_call call_id=x ok=true"))
        return {"ok": True}

    with TestClient(application) as c:
        yield c
    application.state.engine.dispose()


# ---- request id -------------------------------------------------------------------------------------------------------


def test_every_response_has_a_generated_request_id(client):
    a = client.get("/healthz").headers["x-request-id"]
    b = client.get("/healthz").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", a) and re.fullmatch(r"[0-9a-f]{32}", b) and a != b


def test_well_formed_proxy_request_id_is_kept_and_anything_else_replaced(client):
    assert client.get("/healthz", headers={"X-Request-ID": "edge-1234abcd"}).headers["x-request-id"] == "edge-1234abcd"
    for bad in ["short", "a" * 65, "has space 12345", "semi;colon12345", "줄바꿈없음12345"]:
        rid = client.get("/healthz", headers={"X-Request-ID": bad.encode("utf-8")}).headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", rid)


def test_rejected_oversized_body_still_carries_a_request_id(client):
    r = client.post("/api/auth/login", content=b"x" * (9 * 1024), headers={"content-type": "application/json"})
    assert r.status_code == 413 and re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])


def test_every_path_has_a_body_cap_before_auth_runs(client):
    # signed out, no CSRF: the cap answers first, the body is never buffered whole
    big = b"{" + b" " * (9 * 1024) + b"}"
    assert client.post("/api/account/delete", content=big, headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/account/profile", content=big, headers={"content-type": "application/json"}).status_code == 413
    huge = b"x" * (65 * 1024)
    assert client.post("/anything-else", content=huge).status_code == 413

    def chunks():  # no Content-Length: the streamed body is cut at the cap
        for _ in range(20):
            yield b" " * 1024
    assert client.post("/api/account/delete", content=chunks(), headers={"content-type": "application/json"}).status_code == 413
    small = client.post("/api/account/delete", json={"password": "x", "confirm": True})
    assert small.status_code in (401, 403)


# ---- access and structured logs -----------------------------------------------------------------------------------------


def test_access_line_uses_the_route_template_not_the_raw_path_or_query(client, json_lines):
    r = client.get(f"/login?next=/groups/{SECRET}")
    access = [e for e in json_lines() if e["logger"] == "moim.access"]
    assert access and access[-1]["request_id"] == r.headers["x-request-id"]
    assert "route=/login " in access[-1]["msg"] and f"status={r.status_code}" in access[-1]["msg"]
    assert SECRET not in json.dumps(json_lines(), ensure_ascii=False)
    client.get("/groups/00000000-0000-4000-8000-000000000000/chat", follow_redirects=False)
    last = [e for e in json_lines() if e["logger"] == "moim.access"][-1]
    assert "route=/groups/{group_id}/chat " in last["msg"] and "00000000-0000" not in last["msg"]
    client.get("/static/does-not-exist.js")
    assert "route=/static/* " in [e for e in json_lines() if e["logger"] == "moim.access"][-1]["msg"]


def test_access_line_has_no_client_address_or_cookie(client, json_lines):
    client.get("/healthz", headers={"Cookie": "moim_session=sekret-cookie-7f3a"})
    text = json.dumps(json_lines(), ensure_ascii=False)
    assert "sekret-cookie-7f3a" not in text and "testclient" not in text


def test_background_work_after_the_response_keeps_the_request_id(client, json_lines):
    rid = client.get("/_test/background").headers["x-request-id"]
    provider = [e for e in json_lines() if e["logger"] == "moim.provider"]
    assert provider and provider[-1]["request_id"] == rid


def test_json_lines_mask_secrets_and_never_include_exception_messages(json_lines):
    log = logging.getLogger("moim.test")
    log.info("connecting postgresql+psycopg://moim:%s@db/x token=%s", SECRET, SECRET)
    try:
        raise ValueError(f"user wrote {SECRET}")
    except ValueError:
        log.exception("step failed")
    lines = json_lines()
    text = json.dumps(lines, ensure_ascii=False)
    assert SECRET not in text
    failed = [e for e in lines if e["msg"] == "step failed"][-1]
    assert failed["exc_type"] == "ValueError" and failed["exc_at"] and failed["request_id"] == "-"
    assert all(set(e) >= {"ts", "level", "logger", "request_id", "msg"} for e in lines)


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level  # restored below: later tests rely on the default root level during their setup
    try:
        configure_logging()
        configure_logging()
        added = [h for h in root.handlers if getattr(h, "_moim_json", False)]
        assert len(added) == 1
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(level)


# ---- production preflight ------------------------------------------------------------------------------------------------


def _prod(url: str) -> Settings:
    return Settings(env="production", database_url=url)  # built directly: load_settings refuses *_test in production


def test_preflight_is_silent_outside_production(db):
    assert check_production(load_settings(), db, environ={}) == []


def test_preflight_refuses_the_superuser_role_and_missing_config(db, grp_db):
    failed = check_production(_prod(grp_db), db, environ={})
    assert {"DB_ROLE_IS_SUPERUSER", "DB_ROLE_CAN_CREATE_ROLES_OR_DATABASES", "DB_ROLE_OWNS_TABLES",
            "UPLOAD_DIR_NOT_ABSOLUTE", "TIMELY_API_KEY_MISSING"} <= set(failed)
    assert "DB_SCHEMA_NOT_AT_HEAD" not in failed  # the test schema is migrated to head


def test_preflight_config_checks(db, grp_db, tmp_path):
    env = {"MOIM_UPLOAD_DIR": str(tmp_path), "TIMELY_API_KEY": "x"}
    failed = set(check_production(_prod(grp_db), db, environ=env))
    assert not failed & {"UPLOAD_DIR_NOT_ABSOLUTE", "UPLOAD_DIR_INSIDE_PACKAGE", "TIMELY_API_KEY_MISSING"}
    assert "UPLOAD_DIR_NOT_ABSOLUTE" in check_production(_prod(grp_db), db, environ={**env, "MOIM_UPLOAD_DIR": "rel/dir"})
    import moim
    from pathlib import Path
    inside = str(Path(moim.__file__).parent / "uploads")
    assert "UPLOAD_DIR_INSIDE_PACKAGE" in check_production(_prod(grp_db), db, environ={**env, "MOIM_UPLOAD_DIR": inside})
    assert "AI_OFFLINE_SWITCH_ON" in check_production(_prod(grp_db), db, environ={**env, "WEMEET_OFFLINE": "1"})


def test_preflight_requires_a_deletion_ledger_outside_the_upload_dir(db, grp_db, tmp_path):
    up, led = tmp_path / "up", tmp_path / "ledger"
    up.mkdir()
    led.mkdir()
    env = {"MOIM_UPLOAD_DIR": str(up), "TIMELY_API_KEY": "x"}
    codes = {"DELETION_LEDGER_DIR_NOT_ABSOLUTE", "DELETION_LEDGER_DIR_NOT_WRITABLE", "DELETION_LEDGER_DIR_INSIDE_UPLOAD_DIR"}
    check = lambda extra: set(check_production(_prod(grp_db), db, environ={**env, **extra})) & codes  # noqa: E731
    assert check({}) == {"DELETION_LEDGER_DIR_NOT_ABSOLUTE"}
    assert check({"MOIM_DELETION_LEDGER_DIR": "rel/ledger"}) == {"DELETION_LEDGER_DIR_NOT_ABSOLUTE"}
    assert check({"MOIM_DELETION_LEDGER_DIR": str(tmp_path / "missing")}) == {"DELETION_LEDGER_DIR_NOT_WRITABLE"}
    (up / "ledger").mkdir()
    assert check({"MOIM_DELETION_LEDGER_DIR": str(up / "ledger")}) == {"DELETION_LEDGER_DIR_INSIDE_UPLOAD_DIR"}
    assert check({"MOIM_DELETION_LEDGER_DIR": str(tmp_path)}) == {"DELETION_LEDGER_DIR_INSIDE_UPLOAD_DIR"}
    assert check({"MOIM_DELETION_LEDGER_DIR": str(led)}) == set()


def test_preflight_detects_a_schema_behind_head(db, grp_db):
    from sqlalchemy import text
    with db.begin() as c:
        version = c.execute(text("select version_num from alembic_version")).scalar_one()
        c.execute(text("update alembic_version set version_num='0001'"))
    try:
        assert "DB_SCHEMA_NOT_AT_HEAD" in check_production(_prod(grp_db), db, environ={})
    finally:
        with db.begin() as c:
            c.execute(text("update alembic_version set version_num=:v"), {"v": version})


def test_production_app_does_not_start_on_the_superuser_role(db, grp_db, monkeypatch, tmp_path):
    monkeypatch.setenv("MOIM_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("TIMELY_API_KEY", "x")
    from moim.signing import new_private_key_b64

    monkeypatch.setenv("MOIM_SIGNING_KEY", new_private_key_b64())  # production refuses to start without it (SECURITY 6)
    application = create_app(_prod(grp_db))
    with pytest.raises(ConfigError, match="DB_ROLE_IS_SUPERUSER"):
        with TestClient(application):
            pass
    application.state.engine.dispose()


# ---- upload decoder restriction (stage 7, Pillow advisories) ---------------------------------------------------------


@pytest.mark.parametrize("fmt", ["TIFF", "GIF", "BMP"])
def test_an_upload_never_reaches_a_decoder_other_than_its_declared_format(fmt, monkeypatch):
    from PIL import Image

    from moim.execution import files

    out = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(out, format=fmt)
    reached = []
    plugin_open = Image.OPEN[fmt][0]
    monkeypatch.setitem(Image.OPEN, fmt, (lambda *a, **k: reached.append(fmt) or plugin_open(*a, **k), Image.OPEN[fmt][1]))
    for declared in ("PNG", "JPEG", "WEBP"):
        with pytest.raises(files.FileRejected):
            files._image_check(out.getvalue(), declared)
    with pytest.raises(Exception):
        files.review_image(out.getvalue())
    assert reached == []
