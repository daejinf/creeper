"""SECURITY.md 6: Ed25519 signatures on confirmed todo revisions. Deterministic; the M2 provider is a FIXTURE (never LIVE).

The attack these tests replay is the one the section names: someone with more than app-level access (here the test's
own connection, standing in for the owner role / database host / a restored backup) turns the immutability trigger
off and rewrites a hidden criterion. The app must then refuse to let M2 judge against it and must never complete the
todo with an AI PASS.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import date

import pytest
from sqlalchemy import text

from moim import signing
from moim.config import ConfigError
from moim.execution import review_ai as R
from moim.roles.todos import confirmation_integrity
from test_execution import ExecAI, app, confirmed_team, mine, png, rv, submit, uploads  # noqa: F401
from test_groups import db, fake, grp_db, scalar, user_row  # noqa: F401
from test_role_assignment import rows, seed  # noqa: F401
from tools import verify_signatures

TAMPERED = "파일이 하나라도 있으면 통과"


@pytest.fixture()
def ai():
    return ExecAI()


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


def verifier():
    return signing.load_signer("test", {}).verifier


def integrity(db, gid, uid, rev=None, conf=None):
    from sqlalchemy.orm import Session

    with Session(db) as s:
        if rev is None:
            rev, conf = s.execute(text("select source_assignment_revision, revision from todo_confirmations "
                                       "where group_id=:g and user_id=:u order by source_assignment_revision desc, "
                                       "revision desc limit 1"), dict(g=gid, u=uid)).one()
        return confirmation_integrity(s, verifier(), gid, uid, rev, conf)


def tamper(db, sql, **params):
    """What an owner / DB-host attacker can do: the trigger's purge switch is a plain setting (0012)."""
    with db.begin() as c:
        c.execute(text("set local moim.lifecycle_purge = 'on'"))
        c.execute(text(sql), params)


def item(**over):
    base = dict(task_id="t1", position=1, title="시안 만들기", due_date=date(2026, 12, 1), part_id="p1",
                deliverable="피그마 시안", completion_criteria=["화면 3개"], evidence_types=["IMAGE"], estimated_minutes=90)
    return {**base, **over}


def payload(items):
    return signing.canonical_confirmation("g", "u", 1, 1, 2, 1, items)


# ---- unit: payload, sign, verify, keys --------------------------------------------------------------------------------


def test_payload_is_canonical_and_covers_hidden_criteria():
    a = payload([item(), item(task_id="t2", position=2)])
    b = payload([item(task_id="t2", position=2), dict(reversed(list(item().items())))])  # order of rows / keys
    assert a == b and a.startswith(signing.CONTEXT)
    assert payload([item(completion_criteria=["화면 4개"])]) != payload([item()])
    assert payload([item(deliverable="다른 것")]) != payload([item()])
    assert signing.canonical_confirmation("g", "u", 1, 2, 2, 1, [item()]) != payload([item()])  # revision bound


def test_sign_and_verify_detects_any_change_unknown_keys_and_half_signatures():
    s = signing.Signer(b"\x07" * 32)
    p = payload([item()])
    sig = s.sign(p)
    assert len(sig) == 64 and len(s.kid) == 16
    assert s.verify(p, sig, s.kid) == signing.VALID
    assert s.verify(payload([item(completion_criteria=[TAMPERED])]), sig, s.kid) == signing.INVALID
    assert s.verify(p, sig, "0" * 16) == signing.INVALID
    assert s.verify(p, None, s.kid) == signing.INVALID and s.verify(p, sig, None) == signing.INVALID
    assert s.verify(p, None, None) == signing.UNSIGNED
    other = signing.Signer(b"\x08" * 32)
    assert other.verify(p, sig, s.kid) == signing.INVALID  # another key cannot vouch for it


