"""Development-only VISUAL FIXTURE for the stage 5 group screen (used by `capture_screens.py --execution`).

Takes the active group made by `apply_dev_seed` (PARK, KIM, LEE, CHOI) and drives the real API in-process with the
deterministic fixture AI of the test suite: ranking -> server assignment -> interview -> todo draft -> `투두 확정하기`,
then a few completion records with generated files and one fixture M2 review. Nothing here calls Timely and nothing is
reported as LIVE. To give the activity grid some history it moves the project start 20 days back and back-dates some
records (visual fixture only, development database only).
"""
from __future__ import annotations

import io
import os
import random
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def seed_execution(seed: dict) -> str:
    if os.environ.get("MOIM_ENV", "development") not in ("development", "test"):
        raise SystemExit("refusing to seed outside a development/test environment")
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests"))
    from fastapi.testclient import TestClient
    from PIL import Image
    from sqlalchemy import text

    from moim.config import load_settings
    from moim.main import create_app
    from test_execution import ExecAI, review_for  # fixture client: never LIVE
    from test_role_assignment import rank
    from test_todos import confirm, finish_interview, tstart

    gid = seed["vars"]["active_id"]
    app = create_app(load_settings(), goal_client_factory=lambda: None, ai_client_factory=ExecAI,
                     rng_factory=lambda: random.Random(5))
    eng = app.state.engine
    with eng.connect() as c:
        rows = c.execute(text("select u.id, u.display_name from cohort_members m join users u on u.id=m.user_id "
                              "where m.group_id=:g order by u.display_name"), {"g": gid}).all()
    people = {}
    for uid, name in rows:
        client = TestClient(app, follow_redirects=False)
        client.get("/login")
        client.cookies.set("moim_session", app.state.sessions.create(uid, False)[0], domain="testserver.local")
        people[name] = SimpleNamespace(id=uid, name=name, c=client)
    order = ["PARK", "KIM", "CHOI", "LEE"]
    prefs = {"PARK": ["r1", "r5", "r2"], "KIM": ["r2", "r1", "r3"], "CHOI": ["r1", "r3", "r2"], "LEE": ["r4", "r5", "r1"]}
    for n in order:
        assert rank(people[n], gid, prefs[n]).status_code == 201
    for n in order:
        u = people[n]
        finish_interview(u, gid)
        assert tstart(u, gid).status_code == 202
        assert confirm(u, gid).status_code in (200, 201)

    def ex(u):
        return u.c.get(f"/api/groups/{gid}/execution").json()

    def png(color):
        out = io.BytesIO()
        Image.new("RGB", (640, 400), color).save(out, format="PNG")
        return out.getvalue()

    def pptx():  # a format the AI cannot read: the attempt goes straight to the team vote (D-H1)
        import zipfile

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("[Content_Types].xml", b"<Types/>")
            z.writestr("ppt/presentation.xml", b"<p/>")
        return out.getvalue()

    def post(u, task, files=(), title=None, memo=None):
        # D-H1: completion only through attached material (AI PASS here with the fixture AI, or the team vote)
        data = {"action_id": uuid.uuid4().hex, "task_id": task["task_id"],
                "confirmation_revision": str(task["confirmation_revision"])}
        if title:
            data["title"] = title
        if memo:
            data["memo"] = memo
        fl = [("files", f) for f in files]
        r = u.c.post(f"/api/groups/{gid}/records", data=data, files=fl or None,
                     headers={"X-CSRF-Token": u.c.cookies.get("moim_csrf")})
        assert r.status_code in (200, 201), r.text
        return r.json()["id"]

    made = []
    for n, k, files, memo in (
        ("KIM", 0, [("wireframe_v2.png", png((47, 84, 235)), "image/png")], "대시보드·그룹 상세 와이어프레임 3장 정리했어요."),
        ("KIM", 1, [("api_명세.md", "# API 명세 초안".encode(), "text/markdown")], None),
        ("CHOI", 0, [("인터뷰_질문지.txt", "질문 12개".encode(), "text/plain")], "팀플 경험, 불편했던 점 순서로 질문을 만들었어요."),
        ("LEE", 0, [("경쟁서비스_비교.pptx", pptx(), "application/octet-stream")], "비교표를 슬라이드로 만들었어요."),
        ("PARK", 0, [("기능명세서_v1.png", png((242, 106, 79)), "image/png")], "대시보드부터 그룹 상세까지 기능 목록을 정리했어요."),
    ):
        u = people[n]
        t = ex(u)["my_todos"][k]
        made.append((n, post(u, t, files=files, memo=memo)))
    # history for the activity grid (visual fixture): project start 20 days back, older records spread over it
    with eng.begin() as c:
        rev = c.execute(text("select brief_revision from groups where id=:g"), {"g": gid}).scalar_one()
        c.execute(text("update project_briefs set start_date = start_date - 20 where group_id=:g and revision=:r"),
                  {"g": gid, "r": rev})
        for i, (_, rid) in enumerate(made[:-1]):
            c.execute(text("update execution_records set created_at = now() - make_interval(days => :d) where id=:r"),
                      {"d": [1, 3, 1, 6][i], "r": rid})
    app.state.engine.dispose()
    return f"execution fixture: 4 confirmed members, {len(made)} records (fixture AI, not LIVE)"


if __name__ == "__main__":
    print("use: tools/capture_screens.py --execution")
