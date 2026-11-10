"""Explicit disposable review DB, never the user's application DB.

Creates and migrates only the name saved in runtime/improve5/database.json.
Credentials remain in process environment; reports contain no connection URLs.
"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from tools.config import ROOT, user_env

STATE = ROOT / "runtime/improve5/database.json"


def admin_url():
    value = os.environ.get("WEMEET_COACH_ADMIN_DATABASE_URL") or user_env(
        "WEMEET_COACH_ADMIN_DATABASE_URL"
    )
    if not value:
        raise ValueError("Explicit review administrator configuration required")
    return make_url(value)


def connection(url, database=None, autocommit=False):
    return psycopg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname=database or url.database,
        autocommit=autocommit,
    )


def configure():
    """Set process-local test target only. Does not alter the user's registry."""
    state = json.loads(STATE.read_text(encoding="utf8"))
    if not state["database"].startswith("wemeet_coach_review5_"):
        raise ValueError("Not a disposable review database")
    app = make_url(user_env("WEMEET_COACH_DATABASE_URL")).set(
        database=state["database"]
    )
    os.environ["WEMEET_COACH_TEST_DATABASE_URL"] = app.render_as_string(
        hide_password=False
    )
    os.environ["WEMEET_COACH_DATABASE_URL"] = os.environ[
        "WEMEET_COACH_TEST_DATABASE_URL"
    ]
    return state


def migrate(state):
    admin = admin_url().set(database=state["database"])
    env = {
        **os.environ,
        "WEMEET_COACH_ADMIN_DATABASE_URL": admin.render_as_string(hide_password=False),
        "WEMEET_COACH_EXPECTED_DATABASE": state["database"],
        "PYTHONPATH": str(ROOT / "src"),
    }
    result = subprocess.run(
        [os.sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        # Never print arbitrary driver exceptions containing credentials.
        raise RuntimeError(
            "Review database migration failed; inspect version and constraints with the administrator"
        )
    app = make_url(user_env("WEMEET_COACH_DATABASE_URL"))
    role = sql.Identifier(app.username)
    with connection(admin) as c:
        c.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(state["database"]), role
            )
        )
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA wemeet_coach TO {}").format(role))
        c.execute(
            sql.SQL(
                "GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA wemeet_coach TO {}"
            ).format(role)
        )
        c.execute(
            sql.SQL(
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA wemeet_coach TO {}"
            ).format(role)
        )
        for table, columns in [
            ("rooms", None),
            ("private_states", None),
            ("operations", None),
            ("proposals", "state"),
            ("votes", "agree,issue,reason"),
            ("members", "external_user_id"),
        ]:
            grant = (
                sql.SQL("UPDATE")
                if columns is None
                else sql.SQL("UPDATE(" + columns + ")")
            )
            c.execute(
                sql.SQL("GRANT {} ON wemeet_coach.{} TO {}").format(
                    grant, sql.Identifier(table), role
                )
            )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["create", "migrate", "check"])
    args = p.parse_args()
    if args.action == "create":
        if STATE.exists():
            raise ValueError("Existing review target must not be overwritten")
        state = {"database": "wemeet_coach_review5_" + uuid4().hex[:12]}
        with connection(admin_url(), "postgres", True) as c:
            c.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(state["database"]))
            )
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state), encoding="utf8")
    else:
        state = json.loads(STATE.read_text(encoding="utf8"))
    if not state["database"].startswith("wemeet_coach_review5_"):
        raise ValueError("Refusing non-review target")
    if args.action in {"create", "migrate"}:
        migrate(state)
    with connection(admin_url(), state["database"]) as c:
        version = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = c.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='wemeet_coach'"
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "isolated": True,
                "database": state["database"],
                "migration": version,
                "tables": tables,
                "AI_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
