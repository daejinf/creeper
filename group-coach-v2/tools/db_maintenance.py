"""Explicit read-only audit and isolated backup/restore. No model calls.

python -m tools.db_maintenance restore-test --expected-database wemeet_coach_v2
An expected name is mandatory; the original wemeet_goals database is prohibited.
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


def connect(url, database=None, autocommit=False):
    u = make_url(url)
    return psycopg.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=database or u.database,
        autocommit=autocommit,
        connect_timeout=5,
    )


def snapshot(c):
    result = {}
    for (table,) in c.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='wemeet_coach' ORDER BY tablename"
    ):
        rows = sorted(
            json.dumps(r[0], ensure_ascii=False, sort_keys=True, default=str)
            for r in c.execute(
                sql.SQL("SELECT row_to_json(t) FROM wemeet_coach.{} t").format(
                    sql.Identifier(table)
                )
            )
        )
        result[table] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
        }
    return result


def audit(c):
    queries = {
        "orphan_goals": "SELECT count(*) FROM wemeet_coach.goals g LEFT JOIN wemeet_coach.proposals p ON p.id=g.proposal_id AND p.group_id=g.group_id WHERE p.id IS NULL",
        "orphan_tasks": "SELECT count(*) FROM wemeet_coach.tasks t LEFT JOIN wemeet_coach.plans p ON p.id=t.plan_id AND p.group_id=t.group_id WHERE p.id IS NULL",
        "orphan_baselines": "SELECT count(*) FROM wemeet_coach.baselines b LEFT JOIN wemeet_coach.plans p ON p.id=b.plan_id AND p.group_id=b.group_id WHERE p.id IS NULL",
        "unlinked_members": "SELECT count(*) FROM wemeet_coach.members WHERE external_user_id IS NULL",
        "task_identity_mismatch": "SELECT count(*) FROM wemeet_coach.tasks WHERE owner IS DISTINCT FROM body->>'owner_member_id' OR id IS DISTINCT FROM body->>'task_id'",
        "personal_goal_mismatch": "SELECT count(*) FROM wemeet_coach.plans p JOIN wemeet_coach.personal_goals g ON g.id=p.personal_goal_id WHERE p.group_id<>g.group_id OR p.owner<>g.owner OR p.goal_id<>g.goal_id",
        "missing_group_baseline": "SELECT count(*) FROM wemeet_coach.plans p WHERE owner IS NULL AND NOT EXISTS(SELECT 1 FROM wemeet_coach.baselines b WHERE b.plan_id=p.id AND b.scope='GROUP')",
    }
    return {key: c.execute(query).fetchone()[0] for key, query in queries.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["audit", "restore-test", "install-test"])
    p.add_argument("--expected-database", required=True)
    p.add_argument("--output", default="runtime/maintenance")
    args = p.parse_args()
    value = os.environ.get("WEMEET_COACH_ADMIN_DATABASE_URL") or user_env(
        "WEMEET_COACH_ADMIN_DATABASE_URL"
    )
    u = make_url(value)
    if (
        not args.expected_database
        or u.database != args.expected_database
        or u.database in {"postgres", "template0", "template1", "wemeet_goals"}
    ):
        raise ValueError(
            "Explicit expected database must match; original and system databases are prohibited"
        )
    directory = (ROOT / args.output).resolve()
    if not directory.is_relative_to(ROOT / "runtime"):
        raise ValueError("Private maintenance output must stay inside ignored runtime/")
    directory.mkdir(parents=True, exist_ok=True)
    with connect(value) as c:
        c.execute("SET TRANSACTION READ ONLY")
        result = {
            "database": u.database,
            "checks": audit(c),
            "snapshot": snapshot(c),
            "AI_calls": 0,
        }
    if args.action != "audit":
        name = (
            "wemeet_coach_"
            + ("restore_" if args.action == "restore-test" else "install_")
            + uuid4().hex[:12]
        )
        binary = Path(
            os.getenv("WEMEET_COACH_PG_BIN", "C:/Program Files/PostgreSQL/16/bin")
        )
        env = {**os.environ, "PGPASSWORD": u.password or ""}
        common = ["-h", u.host, "-p", str(u.port or 5432), "-U", u.username]
        dump = directory / "backup.dump"
        if args.action == "restore-test":
            with connect(value) as c:
                c.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                source = snapshot(c)
                exported = c.execute("SELECT pg_export_snapshot()").fetchone()[0]
                subprocess.run(
                    [
                        str(binary / "pg_dump.exe"),
                        *common,
                        "-d",
                        u.database,
                        "-Fc",
                        "--snapshot",
                        exported,
                        "-f",
                        str(dump),
                    ],
                    env=env,
                    check=True,
                    capture_output=True,
                )
            result["dump_sha256"] = hashlib.sha256(dump.read_bytes()).hexdigest()
        with connect(value, "postgres", True) as c:
            if c.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone():
                raise RuntimeError("Unexpected test database exists")
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            if args.action == "restore-test":
                subprocess.run(
                    [
                        str(binary / "pg_restore.exe"),
                        *common,
                        "-d",
                        name,
                        "--no-owner",
                        "--exit-on-error",
                        str(dump),
                    ],
                    env=env,
                    check=True,
                    capture_output=True,
                )
                with connect(value, name) as c:
                    c.execute("SET TRANSACTION READ ONLY")
                    result["restore_snapshot_equal"] = snapshot(c) == source
                    result["restored_checks"] = audit(c)
            else:
                child = {
                    **os.environ,
                    "WEMEET_COACH_ADMIN_DATABASE_URL": u.set(
                        database=name
                    ).render_as_string(hide_password=False),
                    "WEMEET_COACH_EXPECTED_DATABASE": name,
                    "PYTHONPATH": str(ROOT / "src"),
                }
                subprocess.run(
                    [os.sys.executable, "-m", "alembic", "upgrade", "head"],
                    env=child,
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                )
                with connect(value, name) as c:
                    result["installed_revision"] = c.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchone()[0]
        finally:
            if (
                not name.startswith(("wemeet_coach_restore_", "wemeet_coach_install_"))
                or name == u.database
            ):
                raise RuntimeError("Unsafe cleanup target")
            with connect(value, "postgres", True) as c:
                c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        result["temporary_database_removed"] = True
    (directory / (args.action + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "snapshot"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
