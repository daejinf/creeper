import logging

import pytest

from moim.logging_utils import install_redaction, redact


@pytest.mark.parametrize(
    "raw,secret",
    [
        ("connect postgresql+psycopg://moim:s3cretpw@127.0.0.1:5432/db failed", "s3cretpw"),
        ("password=hunter2 user=a", "hunter2"),
        ("api_key: sk-abc123", "sk-abc123"),
        ("Authorization: Bearer tok.en-123", "tok.en-123"),
        ("token=abcdef", "abcdef"),
    ],
)
def test_redact_masks_secrets(raw, secret):
    out = redact(raw)
    assert secret not in out
    assert "***" in out


def test_handler_filter_masks_logged_secrets(caplog):
    logger = logging.getLogger("moim.test")
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install_redaction(root)
        logger.warning("db %s", "postgresql+psycopg://moim:s3cretpw@h/db")
    finally:
        root.removeHandler(handler)
    assert records and "s3cretpw" not in records[0]


def test_app_startup_log_has_no_password(test_environ, caplog):
    from fastapi.testclient import TestClient

    from moim.main import create_app

    caplog.set_level(logging.INFO)
    password = test_environ.split(":")[2].split("@")[0]
    with TestClient(create_app()):
        pass
    assert any("starting env=test" in r.getMessage() for r in caplog.records)
    assert all(password not in r.getMessage() for r in caplog.records)
