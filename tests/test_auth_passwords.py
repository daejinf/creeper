import os
import subprocess
import sys
import time

import pytest

from wemeet_backend.auth import passwords as pw


def test_hash_verify_roundtrip():
    h = pw.hash_password("correct horse")
    assert h.startswith("scrypt$32768$8$1$")
    assert pw.verify_password("correct horse", h)
    assert not pw.verify_password("wrong", h)


def test_unicode_password():
    h = pw.hash_password("비밀번호🔑123")
    assert pw.verify_password("비밀번호🔑123", h)
    assert not pw.verify_password("비밀번호🔑124", h)


def test_salt_makes_hashes_differ():
    assert pw.hash_password("same") != pw.hash_password("same")


def test_hash_does_not_contain_plaintext():
    assert "hunter2pass" not in pw.hash_password("hunter2pass")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "plaintext",
        "scrypt$x$8$1$AA==$AA==",
        "bcrypt$32768$8$1$AAAA$AAAA",
        "scrypt$32768$8$1$not base64!$AAAA",
        "scrypt$32768$8$1$$",
        "scrypt$3$8$1$AAAA$AAAA",  # N not a power of two
        f"scrypt${2**30}$8$1$AAAA$AAAA",  # N above cap
        "scrypt$32768$8$1$AAAA$AAAA$extra",
    ],
)
def test_malformed_hash_returns_false(bad):
    assert pw.verify_password("x", bad) is False


def test_non_str_inputs_return_false():
    h = pw.hash_password("abc12345")
    assert pw.verify_password(None, h) is False
    assert pw.verify_password("abc12345", None) is False


def test_too_long_password():
    with pytest.raises(ValueError):
        pw.hash_password("a" * (pw.MAX_PASSWORD_BYTES + 1))
    assert pw.verify_password("a" * (pw.MAX_PASSWORD_BYTES + 1), pw.hash_password("a")) is False


def test_dummy_hash_valid_cached_and_rejects_guess():
    d = pw.dummy_hash()
    assert d is pw.dummy_hash()
    assert pw.verify_password("anything", d) is False


def test_timing_report(capsys):
    start = time.perf_counter()
    h = pw.hash_password("timing-check")
    hash_s = time.perf_counter() - start
    start = time.perf_counter()
    pw.verify_password("timing-check", h)
    verify_s = time.perf_counter() - start
    with capsys.disabled():
        print(f"\n[scrypt] hash={hash_s * 1000:.0f}ms verify={verify_s * 1000:.0f}ms")
    assert hash_s < 5 and verify_s < 5


def test_passwords_module_has_no_wemeet_ai_import():
    code = (
        "import sys; import wemeet_backend.auth.passwords; "
        "sys.exit(1 if any(m == 'wemeet_ai' or m.startswith('wemeet_ai.') for m in sys.modules) else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": "src"}, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.returncode == 0
