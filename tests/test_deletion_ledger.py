"""Stage 7 deterministic contract (D-G1): the deletion ledger and its replay after a backup restore.

The ledger lives outside the database, so a restore never rolls it back. A "restored" database is one in which the
ledger's deletions have not happened: here it is built by writing ledger lines for a team that has not left or been
deleted yet, which is exactly the state a restore leaves behind. The real pg_dump → restore → replay path is exercised
by `tools/rehearse_restore.py` (docs/OPERATIONS.md 5절). No AI call is made.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("cryptography")  # tools/ops_db imports it at module level

from sqlalchemy import event  # noqa: E402

from moim.account.ledger import DeletionLedger, LedgerUnavailable  # noqa: E402
from moim.config import load_settings  # noqa: E402
from moim.main import create_app  # noqa: E402
from test_execution import ai, confirmed_team, ex, uploads  # noqa: E402,F401
from test_groups import db, fake, grp_db, scalar, user_row  # noqa: E402,F401
from test_lifecycle import PW, db_mentions, delete_account, leave, login, set_pw, with_record  # noqa: E402
from test_role_assignment import rows, seed  # noqa: E402,F401
from tools import ops_db  # noqa: E402

UID = "0f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"
GID = "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"


@pytest.fixture()
def ledger_dir(tmp_path):
    d = tmp_path / "ledger"
    d.mkdir()
    return d


@pytest.fixture()
def app(db, fake, ai, seed, uploads, ledger_dir):
    application = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai,
                             rng_factory=lambda: random.Random(seed["value"]), upload_dir=uploads,
                             deletion_ledger_dir=ledger_dir)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


def lines(d):
    return [json.loads(x) for p in sorted(d.iterdir()) for x in p.read_text(encoding="utf-8").splitlines()]


# ---- the ledger file ---------------------------------------------------------------------------------------------------


def test_a_line_holds_only_time_kind_and_internal_ids(ledger_dir):
    led = DeletionLedger(ledger_dir)
    assert led.record("LEAVE", UID, GID) and led.record("ACCOUNT_DELETED", UID)
    got = lines(ledger_dir)
    assert [set(e) for e in got] == [{"id", "ts", "kind", "user_id", "group_id"}] * 2
    assert [(e["kind"], e["user_id"], e["group_id"]) for e in got] == [("LEAVE", UID, GID), ("ACCOUNT_DELETED", UID, None)]
    assert [p.name.startswith("deletions-") and p.suffix == ".jsonl" for p in ledger_dir.iterdir()] == [True]


def test_bad_entries_are_refused_and_a_disabled_ledger_writes_nothing(ledger_dir, tmp_path):
    led = DeletionLedger(ledger_dir)
    for bad in [("OTHER", UID, None), ("LEAVE", "park", GID), ("LEAVE", UID, "../../etc"), ("LEAVE", UID, None), ("RECORD_UNDONE", UID, GID)]:
        with pytest.raises(ValueError):
            led.record(*bad)
    assert list(ledger_dir.iterdir()) == []
    assert DeletionLedger(None).record("ACCOUNT_DELETED", UID) is None


def test_a_write_failure_raises_and_is_logged_with_a_fixed_code_and_no_ids(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(LedgerUnavailable):
        DeletionLedger(tmp_path / "missing").record("ACCOUNT_DELETED", UID)
    assert "kind=LEDGER_WRITE_FAILED" in caplog.text and UID not in caplog.text


def test_a_cancelled_entry_is_never_replayed(ledger_dir):
    led = DeletionLedger(ledger_dir)
    a = led.record("ACCOUNT_DELETED", UID)
    led.record("LEAVE", UID, GID)
    led.cancel(a)
    assert [e["kind"] for e in led.entries()] == ["LEAVE"]


def test_day_files_older_than_seven_days_are_pruned(ledger_dir):
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    for days in (9, 8, 7, 6, 0):
        DeletionLedger(ledger_dir, clock=lambda d=days: now - timedelta(days=d)).record("ACCOUNT_DELETED", UID)
    # every write prunes; the 8- and 9-day-old days are gone, the 7-day-old day is still inside the window
    names = sorted(p.name for p in ledger_dir.iterdir())
    assert names == ["deletions-20261113.jsonl", "deletions-20261114.jsonl", "deletions-20261120.jsonl"]
    assert DeletionLedger(ledger_dir, clock=lambda: now + timedelta(days=2)).prune() == 2


def test_entries_are_in_time_order_and_a_torn_line_is_skipped(ledger_dir):
    t = datetime(2026, 11, 20, tzinfo=timezone.utc)
    def line(**k):
        return json.dumps({"id": "a" * 32, "ts": t.isoformat(), "user_id": UID, "group_id": None, **k}) + "\n"
    (ledger_dir / "deletions-20261120.jsonl").write_text(
        line(kind="ACCOUNT_DELETED", ts=(t + timedelta(seconds=5)).isoformat())
        + line(kind="LEAVE", group_id=GID)
        + line(kind="LEAVE")  # a leave without a group
        + line(kind="LEAVE", group_id=GID, id="not-an-entry-id")
        + '{"ts": "2026-11-20T00:00:09+00:00", "kind": "ACC', encoding="utf-8")
    assert [e["kind"] for e in DeletionLedger(ledger_dir).entries()] == ["LEAVE", "ACCOUNT_DELETED"]


# ---- the app writes the ledger inside each deletion (fail closed) ----------------------------------------------------


def test_leaving_and_deleting_write_the_ledger_with_internal_ids_only(app, mk, db, ledger_dir):
    gid, (lead, m0, m1) = confirmed_team(mk, 3, leader="ledg")
    set_pw(db, m1)
    assert leave(m0, gid, confirm=False).status_code == 409
    assert lines(ledger_dir) == []  # nothing happened, nothing recorded
    assert leave(m0, gid).status_code == 200
    assert delete_account(m1, pw="wrong-pw").status_code == 401
    assert delete_account(m1).status_code == 200
    got = [(e["kind"], e["user_id"], e["group_id"]) for e in lines(ledger_dir)]
    assert got == [("LEAVE", m0.id, gid), ("ACCOUNT_DELETED", m1.id, None)]
    text = "".join(p.read_text(encoding="utf-8") for p in ledger_dir.iterdir())
    assert m1.name not in text and m0.name not in text


def test_no_deletion_commits_without_its_ledger_line(app, mk, db, ledger_dir, caplog):
    gid, (lead, m0, m1) = confirmed_team(mk, 3, leader="ledx")
    set_pw(db, m1)
    ledger_dir.rename(ledger_dir.with_name("gone"))  # the ledger storage disappears
    caplog.set_level(logging.INFO)
    r = leave(m0, gid)
    assert r.status_code == 503 and r.json()["error"]["code"] == "DELETION_UNAVAILABLE"
    r = delete_account(m1)
    assert r.status_code == 503 and r.json()["error"]["code"] == "DELETION_UNAVAILABLE"
    # nothing happened: both are still members, the account and its session still work
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=gid) == 3
    assert scalar(db, "select count(*) from users where id=:i", i=m1.id) == 1
    assert m1.c.get("/api/account").status_code == 200
    assert caplog.text.count("kind=LEDGER_WRITE_FAILED") == 2 and m1.id not in caplog.text


def test_a_failed_commit_cancels_its_ledger_line(app, mk, db, ledger_dir, monkeypatch):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="ledc")
    engine = app.state.engine

    def fail_commit(conn):  # the line is written, then the database refuses the commit
        if lines(ledger_dir):
            raise RuntimeError("commit failed")
    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError):
            leave(m0, gid)
    finally:
        event.remove(engine, "commit", fail_commit)
    got = lines(ledger_dir)
    assert len(got) == 2 and got[1] == {"ts": got[1]["ts"], "cancel": got[0]["id"]}  # written, then cancelled
    assert DeletionLedger(ledger_dir).entries() == []  # the replay never sees it
    assert scalar(db, "select count(*) from group_members where group_id=:g and user_id=:u", g=gid, u=m0.id) == 1


def test_a_failed_deletion_writes_no_ledger_line(app, mk, db, ledger_dir, monkeypatch):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="ledf")
    set_pw(db, m0)
    from moim.account import lifecycle

    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(lifecycle.LifecycleService, "_tombstone", staticmethod(boom))
    with pytest.raises(RuntimeError):
        delete_account(m0)
    assert lines(ledger_dir) == [] and scalar(db, "select count(*) from users where id=:i", i=m0.id) == 1


# ---- replay after a restore ------------------------------------------------------------------------------------------------


def test_replay_runs_the_lost_deletions_again_and_is_safe_to_repeat(app, mk, db, grp_db, uploads, ledger_dir):
    # The "restored" state: a team where m0 has not left and m1 still exists, with records and files.
    gid, (lead, m0, m1) = confirmed_team(mk, 3, leader="replay")
    set_pw(db, m1)
    with_record(m0, gid)
    with_record(m1, gid, files=2)
    m1_files = sorted(p.name for p in (uploads / gid).iterdir())
    assert m1_files
    # ... and the ledger that survived outside the database
    led = DeletionLedger(ledger_dir)
    led.record("LEAVE", m0.id, gid)
    led.record("ACCOUNT_DELETED", m1.id)
    led.record("ACCOUNT_DELETED", "9f9f9f9f-0000-4000-8000-000000000000")  # deleted before the backup: already gone

    dry = ops_db.replay_deletions(grp_db, str(uploads), str(ledger_dir), dry_run=True)
    assert dry == {"entries": 3, "LEAVE": 1, "ACCOUNT_DELETED": 2, "RECORD_UNDONE": 0, "PORTFOLIO_DELETED": 0, "dry_run": True}
    assert scalar(db, "select count(*) from group_members where group_id=:g", g=gid) == 3  # dry run changed nothing

    first = ops_db.replay_deletions(grp_db, str(uploads), str(ledger_dir), dry_run=False)
    assert (first["applied"], first["already"]) == (2, 1)
    # same effect as the user's own requests (D6/D7)
    assert scalar(db, "select count(*) from users where id=:i", i=m1.id) == 0 and db_mentions(db, m1.id) == []
    assert rows(db, "select user_id from group_members where group_id=:g", g=gid) == [(lead.id,)]
    assert scalar(db, "select count(*) from role_interview_turns where group_id=:g and user_id=:u", g=gid, u=m0.id) == 0
    assert login(app, m1.name, PW).status_code == 401
    assert len(list((uploads / gid).iterdir())) < len(m1_files)  # m1's files are gone, m0's stay as "나간 팀원"
    assert [f["who"] for f in ex(lead, gid)["feed"]].count("left") == 1
    # replay records nothing new and a second run changes nothing
    assert len(lines(ledger_dir)) == 3
    before = {t: scalar(db, f"select count(*) from {t}") for t in ("users", "group_members", "execution_records")}
    second = ops_db.replay_deletions(grp_db, str(uploads), str(ledger_dir), dry_run=False)
    assert (second["applied"], second["already"]) == (0, 3)
    assert {t: scalar(db, f"select count(*) from {t}") for t in before} == before


def test_prune_removes_only_old_backup_files(tmp_path, ledger_dir):
    import os

    bdir = tmp_path / "backups"
    bdir.mkdir()
    old, new, other = bdir / "moim-old.bak", bdir / "moim-new.bak", bdir / "notes.txt"
    old.write_bytes(ops_db.MAGIC + b"x")
    new.write_bytes(ops_db.MAGIC + b"y")
    other.write_bytes(b"not a backup")
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))
    assert ops_db.prune(str(bdir), str(ledger_dir), 7, delete=False)["old_backups"] == 1 and old.exists()
    ops_db.prune(str(bdir), str(ledger_dir), 7, delete=True)
    assert not old.exists() and new.exists() and other.exists()
    with pytest.raises(ops_db.OpsError):
        ops_db.prune(str(bdir), None, 0, delete=False)


# ---- D5 `되돌리기` is replayed too (it deletes a record and its files for good) ---------------------------------------


def undo(u, gid, rid):
    return u.c.post(f"/api/groups/{gid}/records/{rid}/undo", json={"confirm_delete": True},
                    headers={"X-CSRF-Token": u.c.cookies.get("moim_csrf")})


def test_undo_writes_the_ledger_and_fails_closed(app, mk, db, uploads, ledger_dir):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="ledu")
    rid = with_record(m0, gid, files=1)
    assert undo(m0, gid, rid).status_code == 200
    assert [(e["kind"], e["user_id"], e["group_id"], e["record_id"]) for e in lines(ledger_dir)] == [
        ("RECORD_UNDONE", m0.id, gid, rid)]
    rid2 = with_record(lead, gid, files=1)
    ledger_dir.rename(ledger_dir.with_name("gone2"))
    r = undo(lead, gid, rid2)
    assert r.status_code == 503 and r.json()["error"]["code"] == "DELETION_UNAVAILABLE"
    assert scalar(db, "select count(*) from execution_records where id=:r", r=rid2) == 1
    assert len(list((uploads / gid).iterdir())) == 1  # the file is still there


def test_replay_undoes_a_record_the_restore_brought_back(app, mk, db, grp_db, uploads, ledger_dir):
    gid, (lead, m0) = confirmed_team(mk, 2, leader="ledr")
    rid = with_record(m0, gid, files=2)
    keep = with_record(lead, gid, files=1)
    assert len(list((uploads / gid).iterdir())) == 3
    DeletionLedger(ledger_dir).record("RECORD_UNDONE", m0.id, gid, rid)
    done = ops_db.replay_deletions(grp_db, str(uploads), str(ledger_dir), dry_run=False)
    assert (done["applied"], done["already"], done["RECORD_UNDONE"]) == (1, 0, 1)
    assert scalar(db, "select count(*) from execution_records where id=:r", r=rid) == 0
    assert scalar(db, "select count(*) from execution_records where id=:r", r=keep) == 1
    assert len(list((uploads / gid).iterdir())) == 1
    again = ops_db.replay_deletions(grp_db, str(uploads), str(ledger_dir), dry_run=False)
    assert (again["applied"], again["already"]) == (0, 1)
