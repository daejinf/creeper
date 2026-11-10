"""Explicit new DB provisioning. Existing DB is opened read-only for a baseline."""

import os, json, secrets, argparse, subprocess, hashlib
from pathlib import Path
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from cryptography.fernet import Fernet
from tools.config import ROOT, user_env, save_user


def original_snapshot(url):
    u = make_url(url)
    result = {}
    with psycopg.connect(
        host=u.host,
        port=u.port,
        user=u.username,
        password=u.password,
        dbname=u.database,
    ) as c:
        c.execute("SET TRANSACTION READ ONLY")
        for (table,) in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='wemeet_goal'"
        ):
            values = sorted(
                json.dumps(row[0], ensure_ascii=False, sort_keys=True, default=str)
                for row in c.execute(
                    sql.SQL("SELECT row_to_json(r) FROM wemeet_goal.{} r").format(
                        sql.Identifier(table)
                    )
                )
            )
            result[table] = {
                "count": len(values),
                "sha256": hashlib.sha256("\n".join(values).encode()).hexdigest(),
            }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-env", required=True)
    args = parser.parse_args()
    admin = user_env(args.admin_env) or os.environ.get(args.admin_env)
    if not admin:
        raise RuntimeError("Explicit administrator credential required")
    u = make_url(admin)
    root = ROOT.parent
    original = root / "src/wemeet_ai/__init__.py"
    if (
        u.database == "wemeet_goals"
        and not (ROOT / "runtime/original-db-before.json").exists()
    ):
        (ROOT / "runtime/original-db-before.json").write_text(
            json.dumps(original_snapshot(admin), indent=2)
        )
    old = user_env("WEMEET_COACH_DATABASE_URL")
    password = make_url(old).password if old else secrets.token_urlsafe(36)
    with psycopg.connect(
        host=u.host,
        port=u.port,
        user=u.username,
        password=u.password,
        dbname="postgres",
        autocommit=True,
    ) as c:
        if not c.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='wemeet_coach_app'"
        ).fetchone():
            c.execute(
                sql.SQL(
                    "CREATE ROLE wemeet_coach_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}"
                ).format(sql.Literal(password))
            )
        if not c.execute(
            "SELECT 1 FROM pg_database WHERE datname='wemeet_coach_v2'"
        ).fetchone():
            c.execute("CREATE DATABASE wemeet_coach_v2")
    admin_new = u.set(database="wemeet_coach_v2").render_as_string(hide_password=False)
    env = {
        **os.environ,
        "WEMEET_COACH_ADMIN_DATABASE_URL": admin_new,
        "WEMEET_COACH_EXPECTED_DATABASE": "wemeet_coach_v2",
        "PYTHONPATH": str(ROOT / "src"),
    }
    subprocess.run(
        [os.sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    with psycopg.connect(
        host=u.host,
        port=u.port,
        user=u.username,
        password=u.password,
        dbname="wemeet_coach_v2",
    ) as c:
        c.execute("GRANT CONNECT ON DATABASE wemeet_coach_v2 TO wemeet_coach_app")
        c.execute("GRANT USAGE ON SCHEMA wemeet_coach TO wemeet_coach_app")
        c.execute(
            "GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA wemeet_coach TO wemeet_coach_app"
        )
        c.execute(
            "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA wemeet_coach TO wemeet_coach_app"
        )
        c.execute(
            "GRANT UPDATE ON wemeet_coach.rooms,wemeet_coach.private_states,wemeet_coach.operations TO wemeet_coach_app"
        )
        c.execute("GRANT UPDATE(state) ON wemeet_coach.proposals TO wemeet_coach_app")
        c.execute(
            "GRANT UPDATE(agree,issue,reason) ON wemeet_coach.votes TO wemeet_coach_app"
        )
    save_user("WEMEET_COACH_ADMIN_DATABASE_URL", admin_new)
    save_user(
        "WEMEET_COACH_DATABASE_URL",
        u.set(
            database="wemeet_coach_v2", username="wemeet_coach_app", password=password
        ).render_as_string(hide_password=False),
    )
    if not user_env("WEMEET_COACH_RECOVERY_KEY"):
        save_user("WEMEET_COACH_RECOVERY_KEY", Fernet.generate_key().decode())
    print(
        "Separate database and restricted role ready. Original database unchanged; credentials not printed."
    )


if __name__ == "__main__":
    main()
