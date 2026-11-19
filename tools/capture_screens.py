"""Capture each original design HTML and its implemented screen at exactly 1440x900, side by side.

Every image is the 1440x900 viewport (the first screen). Use --full-page to capture the whole scrollable page.

    .venv\\Scripts\\python.exe tools\\capture_screens.py [--base-url http://127.0.0.1:8000] [--out artifacts\\screens]

The originals are opened read-only through file:// and are never written. There is no pixel comparison,
no baseline image and no tolerance: the output is for people to look at (open <out>\\index.html).
A screen whose implementation route is still None is recorded as "not built yet", not as a failure.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWPORT = {"width": 1440, "height": 900}

# original file -> implemented route. None = not built yet (later stages fill these in).
# Placeholders ({room_id}, {active_id}) are filled from the development seed. AUTH names the seeded user whose session
# the browser carries for that screen; screens not listed there are captured signed out.
SCREENS: dict[str, str | None] = {
    "moim-landing.html": "/",
    "moim-signup.html": "/signup",
    "moim-login.html": "/login",
    "moim-dashboard.html": "/dashboard",
    "moim-invite.html": "/invite",
    "moim-group-create.html": "/groups/{room_id}/create",
    "moim-chat.html": "/groups/{active_id}/chat",
    "moim-group.html": "/groups/{active_id}",
    "moim-mypage.html": "/mypage",
}
AUTH = {
    "moim-dashboard.html": "lead",
    "moim-invite.html": "roomlead",
    "moim-group-create.html": "roomlead",
    "moim-group.html": "lead",
    "moim-chat.html": "lead",
    "moim-mypage.html": "lead",
}
# The original invite page shows its signed-in header only through its own demo switch.
ORIGINAL_QUERY = {"moim-invite.html": "?login=1"}


def apply_dev_seed() -> dict:
    """Development seed: fixed users (username prefix `shot`) with an active group, the leader's own empty invite room
    and a second invite room with three people. Re-running replaces only those rows.

    The active group's team-goal sentence is a hand-written VISUAL FIXTURE so the header has something to show.
    It is not a Timely result and is never reported as LIVE.
    """
    if os.environ.get("MOIM_ENV", "development") not in ("development", "test"):
        raise SystemExit("refusing to seed outside a development/test environment")
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import uuid
        from datetime import timedelta

        from sqlalchemy import text

        from moim.auth.sessions import SessionStore
        from moim.config import load_settings
        from moim.db import make_engine, make_session_factory
        from moim.groups.service import GroupService, today_kst
        from moim.groups.validation import BriefData, Milestone
        from moim.timeutil import utcnow

        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        return {"note": f"seed unavailable: {type(exc).__name__}"}
    engine = make_engine(settings)
    sf = make_session_factory(engine)
    sessions, groups = SessionStore(sf), GroupService(sf)
    shot = "(select id from users where username like 'shot%')"
    owned = f"(select id from groups where owner_id in {shot})"
    with sf.begin() as s:
        # A seeded group that reached `투두 확정하기` (stage 5 `--execution`) cannot be deleted: confirmed todos are
        # immutable (DB trigger). Its seed users are retired instead: renamed out of the `shot` prefix, signed out.
        confirmed = "(select user_id from cohort_members where group_id in (select group_id from todo_confirmations))"
        s.execute(text(f"delete from auth_sessions where user_id in {shot} and user_id in {confirmed}"))
        s.execute(text(f"update users set username = 'oldshot' || substr(replace(id, '-', ''), 1, 9) "
                       f"where username like 'shot%' and id in {confirmed}"))
        for sql in (
            f"delete from member_progress where group_id in {owned}",
            f"delete from preference_submissions where group_id in {owned}",
            f"delete from member_states where group_id in {owned}",
            f"delete from interview_turns where group_id in {owned}",
            f"delete from role_candidate_sets where group_id in {owned}",
            f"delete from goal_displays where group_id in {owned}",
            f"delete from cohort_members where group_id in {owned}",
            f"delete from activation_cohorts where group_id in {owned}",
            f"delete from project_briefs where group_id in {owned}",
            f"delete from invite_codes where group_id in {owned}",
            f"delete from group_members where group_id in {owned}",
            f"delete from groups where owner_id in {shot}",
            f"delete from auth_sessions where user_id in {shot}",
            "delete from users where username like 'shot%'",
        ):
            s.execute(text(sql))
    names = {"lead": ("shotlead", "PARK"), "kim": ("shotkim", "KIM"), "lee": ("shotlee", "LEE"), "choi": ("shotchoi", "CHOI"),
             "roomlead": ("shotroom", "JIN"), "seo": ("shotseo", "SEO"), "min": ("shotmin", "MIN")}
    ids = {}
    with sf.begin() as s:
        for key, (username, display) in names.items():
            ids[key] = str(uuid.uuid4())
            s.execute(text("insert into users (id, username, display_name, password_hash, terms_accepted_at) "
                           "values (:i, :u, :d, 'seed-not-loginable', now())"), {"i": ids[key], "u": username, "d": display})
    today = today_kst(utcnow())

    def room(leader, members):
        gid, _ = groups.create_or_reuse_room(ids[leader])
        code = groups.room(ids[leader], gid).code
        for m in members:
            groups.join_by_code(ids[m], code)
        return gid

    active = room("lead", ["kim", "lee", "choi"])
    groups.activate(ids["lead"], active, BriefData(
        "캡스톤 디자인", "팀플 협업 서비스의 MVP를 만들고 최종 발표까지 마친다.", today, today + timedelta(days=12),
        ("보고서", "PPT"), (Milestone("중간 발표", today + timedelta(days=6)),)))
    own_room = room("lead", [])  # the leader's own empty invite room, shown as an "초대 대기" card
    invite = room("roomlead", ["seo", "min"])
    with sf.begin() as s:
        s.execute(text("update goal_displays set state='SUCCEEDED', text=:t, model='visual-fixture' where group_id=:g"),
                  {"t": "팀플 협업 서비스 MVP를 완성하고 최종 발표하기", "g": active})
    # VISUAL FIXTURE for the chat screen: a shared role-candidate set (D-B3: no interview). Hand-written, not Timely.
    import json

    candidates = [{"id": f"r{i}", "name": n, "description": d} for i, (n, d) in enumerate(
        [("기획·자료조사", "방향을 잡고 자료를 모아요"), ("디자인", "화면과 발표 자료를 꾸며요"), ("개발", "서비스를 구현해요"),
         ("발표", "최종 발표를 준비해요"), ("문서 정리", "보고서를 정리해요")], 1)]
    with sf.begin() as s:
        s.execute(text("update role_candidate_sets set state='SUCCEEDED', candidates=cast(:c as jsonb), model='visual-fixture' where group_id=:g"),
                  {"c": json.dumps(candidates, ensure_ascii=False), "g": active})
    tokens = {key: sessions.create(ids[key], False)[0] for key in ("lead", "roomlead")}
    engine.dispose()
    return {"note": "seeded 7 users, 1 active group, 2 invite rooms (goal sentence is a visual fixture)",
            "tokens": tokens, "vars": {"active_id": active, "room_id": invite, "own_room_id": own_room}}


def launch(playwright, browser_name: str):
    """Bundled Chromium first, then the installed Edge/Chrome so no browser download is needed."""
    order = [browser_name] if browser_name != "auto" else ["chromium", "msedge", "chrome"]
    errors = []
    for name in order:
        try:
            if name == "chromium":
                return playwright.chromium.launch(), name
            return playwright.chromium.launch(channel=name), name
        except Exception as exc:  # noqa: BLE001 - try the next browser
            errors.append(f"{name}: {str(exc).splitlines()[0]}")
    raise SystemExit("no browser could be started:\n  " + "\n  ".join(errors))


def shot(browser, url: str, path: Path, full_page: bool = False, session: str | None = None) -> None:
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    try:
        if session:
            host = url.split("//", 1)[1].split("/", 1)[0].split(":")[0]
            context.add_cookies([{"name": "moim_session", "value": session, "domain": host, "path": "/"}])
        page = context.new_page()
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(path), full_page=full_page)  # default: exactly the 1440x900 viewport
    finally:
        context.close()


def write_index(out: Path, rows: list[dict]) -> None:
    cells = []
    for row in rows:
        name = html.escape(row["name"])
        original = f'<img src="{name}/original.png" alt="original {name}">'
        if row["impl"] == "captured":
            impl = f'<img src="{name}/impl.png" alt="implementation {name}">'
        else:
            impl = f'<p class="missing">{html.escape(row["note"])}</p>'
        cells.append(f"<section><h2>{name}</h2><div class=\"pair\"><figure><figcaption>original</figcaption>{original}</figure>"
                     f"<figure><figcaption>implementation</figcaption>{impl}</figure></div></section>")
    page = ("<!doctype html><meta charset=\"utf-8\"><title>MOIM screen pairs</title>"
            "<style>body{font:14px sans-serif;margin:16px}.pair{display:flex;gap:12px;align-items:flex-start}"
            "figure{margin:0;flex:1;min-width:0}img{width:100%;border:1px solid #ccc}.missing{color:#a00}</style>"
            "<h1>MOIM screen pairs (1440x900)</h1>" + "".join(cells))
    (out / "index.html").write_text(page, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "screens"))
    ap.add_argument("--browser", default="auto", choices=["auto", "chromium", "msedge", "chrome"])
    ap.add_argument("--full-page", action="store_true",
                    help="capture the whole scrollable page instead of exactly 1440x900 (sizes then vary)")
    ap.add_argument("--execution", action="store_true",
                    help="also seed the stage 5 group screen (confirmed todos and records; fixture AI, not LIVE)")
    ap.add_argument("--only", action="append", help="capture only this original file name (repeatable)")
    ap.add_argument("--route", action="append", default=[], metavar="FILE=/path",
                    help="override an implementation route for this run, e.g. moim-login.html=/login")
    args = ap.parse_args(argv)

    routes = dict(SCREENS)
    for item in args.route:
        name, _, route = item.partition("=")
        if name not in routes or not route.startswith("/"):
            raise SystemExit(f"bad --route {item!r}: use <original file>=/path")
        routes[name] = route
    names = args.only or list(routes)
    unknown = [n for n in names if n not in routes]
    if unknown:
        raise SystemExit(f"unknown screen(s): {', '.join(unknown)}")

    seed = apply_dev_seed()
    if args.execution and seed.get("vars"):
        sys.path.insert(0, str(ROOT / "tools"))
        from seed_execution import seed_execution

        print("seed:", seed_execution(seed))
    print("seed:", seed["note"])
    fill = seed.get("vars", {})
    tokens = seed.get("tokens", {})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    rows, failed = [], False
    with sync_playwright() as p:
        browser, used = launch(p, args.browser)
        print("browser:", used)
        for name in names:
            source = ROOT / name
            if not source.is_file():
                print(f"FAIL {name}: original not found")
                failed = True
                continue
            folder = out / name
            folder.mkdir(parents=True, exist_ok=True)
            row = {"name": name, "impl": "missing", "note": ""}
            shot(browser, source.as_uri() + ORIGINAL_QUERY.get(name, ""), folder / "original.png", args.full_page)
            route = routes[name]
            if route is None:
                row["note"] = "implementation not built yet"
            else:
                try:
                    route = route.format(**fill) if "{" in route else route
                    who = AUTH.get(name)
                    if who and who not in tokens:
                        raise RuntimeError("needs the development seed (" + seed["note"] + ")")
                    shot(browser, args.base_url.rstrip("/") + route, folder / "impl.png", args.full_page, tokens.get(who))
                    row["impl"] = "captured"
                except (Exception, KeyError) as exc:  # noqa: BLE001
                    row["note"] = "implementation capture failed: " + str(exc).splitlines()[0]
                    failed = True
            print(f"{'OK  ' if row['impl'] == 'captured' else 'PART'} {name}: {row['note'] or 'pair saved'}")
            rows.append(row)
        browser.close()
    write_index(out, rows)
    print("index:", out / "index.html")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
