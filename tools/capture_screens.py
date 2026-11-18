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
SCREENS: dict[str, str | None] = {
    "moim-landing.html": "/",
    "moim-signup.html": "/signup",
    "moim-login.html": "/login",
    "moim-dashboard.html": None,
    "moim-invite.html": None,
    "moim-group-create.html": None,
    "moim-chat.html": None,
    "moim-group.html": None,
    "moim-mypage.html": None,
}


def apply_dev_seed() -> str:
    """Development seed hook. There are no product tables yet, so there is nothing to seed."""
    if os.environ.get("MOIM_ENV", "development") not in ("development", "test"):
        raise SystemExit("refusing to seed outside a development/test environment")
    return "nothing to seed yet (no product tables)"


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


def shot(browser, url: str, path: Path, full_page: bool = False) -> None:
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    try:
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

    print("seed:", apply_dev_seed())
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
            shot(browser, source.as_uri(), folder / "original.png", args.full_page)
            route = routes[name]
            if route is None:
                row["note"] = "implementation not built yet"
            else:
                try:
                    shot(browser, args.base_url.rstrip("/") + route, folder / "impl.png", args.full_page)
                    row["impl"] = "captured"
                except Exception as exc:  # noqa: BLE001
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
