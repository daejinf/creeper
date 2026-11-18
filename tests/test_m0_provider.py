"""0B-2 deterministic contract for the M0 provider path.

No network, no API key. A recording fake HTTP session stands in for Timely.
Contract: one user request = one HTTP call, no automatic retry or JSON repair call,
every real call writes exactly one safe log line, and no raw prompt, response,
parsed output or key ever reaches a log or trace event.
"""
import json
import logging

import pytest
import requests
from pydantic import BaseModel

from wemeet_ai.m0.client import AIError, TimelyClient
from wemeet_ai.m0.runtime import Runtime
from wemeet_ai.m0.tracing import MemoryTraceSink

KEY = "tgpt_s_SYNTHETIC_KEY_0123456789"
PROMPT_SECRET = "PRIVATE-PROMPT-MARKER-7731"
REPLY_SECRET = "RAW-AI-REPLY-MARKER-5502"
ERROR_BODY_SECRET = "RAW-ERROR-BODY-MARKER-9184"
LOGGER = "moim.provider"


class Shape(BaseModel):
    value: int


class Reply(BaseModel):
    reply: str


class Request(BaseModel):
    note: str


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def ok(content, usage=(11, 7, 18)):
    return FakeResponse(200, {
        "id": "req-1", "model": "fake-model",
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": usage[2]},
    })


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.posts = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "json": json})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _no_sleep(_seconds):
    raise AssertionError("automatic backoff sleep must not happen")


@pytest.fixture(autouse=True)
def timely_env(monkeypatch):
    monkeypatch.setenv("TIMELY_API_KEY", KEY)
    monkeypatch.delenv("WEMEET_OFFLINE", raising=False)
    monkeypatch.setattr("time.sleep", _no_sleep)


def client(*outcomes):
    session = FakeSession(*outcomes)
    return TimelyClient(session=session), session


def run_runtime(session_outcomes, *, text=False, check=None, debug=True):
    c, session = client(*session_outcomes)
    sink = MemoryTraceSink()
    runtime = Runtime(c, trace_sink=sink, debug=debug)
    result = runtime.run("E.coach", Request(note=PROMPT_SECRET), Reply if text else Shape, "instructions",
                         text=text, check=check)
    return result, session, sink


def call_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


# ---- no automatic re-calls ------------------------------------------------------------------

FAILURES = {
    "timeout": (requests.Timeout(), "TIMEOUT"),
    "connection": (requests.ConnectionError(), "NETWORK_ERROR"),
    "http_429": (FakeResponse(429, {"error": ERROR_BODY_SECRET}), "RATE_LIMITED"),
    "http_503": (FakeResponse(503, {"error": ERROR_BODY_SECRET}), "UPSTREAM_ERROR"),
}


@pytest.mark.parametrize("name", FAILURES)
def test_transient_failure_returns_error_without_second_call(name):
    outcome, code = FAILURES[name]
    c, session = client(outcome)
    with pytest.raises(AIError) as exc:
        c.complete_text([{"role": "user", "content": "hi"}])
    assert exc.value.code == code
    assert len(session.posts) == 1


def test_invalid_json_is_failure_without_repair_call():
    c, session = client(ok("not json at all"))
    with pytest.raises(AIError) as exc:
        c.complete_json([{"role": "user", "content": "hi"}], Shape)
    assert exc.value.code == "INVALID_JSON"
    assert len(session.posts) == 1


def test_schema_error_is_failure_without_repair_call():
    c, session = client(ok(json.dumps({"value": "not-int"})))
    with pytest.raises(AIError) as exc:
        c.complete_json([{"role": "user", "content": "hi"}], Shape)
    assert exc.value.code == "SCHEMA_INVALID"
    assert len(session.posts) == 1


def test_runtime_check_failure_does_not_recall_model():
    def check(_data):
        raise AIError("PLAN_INVALID", "bad plan")

    result, session, _ = run_runtime([ok(json.dumps({"value": 1}))], check=check)
    assert result.status == "FAILED" and result.error.code == "PLAN_INVALID"
    assert len(session.posts) == 1
    assert result.meta.transport_attempts == 1 and result.meta.repair_attempts == 0


@pytest.mark.parametrize("name", FAILURES)
def test_runtime_failure_is_single_call_and_marked_for_user_retry(name):
    outcome, code = FAILURES[name]
    result, session, _ = run_runtime([outcome])
    assert result.status == "FAILED" and result.error.code == code
    assert result.error.retryable is True
    assert len(session.posts) == 1


def test_one_user_message_makes_exactly_one_http_call():
    result, session, _ = run_runtime([ok(REPLY_SECRET)], text=True)
    assert result.status == "OK"
    assert len(session.posts) == 1
    assert result.meta.transport_attempts == 1


