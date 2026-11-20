"""start-moim.cmd helper: create the database named in MOIM_DATABASE_URL if it does not exist yet.

The URL is read from the environment (never from argv) so the password does not appear in the process list.
Exit codes: 0 ok, 2 cannot connect.
"""
from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> int:
    url = make_url(os.environ["MOIM_DATABASE_URL"])
    name = url.database
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn:
            exists = conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
                print(f"    created database {name}")
    except psycopg.OperationalError as exc:
        print(f"    DB connection failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
