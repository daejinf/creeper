"""Runtime contract after 0B-root-hardening: one call, no cache, no reload gate, no hidden token override."""
import inspect
import json

import pytest
from pydantic import BaseModel

from wemeet_ai.m0 import runtime as runtime_module
from wemeet_ai.m0.client import DEFAULT_MAX_OUTPUT_TOKENS, TimelyClient
from wemeet_ai.m0.runtime import Runtime
from test_m0_provider import FakeSession, ok


class Out(BaseModel):
    value: int


class Reply(BaseModel):
    reply: str


class In(BaseModel):
    note: str


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("TIMELY_API_KEY", "tgpt_s_SYNTHETIC_KEY_0123456789")
    monkeypatch.delenv("WEMEET_OFFLINE", raising=False)


def run(runtime, **kw):
    return runtime.run("E.coach", In(note="x"), Out, "instructions", **kw)


def test_runtime_signature_has_no_cache_reload_or_fresh_parameters():
    init = set(inspect.signature(Runtime.__init__).parameters)
    call = set(inspect.signature(Runtime.run).parameters)
    assert not ({"cache", "scope_id"} & init)
    assert "fresh" not in call
    assert not hasattr(runtime_module, "reload_required") and not hasattr(runtime_module, "VALIDATOR_VERSION")


def test_same_request_twice_is_two_calls_never_a_cache_hit():
    session = FakeSession(ok(json.dumps({"value": 1})))
    runtime = Runtime(TimelyClient(session=session))
    first, second = run(runtime), run(runtime)
    assert first.status == second.status == "OK"
    assert len(session.posts) == 2
    assert "cache_hit" not in first.meta.model_dump() and first.meta.execution_source == "LIVE"


def test_a_run_is_never_blocked_by_source_changes():
    # the old gate returned SOURCE_RELOAD_REQUIRED when files on disk changed; nothing like it exists now
    session = FakeSession(ok(json.dumps({"value": 1})))
    assert run(Runtime(TimelyClient(session=session))).error is None


def test_default_token_limit_is_the_single_client_constant():
    session = FakeSession(ok(json.dumps({"value": 1})))
    run(Runtime(TimelyClient(session=session)))
    assert session.posts[0]["json"]["max_completion_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_text_path_uses_the_same_limit():
    session = FakeSession(ok("hello"))
    Runtime(TimelyClient(session=session)).run("E.coach", In(note="x"), Reply, "i", text=True)
    assert session.posts[0]["json"]["max_completion_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_runtime_and_per_call_limits_are_passed_through():
    session = FakeSession(ok(json.dumps({"value": 1})))
    runtime = Runtime(TimelyClient(session=session), max_output_tokens=2000)
    run(runtime)
    run(runtime, max_output_tokens=900)
    assert [p["json"]["max_completion_tokens"] for p in session.posts] == [2000, 900]
    assert all("max_tokens" not in p["json"] for p in session.posts)