def test_defaults_use_direct_api_and_unprefixed_model(monkeypatch):
    monkeypatch.delenv("TIMELY_MODEL", raising=False)
    monkeypatch.delenv("TIMELY_BASE_URL", raising=False)
    c, session = client(ok("hello"))
    c.complete_text([{"role": "user", "content": "hi"}])
    assert session.posts[0]["url"] == "https://hello.timelyai.io/api/v2/chat/direct/openai/v1/chat/completions"
    assert session.posts[0]["json"]["model"] == "gpt-5.6-luna"
    assert "max_tokens" not in session.posts[0]["json"]
    assert session.posts[0]["json"]["max_completion_tokens"] == 1500


def test_user_retry_is_a_new_call():
    c, session = client(FakeResponse(503, {"e": 1}), ok(json.dumps({"value": 2})))
    with pytest.raises(AIError):
        c.complete_json([{"role": "user", "content": "x"}], Shape)
    assert c.complete_json([{"role": "user", "content": "x"}], Shape).parsed_output.value == 2
    assert len(session.posts) == 2


def test_no_second_provider_not_used_on_failure():
    # a Timely failure surfaces as a failure; nothing substitutes a canned success
    result, _, _ = run_runtime([FakeResponse(503, {"e": 1})])
    assert result.status == "FAILED" and result.data is None


# ---- no raw content in logs or traces -------------------------------------------------------

SCENARIOS = {
    "success_json": [ok(json.dumps({"value": 41, "x": REPLY_SECRET}))],
    "success_text": [ok(REPLY_SECRET)],
    "http_error_with_body": [FakeResponse(400, {"error": ERROR_BODY_SECRET})],
    "http_503": [FakeResponse(503, {"error": ERROR_BODY_SECRET})],
    "invalid_json": [ok("not json " + REPLY_SECRET)],
    "schema_error": [ok(json.dumps({"value": REPLY_SECRET}))],
    "unparseable_body": [FakeResponse(200, None, text=ERROR_BODY_SECRET)],
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_no_raw_content_or_key_in_logs_and_traces(name, caplog):
    text = name == "success_text"
    with caplog.at_level(logging.DEBUG):
        run_runtime(SCENARIOS[name], text=text)
    haystack = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (KEY, PROMPT_SECRET, REPLY_SECRET, ERROR_BODY_SECRET, "Bearer"):
        assert secret not in haystack, f"{secret!r} leaked into logs in {name}"


@pytest.mark.parametrize("name", SCENARIOS)
def test_no_raw_content_or_key_in_trace_events(name):
    _, _, sink = run_runtime(SCENARIOS[name], text=(name == "success_text"))
    dump = json.dumps(sink.events, ensure_ascii=False, default=str)
    for secret in (KEY, PROMPT_SECRET, REPLY_SECRET, ERROR_BODY_SECRET):
        assert secret not in dump, f"{secret!r} leaked into trace in {name}"


def test_provider_events_carry_only_safe_metadata():
    _, _, sink = run_runtime([ok(json.dumps({"value": 3}))])
    events = {e["event"]: e["sanitized_payload"] for e in sink.events}
    assert "body" not in events.get("provider.request", {})
    assert "messages" not in events.get("provider.request", {})
    assert "body" not in events.get("provider.response", {})
    request = events["provider.request"]
    assert request["model"] and request["call_id"] and request["body_bytes"] > 0
    assert len(request["body_sha256"]) == 64
    assert "json.parsed" not in events or "value" not in events["json.parsed"]


# ---- one log line per real call -------------------------------------------------------------

def test_each_http_call_writes_exactly_one_log_line(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        run_runtime([ok(json.dumps({"value": 1}))])
    lines = call_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    for part in ("stage=E.coach", "model=fake-model", "ok=true", "latency_ms=", "prompt_tokens=11",
                 "completion_tokens=7", "total_tokens=18"):
        assert part in line, line


@pytest.mark.parametrize("name", FAILURES)
def test_failed_call_is_logged_and_counted(name, caplog):
    outcome, code = FAILURES[name]
    with caplog.at_level(logging.INFO, logger=LOGGER):
        run_runtime([outcome])
    lines = call_lines(caplog)
    assert len(lines) == 1
    assert "ok=false" in lines[0] and f"error={code}" in lines[0] and "latency_ms=" in lines[0]


def test_no_key_means_no_http_call_and_no_call_line(monkeypatch, caplog):
    monkeypatch.delenv("TIMELY_API_KEY")
    c, session = client(ok("x"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        with pytest.raises(AIError) as exc:
            c.complete_text([{"role": "user", "content": "hi"}])
    assert exc.value.code == "AUTH_FAILED"
    assert session.posts == [] and call_lines(caplog) == []


def test_default_run_without_debug_still_logs_call_line_and_no_raw(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result, _, sink = run_runtime([ok(json.dumps({"value": 1}))], debug=False)
    assert len(call_lines(caplog)) == 1
    assert PROMPT_SECRET not in json.dumps(sink.events, default=str)
