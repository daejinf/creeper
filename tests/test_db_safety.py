"""The guard in tests/dbsafety.py must stop a destructive reset BEFORE anything is dropped or even connected."""
import pytest

import dbsafety
from dbsafety import UnsafeTestTarget, assert_safe_to_destroy, reset_test_schema

GOOD = "postgresql+psycopg://u:pw@127.0.0.1:54329/moim_test"
TEST_ENV = {"MOIM_ENV": "test"}


@pytest.fixture()
def connections(monkeypatch):
    """Any attempt to open a connection is recorded; a refused reset must record none."""
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, query):
            calls.append(query)

    monkeypatch.setattr(dbsafety.psycopg, "connect", lambda **kw: Conn())
    return calls


def test_all_conditions_met_runs_the_reset(connections):
    reset_test_schema(GOOD, TEST_ENV)
    assert connections == ["drop schema public cascade", "create schema public"]
    reset_test_schema(GOOD.replace("127.0.0.1", "localhost"), TEST_ENV)


@pytest.mark.parametrize("bad", [
    GOOD.replace("127.0.0.1", "db.example.com"),
    GOOD.replace("127.0.0.1", "10.0.0.5"),
    GOOD.replace("127.0.0.1", "0.0.0.0"),
    GOOD.replace("54329", "5432"),
    GOOD.replace("54329", "55432"),
    GOOD.replace("/moim_test", "/moim_dev"),
    GOOD.replace("/moim_test", "/moim_test2"),
    GOOD.replace("/moim_test", "/moim_test_backup"),
    GOOD.replace("/moim_test", "/Moim_Test"),
    GOOD.replace("/moim_test", "/postgres"),
])
def test_wrong_host_port_or_database_name_deletes_nothing(bad, connections):
    with pytest.raises(UnsafeTestTarget):
        reset_test_schema(bad, TEST_ENV)
    assert connections == []


@pytest.mark.parametrize("environ", [{}, {"MOIM_ENV": "development"}, {"MOIM_ENV": "production"}, {"MOIM_ENV": "Test"}])
def test_moim_env_other_than_test_deletes_nothing(environ, connections):
    with pytest.raises(UnsafeTestTarget, match="MOIM_ENV"):
        reset_test_schema(GOOD, environ)
    assert connections == []


def test_the_real_environment_passes_when_the_fixture_set_it(test_environ):
    assert_safe_to_destroy(test_environ)  # reads the real os.environ set by test_environ


def test_without_the_test_environment_the_real_target_is_refused(test_database_url, monkeypatch):
    monkeypatch.setenv("MOIM_ENV", "development")
    with pytest.raises(UnsafeTestTarget, match="MOIM_ENV"):
        assert_safe_to_destroy(test_database_url)
