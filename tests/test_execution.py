"""Stage 5 deterministic contract: group execution screen, completion records, attachments and the per-todo M2 review.

The provider is a FIXTURE client (never reported as LIVE). It records the M2 messages so the tests can check that the
input is exactly one todo's frozen snapshot plus that submission's own title/memo/files: no name, user id, group or
task id, MemberState, interview text, other todo or other member, and no user file name. FastAPI background tasks run
before the test client returns, so a review is finished when `submit` returns.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import random
import re
import uuid
import zipfile
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from alembic import command
from alembic.config import Config
from docx import Document
from PIL import Image

from moim.config import load_settings
from moim.execution import files as F
from moim.main import create_app
from test_groups import db, fake, grp_db, scalar, user_row  # noqa: F401
from test_role_assignment import ROOT, SECRET_CONSTRAINT, assigned_team, forbidden_in, reconnect, rows, seed  # noqa: F401
from test_todos import TodoAI, confirm, finish_interview, td, tstart, unlock
from wemeet_ai.m0.client import AIError
from wemeet_ai.m2.contracts import CompletionReview
from wemeet_ai.shared.contracts import Usage

SRC = ROOT / "src" / "moim"
INTERVIEW_ANSWER = "피그마로 시안 두 번 만들어 봤어요"  # what finish_interview typed: private MemberState input


def review_for(body, status="MET", message="좋아요"):
    criteria = body["task"]["completion_criteria"]
    ids = [e["evidence_id"] for e in body["evidence"]]
    statuses = status if isinstance(status, list) else [status] * len(criteria)
    results = [{"criterion_index": i, "criterion": c, "status": st, "reason": "근거",
                "evidence_refs": [{"evidence_id": ids[0] if ids else "submission_description", "description": "자료"}] if st == "MET" else []}
               for i, (c, st) in enumerate(zip(criteria, statuses))]
    decision = "REVIEW_NEEDED" if "NOT_MET" in statuses else "NEEDS_INPUT" if "UNKNOWN" in statuses else "PASS"
    return {"decision": decision, "summary": "요약", "criteria_results": results, "missing_items": [], "user_message": message}


class ExecAI(TodoAI):
    """TodoAI plus the M2 review schema. Never LIVE."""

    def __init__(self):
        super().__init__()
        self.scripts["CompletionReview"] = []

    def m2(self):
        return [m for k, m in self.calls if k == "CompletionReview"]

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        if output_schema.__name__ != "CompletionReview":
            return super().complete_json(messages, output_schema, options, stats=stats)
        with self.lock:
            self.calls.append(("CompletionReview", messages))
            item = self.scripts["CompletionReview"].pop(0) if self.scripts["CompletionReview"] else None
        if isinstance(item, Exception):
            raise item
        body = json.loads(messages[-1]["content"][0]["text"])
        data = item(body) if callable(item) else item if item is not None else review_for(body)
        return SimpleNamespace(parsed_output=CompletionReview.model_validate(data), model="fixture-model", latency_ms=11,
                               provider_request_id="fixture-m2", usage=Usage(prompt_tokens=40, completion_tokens=20, total_tokens=60))


@pytest.fixture()
def ai():
    return ExecAI()


@pytest.fixture()
def uploads(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture()
def app(db, fake, ai, seed, uploads):
    application = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai,
                             rng_factory=lambda: random.Random(seed["value"]), upload_dir=uploads)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


# ---- file fixtures --------------------------------------------------------------------------------------------------


def png(w=1200, h=800, color=(30, 90, 200), exif=False):
    im = Image.new("RGB", (w, h), color)
    out = io.BytesIO()
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCamera"  # Make
        im.save(out, format="JPEG", exif=ex)
    else:
        im.save(out, format="PNG")
    return out.getvalue()


def jpeg_with_exif(w=3000, h=1000):
    return png(w, h, exif=True)


def webp():
    out = io.BytesIO()
    Image.new("RGB", (50, 40), (1, 2, 3)).save(out, format="WEBP")
    return out.getvalue()


def pdf(pages=1, author="SecretAuthor"):
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"spec page {i + 1} CODE-PDF")
    doc.set_metadata({"author": author, "title": "SecretTitle"})
    data = doc.tobytes()
    doc.close()
    return data


def docx(text="DOCX 본문 CODE-DOCX", picture=False):
    d = Document()
    d.add_paragraph(text)
    if picture:
        d.add_picture(io.BytesIO(png(40, 40)))
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def zip_doc(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return out.getvalue()


def pptx():
    return zip_doc({"[Content_Types].xml": b"<Types/>", "ppt/presentation.xml": b"<p/>"})


def xlsx():
    return zip_doc({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})


def hwpx():
    return zip_doc({"mimetype": b"application/hwp+zip", "Contents/section0.xml": "<t>한글 본문</t>".encode()})


def hwp():
    return F.OLE_MAGIC + b"\0" * 504 + b"HWP Document File" + b"\0" * 100


def with_member(data: bytes, name: str, content: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            z.writestr(info, src.read(info.filename))
        z.writestr(name, content)
    return out.getvalue()


# ---- helpers ----------------------------------------------------------------------------------------------------------


def confirmed_team(mk, n=2, leader="leader"):
    gid, people = assigned_team(mk, n, leader=leader)
    for u in people:
        finish_interview(u, gid)
        assert tstart(u, gid).status_code == 202
        r = confirm(u, gid)
        assert r.status_code in (200, 201), r.text
    return gid, people


def ex(u, gid):
    r = u.c.get(f"/api/groups/{gid}/execution")
    assert r.status_code == 200, r.text
    return r.json()


def submit(u, gid, task=None, files=(), title=None, memo=None, done=False, rev=None, action=None, token=...):
    data = {"action_id": action or uuid.uuid4().hex}
    if task is not None:
        data["task_id"] = task["task_id"]
        data["confirmation_revision"] = str(rev if rev is not None else task["confirmation_revision"])
        data["marks_done"] = "true" if done else "false"
    if title is not None:
        data["title"] = title
    if memo is not None:
        data["memo"] = memo
    h = {}
    if token is ...:
        token = u.c.cookies.get("moim_csrf")
    if token:
        h["X-CSRF-Token"] = token
    fl = [("files", (name, content, "application/octet-stream")) for name, content in files]
    return u.c.post(f"/api/groups/{gid}/records", data=data, files=fl or None, headers=h)


def rv(u, gid, r):
    """The submitter's review after the background task ran (the POST answer is written before it runs)."""
    g = u.c.get(f"/api/groups/{gid}/records/{r.json()['id']}/review")
    assert g.status_code == 200, g.text
    return g.json()


