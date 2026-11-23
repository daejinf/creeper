"""Stage 7 operations tool: database roles, encrypted backups, restore, restore verification, orphan file sweep.

Usage (every value comes from the environment or arguments; passwords and keys are never printed or logged):

  provision   --admin-url URL --database NAME --owner ROLE --app ROLE
              Passwords: MOIM_OWNER_PASSWORD, MOIM_APP_PASSWORD. Idempotent. Creates the owner role (runs migrations,
              owns every object), the app role (rows only: SELECT/INSERT/UPDATE/DELETE, sequences), the database owned
              by the owner, removes CREATE on the public schema from PUBLIC and sets default privileges so tables the
              owner creates later are granted to the app role automatically.
  grant       --url OWNER_URL --app ROLE      Re-apply the app role's privileges on existing objects (after a restore).
  backup      --url URL --upload-dir DIR --out FILE
              Key: MOIM_BACKUP_KEY (base64 of 32 random bytes; `keygen` prints a new one to stdout ONLY when asked).
              Writes one encrypted file: pg_dump (custom format) + every attachment + a manifest (schema version, row
              count per table, file list with sha256). AES-256-GCM in 4 MiB chunks, each chunk authenticated with its
              index and an end marker, so truncation, reordering and tampering are all detected.
  restore     --in FILE --url TARGET_URL --upload-dir EMPTY_DIR [--owner ROLE]
              Refuses a target database that already has tables, and an upload directory that is not empty.
  verify      --url URL --upload-dir DIR [--manifest-from FILE]
              Every attachment row has its file with the recorded sha256; optional comparison with a backup manifest.
  sweep       --url URL --upload-dir DIR [--delete]
              Lists files that no attachment row points at (orphans left by a failed delete). Dry run by default.
  replay-deletions --url APP_URL --upload-dir DIR --ledger-dir DIR [--dry-run]
              After a restore and BEFORE the app serves users (D-G1): runs every group leave, account deletion and completion undo in
              the deletion ledger again, oldest first, through the app's own lifecycle code. Entries already reflected
              in the database (older than the backup) are counted as `already`; running it twice is safe. Groups whose
              first assignment it completes get a coverage job that the app marks retryable on start.
  prune       --backup-dir DIR [--ledger-dir DIR] [--keep-days 7] [--delete]
              Rolling retention (D-G1): lists, and with --delete removes, backup files (recognised by their header)
              older than the retention, and ledger day files older than it. Dry run by default.
  keygen      Prints a new random backup key.

The tool never touches the test guard database rules: it is meant for an operator's shell, not for pytest.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

MAGIC = b"MOIMBK01"
CHUNK = 4 * 1024 * 1024
UUID_LEN = 36


class OpsError(RuntimeError):
    pass


# ---- helpers ------------------------------------------------------------------------------------------------------------


def _pg_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for base in [os.environ.get("PG_BIN", ""), r"C:\Program Files\PostgreSQL\16\bin", "/usr/lib/postgresql/16/bin"]:
        candidate = Path(base) / (name + (".exe" if os.name == "nt" else ""))
        if base and candidate.is_file():
            return str(candidate)
    raise OpsError(f"{name} not found (set PG_BIN)")


def _conninfo(url: str) -> dict:
    u = make_url(url)
    return {"host": u.host, "port": u.port or 5432, "user": u.username, "password": u.password, "dbname": u.database}


def _connect(url: str, **kw):
    return psycopg.connect(**_conninfo(url), connect_timeout=10, **kw)


def _pg_env(url: str) -> tuple[dict, list[str]]:
    """libpq environment (the password goes through PGPASSWORD, never the command line) and connection args."""
    c = _conninfo(url)
    env = {**os.environ, "PGPASSWORD": c["password"] or ""}
    return env, ["-h", c["host"], "-p", str(c["port"]), "-U", c["user"], "-d", c["dbname"]]


def _key() -> bytes:
    raw = os.environ.get("MOIM_BACKUP_KEY", "")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        key = b""
    if len(key) != 32:
        raise OpsError("MOIM_BACKUP_KEY must be base64 of exactly 32 bytes")
    return key


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _table_counts(conn) -> dict[str, int]:
    names = [r[0] for r in conn.execute("select tablename from pg_tables where schemaname='public' order by 1")]
    return {n: conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(n))).fetchone()[0] for n in names}


def _schema_version(conn) -> str | None:
    try:
        row = conn.execute("select version_num from alembic_version").fetchone()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return None
    return row[0] if row else None


# ---- encryption (AES-256-GCM, chunked) ---------------------------------------------------------------------------------


def encrypt_file(src: Path, dst: Path, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aead = AESGCM(key)
    prefix = os.urandom(8)
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        fout.write(MAGIC + prefix)
        index = 0
        block = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            last = not nxt
            nonce = prefix + struct.pack(">I", index)
            aad = MAGIC + struct.pack(">IB", index, 1 if last else 0)
            ct = aead.encrypt(nonce, block, aad)
            fout.write(struct.pack(">IB", len(ct), 1 if last else 0) + ct)
            if last:
                break
            block, index = nxt, index + 1


def decrypt_file(src: Path, dst: Path, key: bytes) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aead = AESGCM(key)
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        head = fin.read(len(MAGIC) + 8)
        if head[: len(MAGIC)] != MAGIC:
            raise OpsError("not a MOIM backup file")
        prefix = head[len(MAGIC):]
        index = 0
        while True:
            frame = fin.read(5)
            if len(frame) != 5:
                raise OpsError("backup file is truncated")
            length, last = struct.unpack(">IB", frame)
            ct = fin.read(length)
            if len(ct) != length:
                raise OpsError("backup file is truncated")
            try:
                fout.write(aead.decrypt(prefix + struct.pack(">I", index), ct, MAGIC + struct.pack(">IB", index, last)))
            except InvalidTag:
                raise OpsError("backup file failed authentication (wrong key or modified file)") from None
            if last:
                if fin.read(1):
                    raise OpsError("unexpected data after the last chunk")
                return
            index += 1


# ---- commands -------------------------------------------------------------------------------------------------------------


def provision(admin_url: str, database: str, owner: str, app: str) -> None:
    owner_pw, app_pw = os.environ.get("MOIM_OWNER_PASSWORD"), os.environ.get("MOIM_APP_PASSWORD")
    if not owner_pw or not app_pw:
        raise OpsError("MOIM_OWNER_PASSWORD and MOIM_APP_PASSWORD are required")
    with _connect(admin_url, autocommit=True) as conn:
        for role, pw in ((owner, owner_pw), (app, app_pw)):
            exists = conn.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone()
            verb = "alter" if exists else "create"
            conn.execute(sql.SQL(verb + " role {} with login nosuperuser nocreatedb nocreaterole noinherit nobypassrls "
                                 "noreplication password {}").format(sql.Identifier(role), sql.Literal(pw)))
        if not conn.execute("select 1 from pg_database where datname=%s", (database,)).fetchone():
            conn.execute(sql.SQL("create database {} owner {} encoding 'UTF8' template template0").format(
                sql.Identifier(database), sql.Identifier(owner)))
        conn.execute(sql.SQL("revoke all on database {} from public").format(sql.Identifier(database)))
        conn.execute(sql.SQL("grant connect, temporary on database {} to {}").format(sql.Identifier(database), sql.Identifier(app)))
    target = make_url(admin_url).set(database=database).render_as_string(hide_password=False)
    with _connect(target, autocommit=True) as conn:
        conn.execute(sql.SQL("alter schema public owner to {}").format(sql.Identifier(owner)))
        conn.execute("revoke create on schema public from public")
        _grant(conn, owner, app)
    print(f"provisioned database={database} owner={owner} app={app}")


def _grant(conn, owner: str, app: str) -> None:
    o, a = sql.Identifier(owner), sql.Identifier(app)
    conn.execute(sql.SQL("grant usage on schema public to {}").format(a))
    conn.execute(sql.SQL("grant select, insert, update, delete on all tables in schema public to {}").format(a))
    conn.execute(sql.SQL("grant usage, select on all sequences in schema public to {}").format(a))
    conn.execute(sql.SQL("alter default privileges for role {} in schema public grant select, insert, update, delete on tables to {}").format(o, a))
    conn.execute(sql.SQL("alter default privileges for role {} in schema public grant usage, select on sequences to {}").format(o, a))


def grant(url: str, app: str) -> None:
    with _connect(url, autocommit=True) as conn:
        owner = conn.execute("select current_user").fetchone()[0]
        _grant(conn, owner, app)
    print(f"granted app={app}")


def _file_rows(conn) -> list[tuple[str, str, int, str]]:
    try:
        return list(conn.execute("select group_id, id, size_bytes, sha256 from execution_files order by group_id, id"))
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return []


def backup(url: str, upload_dir: str, out: str) -> dict:
    key = _key()
    out_path = Path(out)
    if out_path.exists():
        raise OpsError("output file already exists")
    root = Path(upload_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="moim-backup-") as tmp:
        tmpd = Path(tmp)
        dump = tmpd / "db.dump"
        env, args = _pg_env(url)
        # Consistent snapshot: pg_dump runs in one repeatable-read transaction. Counts are read in a second
        # snapshot right after, so they are informational; the restore check compares them and reports differences.
        subprocess.run([_pg_bin("pg_dump"), *args, "-Fc", "--no-owner", "--no-privileges", "-f", str(dump)],
                       env=env, check=True, capture_output=True)
        with _connect(url) as conn:
            counts = _table_counts(conn)
            version = _schema_version(conn)
            files = _file_rows(conn)
        manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "schema_version": version,
                    "table_counts": counts, "db_dump_sha256": _sha256(dump), "files": [], "missing_files": 0}
        tar_path = tmpd / "bundle.tar"
        with tarfile.open(tar_path, "w") as tar:
            tar.add(dump, arcname="db.dump")
            for gid, fid, size, digest in files:
                p = root / gid / fid
                if not p.is_file():
                    manifest["missing_files"] += 1
                    continue
                tar.add(p, arcname=f"uploads/{gid}/{fid}")
                manifest["files"].append({"group_id": gid, "id": fid, "size": size, "sha256": digest})
            data = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        encrypt_file(tar_path, out_path, key)
    summary = {"out": str(out_path), "bytes": out_path.stat().st_size, "tables": len(counts),
               "rows": sum(counts.values()), "files": len(manifest["files"]), "missing_files": manifest["missing_files"],
               "schema_version": version}
    print(json.dumps(summary))
    return summary


def _open_bundle(src: str, tmpd: Path) -> tuple[Path, dict]:
    tar_path = tmpd / "bundle.tar"
    decrypt_file(Path(src), tar_path, _key())
    extract = tmpd / "x"
    extract.mkdir()
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            name = member.name
            parts = name.split("/")
            ok = name in ("db.dump", "manifest.json") or (
                len(parts) == 3 and parts[0] == "uploads" and len(parts[1]) == UUID_LEN and len(parts[2]) == UUID_LEN)
            if not ok or not member.isfile():
                raise OpsError("unexpected entry in backup bundle")
        tar.extractall(extract, filter="data")
    manifest = json.loads((extract / "manifest.json").read_text(encoding="utf-8"))
    if _sha256(extract / "db.dump") != manifest["db_dump_sha256"]:
        raise OpsError("database dump does not match the manifest")
    return extract, manifest


def restore(src: str, url: str, upload_dir: str, owner: str | None = None) -> dict:
    target_dir = Path(upload_dir)
    if target_dir.exists() and any(target_dir.iterdir()):
        raise OpsError("upload directory is not empty")
    with _connect(url) as conn:
        if conn.execute("select count(*) from pg_tables where schemaname='public'").fetchone()[0]:
            raise OpsError("target database already has tables; restore only into an empty database")
    with tempfile.TemporaryDirectory(prefix="moim-restore-") as tmp:
        extract, manifest = _open_bundle(src, Path(tmp))
        env, args = _pg_env(url)
        cmd = [_pg_bin("pg_restore"), *args, "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction"]
        if owner:
            cmd += ["--role", owner]
        subprocess.run([*cmd, str(extract / "db.dump")], env=env, check=True, capture_output=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in manifest["files"]:
            src_file = extract / "uploads" / f["group_id"] / f["id"]
            if _sha256(src_file) != f["sha256"]:
                raise OpsError("an attachment in the backup does not match its recorded hash")
            dst = target_dir / f["group_id"] / f["id"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_file, dst)
    result = {"restored_files": len(manifest["files"]), "schema_version": manifest["schema_version"]}
    print(json.dumps(result))
    return result


def verify(url: str, upload_dir: str, manifest_from: str | None = None) -> dict:
    root = Path(upload_dir).resolve()
    with _connect(url) as conn:
        counts = _table_counts(conn)
        version = _schema_version(conn)
        files = _file_rows(conn)
    missing = bad_hash = 0
    for gid, fid, size, digest in files:
        p = root / gid / fid
        if not p.is_file():
            missing += 1
        elif p.stat().st_size != size or _sha256(p) != digest:
            bad_hash += 1
    result = {"schema_version": version, "tables": len(counts), "rows": sum(counts.values()), "files": len(files),
              "missing_files": missing, "hash_mismatch": bad_hash, "orphans": len(_orphans(root, files))}
    if manifest_from:
        with tempfile.TemporaryDirectory(prefix="moim-verify-") as tmp:
            _, manifest = _open_bundle(manifest_from, Path(tmp))
        diff = {t: (manifest["table_counts"].get(t), counts.get(t))
                for t in set(manifest["table_counts"]) | set(counts) if manifest["table_counts"].get(t) != counts.get(t)}
        result["count_differences"] = diff
        result["schema_matches"] = manifest["schema_version"] == version
    print(json.dumps(result, ensure_ascii=False))
    return result


def _orphans(root: Path, files) -> list[Path]:
    known = {(g, f) for g, f, _, _ in files}
    out = []
    if not root.is_dir():
        return out
    for gdir in root.iterdir():
        if not gdir.is_dir():
            out.append(gdir)
            continue
        for p in gdir.iterdir():
            if (gdir.name, p.name) not in known:
                out.append(p)
    return out


def sweep(url: str, upload_dir: str, delete: bool) -> dict:
    root = Path(upload_dir).resolve()
    with _connect(url) as conn:
        files = _file_rows(conn)
    orphans = _orphans(root, files)
    removed = 0
    if delete:
        for p in orphans:
            if p.is_file():
                p.unlink()
                removed += 1
        for gdir in root.iterdir() if root.is_dir() else []:
            if gdir.is_dir() and not any(gdir.iterdir()):
                gdir.rmdir()
    result = {"orphans": len(orphans), "removed": removed, "dry_run": not delete}
    print(json.dumps(result))
    return result


def replay_deletions(url: str, upload_dir: str, ledger_dir: str, dry_run: bool) -> dict:
    """D-G1. Imports the app only here: the other commands do not need it installed."""
    from sqlalchemy import create_engine

    from moim.account.ledger import DeletionLedger
    from moim.account.lifecycle import LifecycleError, LifecycleService
    from moim.db import make_session_factory
    from moim.execution.files import Storage
    from moim.roles.assignment import AssignmentService

    if not Path(ledger_dir).is_dir():
        raise OpsError("ledger directory does not exist")
    ledger = DeletionLedger(ledger_dir)
    entries = ledger.entries()
    kinds = {k: sum(1 for e in entries if e["kind"] == k) for k in ("LEAVE", "ACCOUNT_DELETED", "RECORD_UNDONE")}
    kinds["PORTFOLIO_DELETED"] = sum(1 for e in entries if e["kind"].startswith("PORTFOLIO_"))  # stage 8A, D-I1 I4
    if dry_run:
        result = {"entries": len(entries), **kinds, "dry_run": True}
    else:
        engine = create_engine(url, hide_parameters=True, connect_args={"options": "-c timezone=UTC"})
        try:
            svc = LifecycleService(make_session_factory(engine), Storage(upload_dir), AssignmentService())
            try:
                done = svc.replay(entries)
            except LifecycleError as exc:  # stopped; entries before it are applied and become `already` next time
                raise OpsError(f"replay stopped: {exc.code}") from None
        finally:
            engine.dispose()
        result = {"entries": len(entries), **kinds, "applied": done["applied"], "already": done["already"],
                  "first_assignments": len(done["assigned"]), "dry_run": False}
    print(json.dumps(result))
    return result


def _is_backup(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def prune(backup_dir: str, ledger_dir: str | None, keep_days: int, delete: bool) -> dict:
    """D-G1 rolling retention. Only files that start with the backup header are considered; nothing else is touched."""
    if keep_days < 1:
        raise OpsError("keep-days must be at least 1")
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    root = Path(backup_dir)
    if not root.is_dir():
        raise OpsError("backup directory does not exist")
    old = [p for p in root.iterdir() if p.is_file() and p.stat().st_mtime < cutoff and _is_backup(p)]
    if delete:
        for p in old:
            p.unlink()
    result = {"old_backups": len(old), "dry_run": not delete}
    if ledger_dir:
        from moim.account.ledger import DeletionLedger

        ledger = DeletionLedger(ledger_dir, retention_days=keep_days)
        if delete:
            result["old_ledger_days"] = ledger.prune()
        else:
            result["ledger_entries"] = len(ledger.entries())
    print(json.dumps(result))
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ops_db")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("provision"); s.add_argument("--admin-url", required=True); s.add_argument("--database", required=True)
    s.add_argument("--owner", required=True); s.add_argument("--app", required=True)
    s = sub.add_parser("grant"); s.add_argument("--url", required=True); s.add_argument("--app", required=True)
    s = sub.add_parser("backup"); s.add_argument("--url", required=True); s.add_argument("--upload-dir", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("restore"); s.add_argument("--in", dest="src", required=True); s.add_argument("--url", required=True)
    s.add_argument("--upload-dir", required=True); s.add_argument("--owner")
    s = sub.add_parser("verify"); s.add_argument("--url", required=True); s.add_argument("--upload-dir", required=True)
    s.add_argument("--manifest-from")
    s = sub.add_parser("sweep"); s.add_argument("--url", required=True); s.add_argument("--upload-dir", required=True)
    s.add_argument("--delete", action="store_true")
    s = sub.add_parser("replay-deletions"); s.add_argument("--url", required=True)
    s.add_argument("--upload-dir", required=True); s.add_argument("--ledger-dir", required=True)
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("prune"); s.add_argument("--backup-dir", required=True); s.add_argument("--ledger-dir")
    s.add_argument("--keep-days", type=int, default=7); s.add_argument("--delete", action="store_true")
    sub.add_parser("keygen")
    a = p.parse_args(argv)
    try:
        if a.cmd == "provision":
            provision(a.admin_url, a.database, a.owner, a.app)
        elif a.cmd == "grant":
            grant(a.url, a.app)
        elif a.cmd == "backup":
            backup(a.url, a.upload_dir, a.out)
        elif a.cmd == "restore":
            restore(a.src, a.url, a.upload_dir, a.owner)
        elif a.cmd == "verify":
            r = verify(a.url, a.upload_dir, a.manifest_from)
            return 0 if not (r["missing_files"] or r["hash_mismatch"]) else 2
        elif a.cmd == "sweep":
            sweep(a.url, a.upload_dir, a.delete)
        elif a.cmd == "replay-deletions":
            replay_deletions(a.url, a.upload_dir, a.ledger_dir, a.dry_run)
        elif a.cmd == "prune":
            prune(a.backup_dir, a.ledger_dir, a.keep_days, a.delete)
        elif a.cmd == "keygen":
            print(base64.b64encode(os.urandom(32)).decode())
    except subprocess.CalledProcessError as exc:
        # pg tools' stderr can name objects but not data; show only the first line, never the command (no secrets in it).
        first = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[:1]
        print(f"error: {Path(exc.cmd[0]).name} failed: {first[0] if first else exc.returncode}", file=sys.stderr)
        return 1
    except OpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
