"""Stage 7: the backup file format of tools/ops_db.py (no database needed).

The full provision -> migrate -> backup -> restore -> verify cycle runs against separate rehearsal databases, which
the test guard (exactly `moim_test`) deliberately does not allow; it is recorded in docs/OPERATIONS.md.
"""
from __future__ import annotations

import base64
import os
import uuid

import pytest

pytest.importorskip("cryptography")

from tools import ops_db  # noqa: E402


@pytest.fixture()
def key(monkeypatch):
    k = os.urandom(32)
    monkeypatch.setenv("MOIM_BACKUP_KEY", base64.b64encode(k).decode())
    return k


@pytest.mark.parametrize("size", [0, 1, ops_db.CHUNK - 1, ops_db.CHUNK, ops_db.CHUNK * 2 + 17])
def test_encrypt_roundtrip(tmp_path, key, size):
    data = os.urandom(size)
    (tmp_path / "plain").write_bytes(data)
    ops_db.encrypt_file(tmp_path / "plain", tmp_path / "enc", key)
    if size >= 64:  # the plaintext never appears in the file (shorter samples can match by chance)
        assert data[:64] not in (tmp_path / "enc").read_bytes()
    ops_db.decrypt_file(tmp_path / "enc", tmp_path / "out", key)
    assert (tmp_path / "out").read_bytes() == data


def _encrypted(tmp_path, key, size=ops_db.CHUNK * 2 + 5):
    (tmp_path / "plain").write_bytes(os.urandom(size))
    ops_db.encrypt_file(tmp_path / "plain", tmp_path / "enc", key)
    return (tmp_path / "enc").read_bytes()


def test_wrong_key_is_rejected(tmp_path, key):
    _encrypted(tmp_path, key)
    with pytest.raises(ops_db.OpsError, match="authentication"):
        ops_db.decrypt_file(tmp_path / "enc", tmp_path / "out", os.urandom(32))


def test_tampered_byte_is_rejected(tmp_path, key):
    blob = bytearray(_encrypted(tmp_path, key))
    blob[100] ^= 1
    (tmp_path / "bad").write_bytes(bytes(blob))
    with pytest.raises(ops_db.OpsError, match="authentication"):
        ops_db.decrypt_file(tmp_path / "bad", tmp_path / "out", key)


def test_truncation_at_a_chunk_boundary_is_rejected(tmp_path, key):
    blob = _encrypted(tmp_path, key)
    first_frame = 16 + 5 + ops_db.CHUNK + 16  # header, frame header, one full chunk and its tag
    (tmp_path / "cut").write_bytes(blob[:first_frame])
    with pytest.raises(ops_db.OpsError, match="truncated"):
        ops_db.decrypt_file(tmp_path / "cut", tmp_path / "out", key)


def test_appended_data_is_rejected(tmp_path, key):
    (tmp_path / "extra").write_bytes(_encrypted(tmp_path, key) + b"x")
    with pytest.raises(ops_db.OpsError, match="after the last chunk"):
        ops_db.decrypt_file(tmp_path / "extra", tmp_path / "out", key)


def test_key_must_be_32_bytes(monkeypatch):
    monkeypatch.setenv("MOIM_BACKUP_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ops_db.OpsError):
        ops_db._key()
    monkeypatch.setenv("MOIM_BACKUP_KEY", "not base64 !!")
    with pytest.raises(ops_db.OpsError):
        ops_db._key()


def test_orphans_are_files_no_row_points_at(tmp_path):
    g, f1, f2 = (str(uuid.uuid4()) for _ in range(3))
    (tmp_path / g).mkdir()
    (tmp_path / g / f1).write_bytes(b"a")
    (tmp_path / g / f2).write_bytes(b"b")
    orphans = ops_db._orphans(tmp_path, [(g, f1, 1, "x")])
    assert [p.name for p in orphans] == [f2]
