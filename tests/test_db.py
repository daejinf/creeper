"""Migration up/down/up, app import/health, and test-DB isolation (needs the _test database)."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]


def alembic_cfg() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def tables(url) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


@pytest.fixture()
def clean_db(test_environ, reset_schema):
    reset_schema()  # guarded drop of the test schema
    yield test_environ


def test_migration_up_down_up(clean_db):
    cfg = alembic_cfg()
    command.upgrade(cfg, "head")
    assert {"app_meta", "ai_jobs"} <= tables(clean_db)
    command.downgrade(cfg, "base")
    assert not ({"app_meta", "ai_jobs"} & tables(clean_db))
    command.upgrade(cfg, "head")
    assert {"app_meta", "ai_jobs"} <= tables(clean_db)


def test_migration_refuses_without_settings(monkeypatch):
    monkeypatch.delenv("MOIM_DATABASE_URL", raising=False)
    monkeypatch.setenv("MOIM_ENV", "test")
    from moim.config import ConfigError

    with pytest.raises(ConfigError):
        command.upgrade(alembic_cfg(), "head")


def test_migration_refuses_dev_db_name_in_test_env(monkeypatch, test_database_url):
    monkeypatch.setenv("MOIM_ENV", "test")
    monkeypatch.setenv("MOIM_DATABASE_URL", test_database_url.replace("_test", "_dev"))
    from moim.config import ConfigError

    with pytest.raises(ConfigError, match="_test"):
        command.upgrade(alembic_cfg(), "head")


def test_app_imports_and_health_uses_test_db(clean_db):
    command.upgrade(alembic_cfg(), "head")  # the app expects a migrated database (job state table)
    from fastapi.testclient import TestClient

    from moim.main import create_app

    app = create_app()
    assert app.state.settings.database_url.rsplit("/", 1)[1].endswith("_test")
    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok", "env": "test"}


def test_connection_timezone_is_utc(clean_db):
    from moim.config import load_settings
    from moim.db import make_engine

    eng = make_engine(load_settings())
    with eng.connect() as c:
        assert c.execute(text("show timezone")).scalar() == "UTC"
    eng.dispose()


def test_bad_db_url_fails_health_cleanly(monkeypatch):
    from moim.config import ConfigError, load_settings

    with pytest.raises(ConfigError):
        load_settings({"MOIM_ENV": "development", "MOIM_DATABASE_URL": "postgres//broken"})