def jpost(u, path, body):
    return u.c.post(path, json=body, headers={"X-CSRF-Token": u.c.cookies.get("moim_csrf")})


def mine(u, gid, i=0):
    return ex(u, gid)["my_todos"][i]


def my_feed(u, gid):
    return [f for f in ex(u, gid)["feed"] if f["id"]]


def m2_blob(ai):
    return json.dumps(ai.m2(), ensure_ascii=False)


def snapshot(db, task_id):
    return rows(db, "select title, deliverable, completion_criteria, evidence_types from todo_confirmed_items where task_id=:t", t=task_id)


# ---- group screen ---------------------------------------------------------------------------------------------------


def test_group_screen_shows_confirmed_roles_and_todos_but_no_hidden_criteria_or_private_text(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    html = a.c.get(f"/groups/{gid}").text
    data = ex(a, gid)
    crit = rows(db, "select deliverable, completion_criteria from todo_confirmed_items where group_id=:g", g=gid)
    blob = html + json.dumps(data, ensure_ascii=False)
    for r in crit:
        assert r.deliverable not in blob
        for c in r.completion_criteria:
            assert c not in blob
    assert SECRET_CONSTRAINT not in blob and INTERVIEW_ANSWER not in blob
    for word in ("deliverable", "completion_criteria", "evidence_types", "estimated_minutes", "rank", "member_state"):
        assert word not in json.dumps(data)
    roles = {m["role_name"] for m in data["members"]}
    assert None not in roles and len(data["members"]) == 2
    assert all(m["total"] >= 2 and m["done"] == 0 for m in data["members"])
    # my todos carry their task id + revision; the other member's todos carry only title, due date and done state
    assert all(set(t) == {"task_id", "confirmation_revision", "title", "due_date", "done_on", "record_id", "has_files"} for t in data["my_todos"])
    other = next(m["key"] for m in data["members"] if not m["is_me"])
    assert all(set(t) == {"title", "due_date", "done_on"} for t in data["member_todos"][other])
    assert b.id not in blob and a.id not in json.dumps(data)


def test_a_member_without_a_confirmation_shows_no_todos(db, mk, ai):
    gid, (a, b) = assigned_team(mk, 2)
    finish_interview(a, gid)
    assert tstart(a, gid).status_code == 202
    draft = [t["title"] for t in td(a, gid)["items"]]
    data = ex(b, gid)
    assert all(m["total"] == 0 and m["role_name"] is None for m in data["members"])
    assert not any(t in json.dumps(data, ensure_ascii=False) for t in draft)
    assert ex(a, gid)["my_todos"] == []


def test_outsiders_and_signed_out_users_get_nothing(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    gid2, (c, d) = assigned_team(mk, 2, leader="other")
    assert c.c.get(f"/api/groups/{gid}/execution").status_code == 404
    assert a.c.get("/api/groups/not-a-uuid/execution").status_code == 404
    a.c.cookies.delete("moim_session")
    assert a.c.get(f"/api/groups/{gid}/execution").status_code == 401


# ---- checkbox, records, undo -------------------------------------------------------------------------------------------


def test_checkbox_completes_at_once_and_undo_reverts_with_an_audit_trail(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    r = submit(a, gid, t, done=True)
    assert r.status_code == 201 and r.json()["review"] is None and not ai.m2()
    data = ex(b, gid)
    me = next(m for m in data["members"] if not m["is_me"])
    assert me["done"] == 1
    assert data["feed"][0]["title"] == t["title"] and data["feed"][0]["id"] is None  # others see no record id
    t = mine(a, gid)
    assert t["done_on"] and t["record_id"]
    rid = t["record_id"]
    u = jpost(a, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": False})
    assert u.status_code == 200
    assert mine(a, gid)["done_on"] is None and ex(a, gid)["feed"] == []
    kinds = [r.kind for r in rows(db, "select kind from execution_events where group_id=:g order by id", g=gid)]
    assert kinds == ["COMPLETE", "UNDO"]
    ev = rows(db, "select * from execution_events where group_id=:g", g=gid)
    assert all(e.task_id == t["task_id"] and e.confirmation_revision == t["confirmation_revision"] and e.user_id == a.id for e in ev)


def test_a_record_is_bound_to_exactly_one_submitter_task_and_confirmation_revision(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    rid = submit(a, gid, t, files=[("w.png", png())], done=True).json()["id"]
    rec = rows(db, "select * from execution_records where id=:r", r=rid)[0]
    conf = rows(db, "select source_assignment_revision, confirmation_revision from todo_confirmed_items where task_id=:t", t=t["task_id"])
    assert (rec.user_id, rec.task_id, rec.confirmation_revision) == (a.id, t["task_id"], t["confirmation_revision"])
    assert (rec.source_assignment_revision, rec.confirmation_revision) in [(c.source_assignment_revision, c.confirmation_revision) for c in conf]
    # someone else's task id is refused, never re-bound
    tb = mine(b, gid)
    assert submit(a, gid, tb, files=[("w.png", png())]).status_code == 409
    assert submit(a, gid, {"task_id": str(uuid.uuid4()), "confirmation_revision": t["confirmation_revision"]}, done=True).status_code == 409


def test_replayed_submission_writes_once_and_a_done_todo_cannot_be_done_twice(db, mk, ai, uploads):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    act = uuid.uuid4().hex
    r1 = submit(a, gid, t, files=[("w.png", png())], done=True, action=act)
    r2 = submit(a, gid, t, files=[("w.png", png())], done=True, action=act)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert scalar(db, "select count(*) from execution_files") == 1
    assert len(list(uploads.rglob("*"))) == 2  # the group directory and one file
    assert len(ai.m2()) == 1
    r3 = submit(a, gid, t, done=True)
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "ALREADY_DONE"


def test_revision_conflict_after_reconfirm_and_refresh_recovers(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    old = mine(a, gid)
    assert unlock(a, gid).status_code in (200, 201)
    assert confirm(a, gid).status_code in (200, 201)
    r = submit(a, gid, old, done=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_CONFLICT"
    new = next(x for x in ex(a, gid)["my_todos"] if x["task_id"] == old["task_id"])  # task id survives re-confirming
    assert new["confirmation_revision"] == old["confirmation_revision"] + 1
    assert submit(a, gid, new, done=True).status_code == 201
    fresh = reconnect(a)
    assert ex(fresh, gid)["my_todos"][0]["done_on"] or any(x["done_on"] for x in ex(fresh, gid)["my_todos"])


def test_an_etc_note_is_text_only_and_empty_submissions_are_refused(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    assert submit(a, gid, title="회의 메모", memo="다음 주 화요일 회의").status_code == 201
    r = submit(a, gid, title="자료", files=[("w.png", png())])
    assert r.status_code == 422 and r.json()["error"]["code"] == "ETC_NO_FILE"
    assert submit(a, gid).status_code == 422
    assert submit(a, gid, mine(a, gid)).status_code == 422  # a todo, not done, nothing attached
    assert submit(a, gid, title="x" * 41).status_code == 422
    assert submit(a, gid, memo="x" * 1001).status_code == 422
    assert ex(b, gid)["feed"][0]["title"] == "회의 메모"


def test_login_and_csrf_are_required_for_every_write(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    assert submit(a, gid, t, done=True, token=None).status_code == 403
    assert scalar(db, "select count(*) from execution_records") == 0
    rid = submit(a, gid, t, done=True).json()["id"]
    assert a.c.post(f"/api/groups/{gid}/records/{rid}/undo", json={"confirm_delete": False}).status_code == 403
    a.c.cookies.delete("moim_session")
    assert submit(a, gid, t, done=True).status_code == 401


# ---- attachments: validation and access ------------------------------------------------------------------------------


@pytest.mark.parametrize("name,content,code", [
    ("evil.png", b"MZ\x90\x00" + b"\0" * 100, "MISMATCH"),          # an executable named as an image
    ("photo.pdf", png(20, 20), "MISMATCH"),                          # an image named as a PDF
    ("doc.docx", pptx(), "MISMATCH"),                                # a PPTX named as a DOCX
    ("page.html", b"<script>alert(1)</script>", "TYPE"),
    ("vector.svg", b"<svg onload=alert(1)>", "TYPE"),
    ("tool.exe", b"MZ", "TYPE"),
    ("notes.txt", b"bin\x00ary", "MISMATCH"),
    ("broken.pdf", b"%PDF-1.4 garbage", "CORRUPT"),
    ("empty.png", b"", "EMPTY"),
    ("macro.docx", None, "MACRO"),
    ("bomb.docx", None, "ARCHIVE_LIMIT"),
    ("wide.png", None, "IMAGE_LIMIT"),
    ("huge.png", None, "IMAGE_LIMIT"),
    ("fake.hwp", F.OLE_MAGIC + b"\0" * 64, "MISMATCH"),
])
def test_disallowed_disguised_and_dangerous_files_are_refused(db, mk, ai, uploads, name, content, code):
    if name == "macro.docx":
        content = with_member(docx(), "word/vbaProject.bin", b"x")
    elif name == "bomb.docx":
        content = with_member(docx(), "word/bomb.bin", b"\0" * (6 * 1024 * 1024))
    elif name == "wide.png":
        content = png(12_001, 1)
    elif name == "huge.png":
        out = io.BytesIO()
        Image.new("1", (7000, 7000)).save(out, format="PNG")
        content = out.getvalue()
    gid, (a, b) = confirmed_team(mk, 2)
    r = submit(a, gid, mine(a, gid), files=[(name, content)], done=True)
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert scalar(db, "select count(*) from execution_records") == 0
    assert not uploads.exists() or not any(p.is_file() for p in uploads.rglob("*"))


def test_size_and_count_limits(db, mk, ai, monkeypatch):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    big = b"a" * (F.MAX_FILE_BYTES + 1)
    r = submit(a, gid, t, files=[("big.txt", big)])
    assert r.status_code == 422 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
    six = [(f"n{i}.txt", b"hello") for i in range(6)]
    r = submit(a, gid, t, files=six)
    assert r.status_code == 422 and r.json()["error"]["code"] == "TOO_MANY_FILES"
    r = a.c.post(f"/api/groups/{gid}/records", content=b"x", headers={
        "X-CSRF-Token": a.c.cookies.get("moim_csrf"), "Content-Length": str(60 * 1024 * 1024),
        "Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413
    monkeypatch.setattr(F, "GROUP_TOTAL_BYTES", 10)
    r = submit(a, gid, t, files=[("n.txt", b"hello world!")])
    assert r.status_code == 413 and r.json()["error"]["code"] == "GROUP_FULL"
    assert scalar(db, "select count(*) from execution_records") == 0


def test_eleven_formats_are_accepted_by_content(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    good = [("a.png", png(30, 30)), ("b.jpg", jpeg_with_exif(30, 30)), ("c.webp", webp()), ("d.pdf", pdf()), ("e.docx", docx())]
    assert submit(a, gid, t, files=good).status_code == 201
    more = [("f.hwp", hwp()), ("g.hwpx", hwpx()), ("h.txt", "텍스트".encode()), ("i.md", b"# md"), ("j.pptx", pptx())]
    assert submit(a, gid, t, files=more).status_code == 201
    assert submit(a, gid, t, files=[("k.xlsx", xlsx())]).status_code == 201
    kinds = {r.kind for r in rows(db, "select kind from execution_files")}
    assert kinds == {"PNG", "JPEG", "WEBP", "PDF", "DOCX", "HWP", "HWPX", "TXT", "MD", "PPTX", "XLSX"}


def test_path_and_markup_in_file_names_never_reach_a_path_or_markup(db, mk, ai, uploads):
    gid, (a, b) = confirmed_team(mk, 2)
    names = ["../../../../etc/passwd.png", "..\\..\\win.ini.png", '<img src=x onerror=alert(1)>.png']
    r = submit(a, gid, mine(a, gid), files=[(n, png(10, 10)) for n in names])
    assert r.status_code == 201, r.text
    stored = [x.original_name for x in rows(db, "select original_name from execution_files order by position")]
    assert stored == ["passwd.png", "win.ini.png", "<img src=x onerror=alert(1)>.png"]
    for p in uploads.rglob("*"):
        assert p.resolve().is_relative_to(uploads.resolve())
        assert re.fullmatch(r"[0-9a-f-]{36}", p.name), p.name  # server uuids only
    # rendered only with textContent (no innerHTML anywhere in the static scripts)
    js = (SRC / "static" / "group-exec.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js


def test_downloads_are_attachments_for_current_members_of_that_group_only(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    gid2, (c, d) = confirmed_team(mk, 2, leader="other")
    submit(a, gid, mine(a, gid), files=[("한글 이름.png", png(10, 10))], done=True)
    fid = ex(a, gid)["feed"][0]["files"][0]["id"]
    r = b.c.get(f"/api/groups/{gid}/files/{fid}")
    assert r.status_code == 200 and r.content == png(10, 10)
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;") and "filename*=UTF-8''" in r.headers["content-disposition"]
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]
    assert c.c.get(f"/api/groups/{gid}/files/{fid}").status_code == 404     # another group's member
    assert c.c.get(f"/api/groups/{gid2}/files/{fid}").status_code == 404    # the file id under the wrong group
    assert b.c.get(f"/api/groups/{gid}/files/..%2F..%2Fx").status_code == 404
    # a cohort member who is no longer in the group (stage 6 leave) loses access at once (D-D1 D5)
    with db.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("delete from group_members where group_id=:g and user_id=:u"), {"g": gid, "u": b.id})
    assert b.c.get(f"/api/groups/{gid}/files/{fid}").status_code == 404
    assert b.c.get(f"/api/groups/{gid}/execution").status_code == 404


def test_other_members_cannot_undo_or_read_my_review(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())], done=True).json()["id"]
    assert jpost(b, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": True}).status_code == 404
    assert b.c.get(f"/api/groups/{gid}/records/{rid}/review").status_code == 404
    assert jpost(b, f"/api/groups/{gid}/records/{rid}/review/retry", {"expected_attempt": 1}).status_code == 404
    assert "review" not in ex(b, gid)["feed"][0]
    assert ex(a, gid)["feed"][0]["review"]["state"] == "SUCCEEDED"


def test_undo_with_files_needs_confirmation_then_deletes_everything_permanently(db, mk, ai, uploads):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    rid = submit(a, gid, t, files=[("w.png", png()), ("n.txt", b"note")], memo="설명", done=True).json()["id"]
    assert len([p for p in uploads.rglob("*") if p.is_file()]) == 2
    r = jpost(a, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": False})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFIRM_REQUIRED"
    assert r.json()["error"]["message"] == "첨부 자료도 삭제되며 복구할 수 없어요. 되돌릴까요?"
    assert scalar(db, "select count(*) from execution_files") == 2  # cancelled: nothing changed
    assert jpost(a, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": True}).json() == {"deleted_files": 2}
    for table in ("execution_records", "execution_files", "execution_reviews"):
        assert scalar(db, f"select count(*) from {table}") == 0
    assert not [p for p in uploads.rglob("*") if p.is_file()]
    ev = rows(db, "select * from execution_events where kind='UNDO'")
    assert len(ev) == 1 and ev[0].file_count == 2  # metadata only: the log has no name or memo column
    assert mine(a, gid)["done_on"] is None


# ---- M2 review ------------------------------------------------------------------------------------------------------


def test_m2_input_is_only_that_todo_snapshot_and_that_submission(db, mk, ai, caplog):
    caplog.set_level(logging.DEBUG)
    gid, (a, b) = confirmed_team(mk, 2)
    mine_all = ex(a, gid)["my_todos"]
    t = mine_all[0]
    others = [x["title"] for x in mine_all[1:]] + [x["title"] for x in ex(b, gid)["my_todos"]]
    r = submit(a, gid, t, files=[("내비밀파일이름-PRIV.jpg", jpeg_with_exif())], title="와이어프레임", memo="세 장 정리", done=True)
    assert r.status_code == 201 and r.json()["review"]["state"] == "PENDING" and rv(a, gid, r)["state"] == "SUCCEEDED"
    assert len(ai.m2()) == 1
    messages = ai.m2()[0]
    body = json.loads(messages[-1]["content"][0]["text"])
    snap = snapshot(db, t["task_id"])[0]
    assert body["task"] == {"title": snap.title, "deliverable": snap.deliverable,
                            "completion_criteria": snap.completion_criteria, "allowed_evidence": snap.evidence_types}
    assert body["submission"]["description"] == "와이어프레임\n세 장 정리"
    blob = m2_blob(ai)
    forbidden_in(blob, [a, b], extra=[gid, t["task_id"], "PRIV", SECRET_CONSTRAINT, INTERVIEW_ANSWER, *others])
    # the image is a fresh copy: long side 2048, no EXIF
    url = next(p["image_url"]["url"] for p in messages[-1]["content"] if p["type"] == "image_url")
    assert url.startswith("data:image/jpeg;base64,")
    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(im.size) == 2048 and not im.getexif()
    # no file content, data URL or name in any log line
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "data:image" not in logs and "PRIV" not in logs and "세 장 정리" not in logs and snap.deliverable not in logs
    assert "review_event" in logs


def test_m2_never_changes_criteria_or_completion(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    before = snapshot(db, t["task_id"])
    ai.scripts["CompletionReview"].append(lambda body: review_for(body, "NOT_MET", "화면 3장 중 1장만 보여요"))
    r = submit(a, gid, t, files=[("w.png", png())], done=True)
    line = rv(a, gid, r)["line"]
    assert line == "AI 확인: 보완하면 좋은 점이 있어요 — 화면 3장 중 1장만 보여요"
    assert mine(a, gid)["done_on"]  # REVIEW_NEEDED does not cancel a manual completion (D-D1 D2)
    assert snapshot(db, t["task_id"]) == before
    # PASS on a submission that did not tick the box does not complete the todo
    t2 = next(x for x in ex(a, gid)["my_todos"] if not x["done_on"])
    r = submit(a, gid, t2, files=[("w.png", png())], done=False)
    assert rv(a, gid, r)["line"] == "AI 확인: 기준을 모두 충족했어요."
    assert next(x for x in ex(a, gid)["my_todos"] if x["task_id"] == t2["task_id"])["done_on"] is None
    ai.scripts["CompletionReview"].append(lambda body: review_for(body, "UNKNOWN", "완성본 파일을 올려 주세요"))
    r = submit(a, gid, t2, files=[("w.png", png())])
    assert rv(a, gid, r)["line"] == "AI 확인: 자료가 부족해요 — 완성본 파일을 올려 주세요"
    assert set(rows(db, "select decision from execution_reviews")[i].decision for i in range(3)) == {"REVIEW_NEEDED", "PASS", "NEEDS_INPUT"}


def test_an_inconsistent_or_unfounded_review_is_a_failure_with_a_user_retry(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)

    def lying(body):
        out = review_for(body, "NOT_MET")
        out["decision"] = "PASS"  # contradicts its own criteria results
        return out

    ai.scripts["CompletionReview"].append(lying)
    r = submit(a, gid, t, files=[("w.png", png())], done=True)
    v1 = rv(a, gid, r)
    assert v1["state"] == "FAILED" and v1["can_retry"] and v1["line"] == "AI 확인을 하지 못했어요. 다시 시도해 주세요."
    assert mine(a, gid)["done_on"]  # the failure does not block the manual completion
    rid = r.json()["id"]
    ai.scripts["CompletionReview"].append(lambda body: {**review_for(body, "MET"), "criteria_results": [
        {**x, "evidence_refs": [{"evidence_id": "e99", "description": "x"}]} for x in review_for(body)["criteria_results"]]})
    rr = jpost(a, f"/api/groups/{gid}/records/{rid}/review/retry", {"expected_attempt": 1})
    assert rr.status_code == 202
    assert a.c.get(f"/api/groups/{gid}/records/{rid}/review").json()["state"] == "FAILED"  # unknown evidence id
    assert jpost(a, f"/api/groups/{gid}/records/{rid}/review/retry", {"expected_attempt": 1}).status_code == 409  # stale attempt
    assert jpost(a, f"/api/groups/{gid}/records/{rid}/review/retry", {"expected_attempt": 2}).status_code == 202
    v = a.c.get(f"/api/groups/{gid}/records/{rid}/review").json()
    assert v["state"] == "SUCCEEDED" and v["attempt"] == 3
    assert len(ai.m2()) == 3  # one call per attempt, each started by the user
    assert jpost(a, f"/api/groups/{gid}/records/{rid}/review/retry", {"expected_attempt": 3}).status_code == 409


def test_provider_failures_are_shown_never_hidden(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    ai.scripts["CompletionReview"].append(AIError("TIMEOUT", "slow", True))
    r = submit(a, gid, mine(a, gid), files=[("w.png", png())], done=True)
    assert rv(a, gid, r)["line"] == "AI 응답이 늦어 확인하지 못했어요. 다시 시도해 주세요."
    ai.scripts["CompletionReview"].append(AIError("VISION_NOT_SUPPORTED", "x"))
    r = submit(a, gid, mine(a, gid, 1), files=[("w.png", png())])
    v = rows(db, "select state, error_code from execution_reviews where record_id=:r", r=r.json()["id"])[0]
    assert (v.state, v.error_code) == ("FAILED", "VISION_NOT_SUPPORTED")


def test_unsupported_formats_are_shared_but_not_reviewed(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    r = submit(a, gid, mine(a, gid), files=[("a.hwp", hwp()), ("b.hwpx", hwpx()), ("c.pptx", pptx()), ("d.xlsx", xlsx())], done=True)
    assert r.status_code == 201
    assert rv(a, gid, r)["line"] == "이 파일 형식은 AI 확인을 지원하지 않아요. 파일은 정상적으로 공유됐어요."
    assert not ai.m2()
    assert len(ex(b, gid)["feed"][0]["files"]) == 4 and mine(a, gid)["done_on"]


def test_mixed_submission_sends_a_fixed_phrase_instead_of_excluded_file_names(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    ai.scripts["CompletionReview"].append(lambda body: review_for(body, "UNKNOWN", "발표 자료를 PDF로 올려 주세요"))
    r = submit(a, gid, mine(a, gid), files=[("w.png", png()), ("발표비밀-HWPNAME.hwp", hwp()), ("x.pptx", pptx())])
    body = json.loads(ai.m2()[0][-1]["content"][0]["text"])
    assert body["excluded"] == ["검토에서 제외된 HWP 1개", "검토에서 제외된 PPTX 1개"]
    assert "HWPNAME" not in m2_blob(ai) and "x.pptx" not in m2_blob(ai)
    assert rv(a, gid, r)["line"].endswith("AI는 1개 파일만 확인했어요.")


def test_over_the_review_limit_skips_m2_but_shares_the_files(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    r = submit(a, gid, mine(a, gid), files=[("long.txt", ("가" * (F.REVIEW_MAX_TEXT + 1)).encode())], done=True)
    assert rv(a, gid, r)["line"] == "자료가 AI 확인 한도를 넘어 AI 확인은 하지 않았어요. 파일은 정상적으로 공유됐어요."
    r = submit(a, gid, mine(a, gid, 1), files=[("p1.pdf", pdf(12)), ("p2.pdf", pdf(9))])
    assert rv(a, gid, r)["state"] == "SKIPPED"
    assert not ai.m2() and scalar(db, "select count(*) from execution_files") == 3


def test_pdf_is_sent_as_a_metadata_free_file_part_and_docx_as_text(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    r = submit(a, gid, mine(a, gid), files=[("spec.pdf", pdf()), ("doc.docx", docx(picture=True)), ("n.md", "# 메모 CODE-MD".encode())])
    parts = ai.m2()[0][-1]["content"]
    filep = next(p for p in parts if p["type"] == "file")["file"]
    assert filep["filename"] == "evidence-1.pdf" and filep["file_data"].startswith("data:application/pdf;base64,")
    doc = fitz.open(stream=base64.b64decode(filep["file_data"].split(",", 1)[1]), filetype="pdf")
    assert not doc.metadata.get("author") and not doc.metadata.get("title") and doc.page_count == 1
    texts = [p["text"] for p in parts if p["type"] == "text"]
    assert any(t.startswith("[evidence:e2] DOCX 본문 CODE-DOCX") for t in texts)
    assert any(t.startswith("[evidence:e3] # 메모 CODE-MD") for t in texts)
    assert sum(p["type"] == "image_url" for p in parts) == 0  # the picture inside the DOCX is not sent
    assert rv(a, gid, r)["line"].endswith("문서 속 이미지는 확인하지 않았어요.")


def test_m2_disabled_keeps_the_manual_flow(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    app.state.execution.review_enabled = False
    r = submit(a, gid, mine(a, gid), files=[("w.png", png())], done=True)
    assert r.status_code == 201 and r.json()["review"] is None and not ai.m2()
    assert mine(a, gid)["done_on"] and ex(a, gid)["review_enabled"] is False


def test_an_interrupted_review_becomes_a_retryable_failure_after_restart_and_refresh_shows_it(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())], done=True).json()["id"]
    with db.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("update execution_reviews set state='RUNNING', decision=null where record_id=:r"), {"r": rid})
    assert app.state.execution.fail_interrupted() == 1
    fresh = reconnect(a)
    v = next(f for f in ex(fresh, gid)["feed"] if f["id"] == rid)["review"]
    assert v["state"] == "FAILED" and v["can_retry"] and v["line"] == "서버가 다시 시작되어 AI 확인이 중단됐어요. 다시 시도해 주세요."
    assert jpost(fresh, f"/api/groups/{gid}/records/{rid}/review/retry", {"expected_attempt": v["attempt"]}).status_code == 202
    assert fresh.c.get(f"/api/groups/{gid}/records/{rid}/review").json()["state"] == "SUCCEEDED"


def test_undo_during_a_review_leaves_no_trace(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    svc = app.state.execution
    svc.review_enabled = True
    t = mine(a, gid)
    # run the review by hand after the record is gone: every write is a no-op, nothing is called
    with db.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("select 1"))
    rid = submit(a, gid, t, files=[("n.txt", b"hello")], done=True).json()["id"]
    assert jpost(a, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": True}).status_code == 200
    calls = len(ai.m2())
    svc.run_review(rid, 1)
    assert len(ai.m2()) == calls and scalar(db, "select count(*) from execution_reviews") == 0


def test_activity_and_progress_follow_the_records(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), done=True)
    submit(b, gid, mine(b, gid), files=[("w.png", png())], memo="메모", done=True)
    submit(b, gid, title="기타 메모")
    data = ex(a, gid)
    keys = {m["is_me"]: m["key"] for m in data["members"]}
    assert sum(data["activity"][keys[True]].values()) == 1 and sum(data["activity"][keys[False]].values()) == 2
    assert sum(m["done"] for m in data["members"]) == 2
    assert [f["label"].startswith("오늘") for f in data["feed"]] == [True, True, True]


def test_template_and_static_carry_no_hidden_fields_and_original_html_is_untouched():
    tpl = (SRC / "templates" / "group.html").read_text(encoding="utf-8")
    js = (SRC / "static" / "group-exec.js").read_text(encoding="utf-8")
    for word in ("deliverable", "completion_criteria", "evidence_types", "criteria_results", "timely", "TIMELY"):
        assert word not in tpl and word not in js
    assert "|safe" not in tpl and "localStorage" not in js and "sessionStorage" not in js


def test_migration_0011_down_and_up(db, grp_db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    cfg = Config(str(ROOT / "alembic.ini"))
    try:
        command.downgrade(cfg, "0010")
        assert scalar(db, "select to_regclass('public.execution_records')") is None
        assert scalar(db, "select count(*) from todo_confirmations where group_id=:g", g=gid) == 2
        command.upgrade(cfg, "head")
        assert scalar(db, "select to_regclass('public.execution_events')") is not None
    finally:
        command.upgrade(cfg, "head")
