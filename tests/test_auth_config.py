import os

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from wemeet_backend import config as cfg
from wemeet_backend import db

URL = "postgresql://u:secretpw@localhost:5432/x"


def test_missing_url_names_variable_only():
    with pytest.raises(cfg.ConfigError) as e:
        cfg.load_settings({})
    assert "WEMEET_AUTH_DATABASE_URL" in str(e.value)


def test_goal_url_is_not_used_as_fallback():
    with pytest.raises(cfg.ConfigError):
        cfg.load_settings({"WEMEET_GOAL_DATABASE_URL": URL})


def test_defaults_and_driver_normalization():
    s = cfg.load_settings({"WEMEET_AUTH_DATABASE_URL": URL})
    assert s.database_url.startswith("postgresql+psycopg://")
    assert (s.db_pool_mode, s.session_days, s.cookie_name, s.cookie_samesite) == ("null", 7, "wemeet_session", "lax")
    assert "secretpw" not in repr(s)


def test_bad_scheme_error_hides_url():
    with pytest.raises(cfg.ConfigError) as e:
        cfg.load_settings({"WEMEET_AUTH_DATABASE_URL": "mysql://u:secretpw@h/x"})
    assert "secretpw" not in str(e.value)


@pytest.mark.parametrize(
    "extra",
    [
        {"WEMEET_DB_POOL_MODE": "big"},
        {"WEMEET_AUTH_SESSION_DAYS": "abc"},
        {"WEMEET_AUTH_SESSION_DAYS": "0"},
        {"WEMEET_AUTH_COOKIE_SAMESITE": "strict"},
        {"WEMEET_AUTH_COOKIE_SAMESITE": "none"},  # needs Secure
        {"WEMEET_AUTH_COOKIE_SECURE": "maybe"},
        {"WEMEET_AUTH_COOKIE_NAME": "__Host-s"},  # needs Secure
        {"WEMEET_CORS_ORIGINS": "*"},
    ],
)
def test_invalid_values_rejected(extra):
    with pytest.raises(cfg.ConfigError):
        cfg.load_settings({"WEMEET_AUTH_DATABASE_URL": URL, **extra})


def test_deploy_style_settings():
    s = cfg.load_settings(
        {
            "WEMEET_AUTH_DATABASE_URL": URL,
            "WEMEET_AUTH_COOKIE_NAME": "__Host-wemeet_session",
            "WEMEET_AUTH_COOKIE_SECURE": "true",
            "WEMEET_DB_PREPARE_OFF": "1",
            "WEMEET_CORS_ORIGINS": "https://a.example.com/, https://b.example.com",
        }
    )
    assert s.cookie_secure and s.db_prepare_off
    assert s.cors_origins == ("https://a.example.com", "https://b.example.com")


def test_engine_null_pool_is_lazy():
    s = cfg.load_settings({"WEMEET_AUTH_DATABASE_URL": URL, "WEMEET_DB_PREPARE_OFF": "1"})
    assert isinstance(db.build_engine(s).pool, NullPool)  # no connection attempted


def test_get_engine_without_env_raises(monkeypatch):
    monkeypatch.delenv("WEMEET_AUTH_DATABASE_URL", raising=False)
    db.reset_engine()
    with pytest.raises(cfg.ConfigError):
        db.get_engine()


@pytest.mark.skipif(not os.getenv("WEMEET_AUTH_DATABASE_URL"), reason="WEMEET_AUTH_DATABASE_URL not set")
def test_select_1_live():
    db.reset_engine()
    try:
        with db.get_engine().connect() as c:
            assert c.execute(text("SELECT 1")).scalar() == 1
    finally:
        db.reset_engine()
