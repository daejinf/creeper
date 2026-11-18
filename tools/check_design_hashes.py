"""Verify the 9 original design HTML files against the approved table in docs/PRODUCT.md.

Read-only. Never prints file contents. Exit 0 = identical to approved baseline.
Checks: SHA-256, byte size, LF line count, no CR, no BOM, trailing newline.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\|\s*`(moim-[a-z-]+\.html)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]{64})`\s*\|", re.M)


def load_baseline(product_md: Path | None = None) -> dict[str, tuple[int, int, str]]:
    text = (product_md or ROOT / "docs" / "PRODUCT.md").read_text(encoding="utf-8")
    table = {m[1]: (int(m[2]), int(m[3]), m[4]) for m in ROW.finditer(text)}
    if len(table) != 9:
        raise SystemExit(f"baseline table must have 9 rows, found {len(table)}")
    return table


def check(root: Path = ROOT, baseline: dict | None = None) -> list[str]:
    baseline = baseline or load_baseline()
    problems: list[str] = []
    for name, (size, lines, sha) in sorted(baseline.items()):
        path = root / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            problems.append(f"{name}: SHA-256 differs from baseline")
        if len(data) != size:
            problems.append(f"{name}: size {len(data)} != {size}")
        if b"\r" in data:
            problems.append(f"{name}: contains CR (line-ending changed)")
        if data.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{name}: BOM present")
        if not data.endswith(b"\n"):
            problems.append(f"{name}: no trailing newline")
        if data.count(b"\n") != lines:
            problems.append(f"{name}: line count {data.count(b'\n')} != {lines}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("DESIGN HASH CHECK FAILED")
        for p in problems:
            print(" -", p)
        return 1
    print("design hash check OK: 9/9 identical to docs/PRODUCT.md baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
