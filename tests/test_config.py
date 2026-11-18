import pytest

from moim.config import ConfigError, load_settings

GOOD = "postgresql+psycopg://moim:s3cretpw@127.0.0.1:54329/moim_dev"


def env(**kw):
    base = {"MOIM_ENV": "development", "MOIM_DATABASE_URL": GOOD}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


def test_loads_valid_settings():
    s = load_settings(env())
    assert s.env == "development"
    assert s.database_url == GOOD


def test_missing_env_fails():
    with pytest.raises(ConfigError, match="MOIM_ENV"):
        load_settings(env(MOIM_ENV=None))


def test_invalid_env_fails():
    with pytest.raises(ConfigError, match="MOIM_ENV"):
        load_settings(env(MOIM_ENV="staging2"))


def test_missing_database_url_fails():
    with pytest.raises(ConfigError, match="MOIM_DATABASE_URL is required"):
        load_settings(env(MOIM_DATABASE_URL=None))


def test_blank_database_url_fails():
    with pytest.raises(ConfigError, match="required"):
        load_settings(env(MOIM_DATABASE_URL="   "))


@pytest.mark.parametrize(
    "bad",
    [
        "not a url",
        "sqlite:///x.db",
        "mysql+pymysql://u:p@h/db",
        "postgresql://u:p@h/db",  # wrong driver (must be psycopg v3)
        "postgresql+psycopg://u:p@/db",  # no host
        "postgresql+psycopg://u:p@h",  # no database
    ],
)
def test_invalid_database_url_fails(bad):
    with pytest.raises(ConfigError):
        load_settings(env(MOIM_DATABASE_URL=bad))


def test_error_messages_never_contain_password():
    with pytest.raises(ConfigError) as exc:
        load_settings(env(MOIM_DATABASE_URL="postgresql://moim:s3cretpw@h/db"))
    assert "s3cretpw" not in str(exc.value)


def test_test_env_requires_test_database():
    with pytest.raises(ConfigError, match="_test"):
        load_settings(env(MOIM_ENV="test"))


def test_test_database_rejected_outside_test_env():
    with pytest.raises(ConfigError, match="_test"):
        load_settings(env(MOIM_DATABASE_URL=GOOD.replace("moim_dev", "moim_test")))


def test_settings_repr_and_safe_url_hide_password():
    s = load_settings(env())
    assert "s3cretpw" not in repr(s)
    assert "s3cretpw" not in s.safe_database_url
    assert "s3cretpw" not in str(s)
