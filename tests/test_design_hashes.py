import shutil
from pathlib import Path

import pytest

from tools import check_design_hashes as chk


def test_original_html_matches_approved_baseline():
    assert chk.check() == []


@pytest.fixture()
def copy_root(tmp_path):
    base = chk.load_baseline()
    for name in base:
        shutil.copyfile(chk.ROOT / name, tmp_path / name)
    return tmp_path, base


def test_clean_copy_passes(copy_root):
    root, base = copy_root
    assert chk.check(root, base) == []


def test_crlf_conversion_detected(copy_root):
    root, base = copy_root
    p = root / "moim-login.html"
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    problems = chk.check(root, base)
    assert any("CR" in x for x in problems) and any("SHA-256" in x for x in problems)


def test_content_change_detected(copy_root):
    root, base = copy_root
    p = root / "moim-chat.html"
    p.write_bytes(p.read_bytes() + b" ")
    assert any("moim-chat.html" in x for x in chk.check(root, base))


def test_missing_file_detected(copy_root):
    root, base = copy_root
    (root / "moim-signup.html").unlink()
    assert "moim-signup.html: missing" in chk.check(root, base)


def test_baseline_needs_nine_rows(tmp_path):
    f = tmp_path / "PRODUCT.md"
    f.write_text("| `moim-login.html` | 1 | 1 | `" + "0" * 64 + "` |\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        chk.load_baseline(f)