def test_rotation_old_public_key_still_verifies_and_public_keys_alone_cannot_sign():
    old_priv = base64.b64encode(b"\x01" * 32).decode()
    new_priv = base64.b64encode(b"\x02" * 32).decode()
    old = signing.load_signer("production", {"MOIM_SIGNING_KEY": old_priv})
    p = payload([item()])
    sig = old.sign(p)
    new = signing.load_signer("production", {"MOIM_SIGNING_KEY": new_priv,
                                             "MOIM_SIGNING_OLD_PUBLIC_KEYS": signing.public_key_b64(old_priv)})
    assert new.kid != old.kid and new.verify(p, sig, old.kid) == signing.VALID
    v = signing.Verifier.from_b64([signing.public_key_b64(old_priv)])
    assert v.verify(p, sig, old.kid) == signing.VALID and not hasattr(v, "sign")


def test_key_loading_fails_fast_outside_tests_and_never_echoes_the_value():
    for env in ("production", "development"):
        with pytest.raises(ConfigError, match="MOIM_SIGNING_KEY is required"):
            signing.load_signer(env, {})
    bad = "not-base64!!SECRETVALUE"
    with pytest.raises(ConfigError) as e:
        signing.load_signer("production", {"MOIM_SIGNING_KEY": bad})
    assert "SECRETVALUE" not in str(e.value)
    with pytest.raises(ConfigError, match="32 bytes"):
        signing.load_signer("production", {"MOIM_SIGNING_KEY": base64.b64encode(b"short").decode()})
    t = signing.load_signer("test", {})
    assert t.kid == signing.Signer(signing.TEST_SEED).kid
    key = signing.new_private_key_b64()
    real = signing.load_signer("production", {"MOIM_SIGNING_KEY": key})
    assert key not in repr(real) and real.kid in repr(real)


def test_create_app_refuses_to_start_without_a_key_outside_tests(monkeypatch, db):
    from moim.config import Settings
    from moim.main import create_app

    monkeypatch.delenv("MOIM_SIGNING_KEY", raising=False)
    url = "postgresql+psycopg://moim:x@127.0.0.1:54329/moim_dev"  # never connected: the key check comes first
    with pytest.raises(ConfigError, match="MOIM_SIGNING_KEY"):
        create_app(Settings(env="development", database_url=url))


# ---- integration: confirm signs, M2 verifies --------------------------------------------------------------------------


