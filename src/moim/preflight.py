"""Stage 7: production start-up checks. The server refuses to start in production when a check fails.

Development and test are unchanged (local HTTP, the single local `moim` role). In production:
  * the database role must not be a superuser, must not be able to create roles or databases, and must not own the
    application tables (migrations run as a separate owner role; the app role only reads and writes rows);
  * the database schema must be at the newest migration head (the app never migrates by itself);
  * MOIM_UPLOAD_DIR must be an explicit absolute path outside the installed package;
  * TIMELY_API_KEY must be present (its value is never read here beyond "is it set");
  * MOIM_DELETION_LEDGER_DIR (D-G1) must be an absolute, existing, writable directory that is neither inside the upload
    directory nor contains it (a restore empties the upload directory and must never roll the ledger back).
Messages name the failed check only, never a value.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .config import ConfigError, Settings

PACKAGE_DIR = Path(__file__).resolve().parent


def _migration_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:  # pragma: no cover - alembic is a dependency
        return None
    root = Path(__file__).resolve().parents[2]
    ini = root / "alembic.ini"
    if not ini.is_file():
        return None  # installed without the migration scripts: the version check below is skipped
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def check_production(settings: Settings, engine: Engine, environ: Mapping[str, str] | None = None) -> list[str]:
    """The names of the failed checks (empty list = ready). Only production is checked."""
    if settings.env != "production":
        return []
    environ = os.environ if environ is None else environ
    failed: list[str] = []
    with engine.connect() as conn:
        row = conn.execute(text(
            "select rolsuper, rolcreaterole, rolcreatedb, rolbypassrls from pg_roles where rolname = current_user"
        )).one()
        if row.rolsuper:
            failed.append("DB_ROLE_IS_SUPERUSER")
        if row.rolcreaterole or row.rolcreatedb:
            failed.append("DB_ROLE_CAN_CREATE_ROLES_OR_DATABASES")
        if row.rolbypassrls:
            failed.append("DB_ROLE_BYPASSES_RLS")
        owned = conn.execute(text(
            "select count(*) from pg_tables where schemaname = 'public' and tableowner = current_user"
        )).scalar_one()
        if owned:
            failed.append("DB_ROLE_OWNS_TABLES")
        head = _migration_head()
        if head is not None:
            try:
                version = conn.execute(text("select version_num from alembic_version")).scalar_one_or_none()
            except Exception:  # noqa: BLE001 - missing table means "not migrated"
                version = None
            if version != head:
                failed.append("DB_SCHEMA_NOT_AT_HEAD")
    upload = (environ.get("MOIM_UPLOAD_DIR") or "").strip()
    upload_ok = bool(upload) and Path(upload).is_absolute()
    if not upload_ok:
        failed.append("UPLOAD_DIR_NOT_ABSOLUTE")
    elif Path(upload).resolve().is_relative_to(PACKAGE_DIR):
        failed.append("UPLOAD_DIR_INSIDE_PACKAGE")
    ledger = (environ.get("MOIM_DELETION_LEDGER_DIR") or "").strip()
    if not ledger or not Path(ledger).is_absolute():
        failed.append("DELETION_LEDGER_DIR_NOT_ABSOLUTE")
    else:
        lp = Path(ledger).resolve()
        if not lp.is_dir() or not os.access(lp, os.W_OK):
            failed.append("DELETION_LEDGER_DIR_NOT_WRITABLE")
        if upload_ok:
            up = Path(upload).resolve()
            if lp.is_relative_to(up) or up.is_relative_to(lp):
                failed.append("DELETION_LEDGER_DIR_INSIDE_UPLOAD_DIR")
    if not (environ.get("TIMELY_API_KEY") or "").strip():
        failed.append("TIMELY_API_KEY_MISSING")
    if (environ.get("WEMEET_OFFLINE") or "") == "1":
        failed.append("AI_OFFLINE_SWITCH_ON")
    return failed


def enforce_production(settings: Settings, engine: Engine, environ: Mapping[str, str] | None = None) -> None:
    failed = check_production(settings, engine, environ)
    if failed:
        raise ConfigError("production preflight failed: " + ", ".join(failed))