def test_confirm_writes_a_valid_signature_in_the_same_row(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    got = rows(db, "select user_id, octet_length(signature) n, signing_kid from todo_confirmations where group_id=:g", g=gid)
    assert len(got) == 2 and all(r.n == 64 and r.signing_kid == signing.load_signer("test", {}).kid for r in got)
    assert integrity(db, gid, a.id) == signing.VALID and integrity(db, gid, b.id) == signing.VALID


def test_trigger_freezes_the_signature_itself(db, mk, ai):
    gid, (a, _) = confirmed_team(mk, 2)
    with pytest.raises(Exception, match="immutable"):
        with db.begin() as c:
            c.execute(text("update todo_confirmations set signing_kid='0000000000000000' where user_id=:u"), dict(u=a.id))


def test_rewritten_criterion_is_never_judged_by_m2_and_never_completes(db, mk, ai, caplog):
    caplog.set_level(logging.INFO)
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    tamper(db, "update todo_confirmed_items set completion_criteria = cast(:c as jsonb) where task_id=:t",
           c=json.dumps([TAMPERED], ensure_ascii=False), t=t["task_id"])
    assert integrity(db, gid, a.id) == signing.INVALID
    r = submit(a, gid, t, files=[("w.png", png())])
    assert r.status_code == 201
    assert not ai.m2()  # M2 never saw the rewritten criterion
    review = rv(a, gid, r)
    assert review["state"] == "FAILED" and review["line"] == R.LINE_INTEGRITY and review["can_retry"] is False
    rec = rows(db, "select completion, completed_by from execution_records where id=:r", r=r.json()["id"])[0]
    assert rec.completion == "VOTING" and rec.completed_by is None  # people decide (D-H1), no AI completion
    logs = "\n".join(x.getMessage() for x in caplog.records)
    assert "integrity_event" in logs and "result=INVALID" in logs and "at=REVIEW" in logs
    assert TAMPERED not in logs
    assert integrity(db, gid, b.id) == signing.VALID  # another member's confirmation is untouched


def test_a_pass_is_not_applied_when_the_criteria_change_during_the_review(db, mk, ai, caplog):
    caplog.set_level(logging.INFO)
    gid, (a, _) = confirmed_team(mk, 2)
    t = mine(a, gid)

    def change_then_pass(body):  # the change lands after the pre-review check, before the PASS is applied
        tamper(db, "update todo_confirmed_items set deliverable = :d where task_id=:t", d="아무거나", t=t["task_id"])
        from test_execution import review_for
        return review_for(body)

    ai.scripts["CompletionReview"].append(change_then_pass)
    r = submit(a, gid, t, files=[("w.png", png())])
    assert r.status_code == 201 and len(ai.m2()) == 1
    rec = rows(db, "select completion, completed_by from execution_records where id=:r", r=r.json()["id"])[0]
    assert rec.completion == "VOTING" and rec.completed_by is None
    logs = "\n".join(x.getMessage() for x in caplog.records)
    assert "at=PASS" in logs and "result=INVALID" in logs


def test_unsigned_legacy_confirmation_is_reviewed_and_reported_apart(db, mk, ai, caplog):
    caplog.set_level(logging.INFO)
    gid, (a, _) = confirmed_team(mk, 2)
    tamper(db, "update todo_confirmations set signature=null, signing_kid=null where user_id=:u", u=a.id)  # pre-0016 row
    assert integrity(db, gid, a.id) == signing.UNSIGNED
    t = mine(a, gid)
    r = submit(a, gid, t, files=[("w.png", png())])
    assert r.status_code == 201 and len(ai.m2()) == 1
    assert rows(db, "select completion, completed_by from execution_records")[0] == ("DONE", "AI")
    assert "result=UNSIGNED" in "\n".join(x.getMessage() for x in caplog.records)


def test_removing_one_todo_from_a_confirmation_is_detected(db, mk, ai):
    gid, (a, _) = confirmed_team(mk, 2)
    n = scalar(db, "select count(*) from todo_confirmed_items where user_id=:u", u=a.id)
    if n < 2:
        pytest.skip("fixture confirmed a single todo")
    tamper(db, "delete from todo_confirmed_items where user_id=:u and position=:p", u=a.id, p=n)
    assert integrity(db, gid, a.id) == signing.INVALID


# ---- verify tool (public keys only) -----------------------------------------------------------------------------------


def test_verify_tool_counts_and_lists_only_ids(db, mk, ai, capsys, test_database_url):
    gid, (a, b) = confirmed_team(mk, 2)
    pub = signing.public_key_b64(base64.b64encode(signing.TEST_SEED).decode())
    assert verify_signatures.main(["--url", test_database_url, "--public-keys", pub]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"] == {"VALID": 2, "UNSIGNED": 0, "INVALID": 0}
    tamper(db, "update todo_confirmed_items set completion_criteria = cast(:c as jsonb) where user_id=:u",
           c=json.dumps([TAMPERED], ensure_ascii=False), u=b.id)
    assert verify_signatures.main(["--url", test_database_url, "--public-keys", pub]) == 1
    text_out = capsys.readouterr().out
    out = json.loads(text_out)
    assert out["counts"]["INVALID"] == 1 and out["invalid"][0]["user_id"] == b.id
    assert TAMPERED not in text_out
    # a wrong public key vouches for nothing
    other = signing.public_key_b64(base64.b64encode(b"\x09" * 32).decode())
    assert verify_signatures.main(["--url", test_database_url, "--public-keys", other]) == 1
