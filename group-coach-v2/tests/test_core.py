import json
import pytest, requests
from pydantic import BaseModel
from wemeet_coach.m0.client import TimelyClient, AIError, RunStats
from wemeet_coach.shared.privacy import sanitize
from wemeet_coach.shared.contracts import (
    PersonalRequest,
    PersonalState,
    Question,
    Option,
    Message,
)
from wemeet_coach.personal import apply_answer
from conftest import members, proposal


class Shape(BaseModel):
    answer: str


class Response:
    def __init__(self, status=200, content='{"answer":"yes"}', finish="stop"):
        self.status_code = status
        self.data = {
            "id": "test-request",
            "model": "test-model",
            "usage": {"total_tokens": 10},
            "choices": [{"finish_reason": finish, "message": {"content": content}}],
        }

    def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, *a, **k):
        self.calls.append(k)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("TIMELY_API_KEY", "tgpt_TEST_ONLY_SYNTHETIC_123456789")


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_no_retry_for_auth_and_invalid_request(status):
    s = Session([Response(status)])
    with pytest.raises(AIError):
        TimelyClient(s, lambda _: None).complete_text([])
    assert len(s.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [Response(429), Response(500), requests.Timeout(), requests.ConnectionError()],
)
def test_transient_retry(bad):
    s = Session([bad, Response()])
    r = TimelyClient(s, lambda _: None).complete_text([])
    assert r.transport_attempts == 2 and s.calls[0]["timeout"] == (10, 60)


def test_missing_key_never_calls_or_falls_back(monkeypatch):
    monkeypatch.delenv("TIMELY_API_KEY")
    s = Session([])
    with pytest.raises(AIError):
        TimelyClient(s).complete_text([])
    assert not s.calls


def test_json_repair_and_four_transport_limit():
    s = Session([Response(content="{"), Response()])
    stats = RunStats()
    r = TimelyClient(s).complete_json([], Shape, stats=stats)
    assert (
        r.parsed_output.answer == "yes"
        and stats.repair_attempts == 1
        and stats.transport_attempts == 2
    )
    s = Session(
        [Response(500), Response(content="{"), Response(500), Response(content="{")]
    )
    with pytest.raises(AIError):
        TimelyClient(s, lambda _: None).complete_json([], Shape)
    assert len(s.calls) == 4


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(content=""), "EMPTY_RESPONSE"),
        (Response(finish="length"), "TRUNCATED_OUTPUT"),
    ],
)
def test_empty_and_truncated(response, code):
    with pytest.raises(AIError) as e:
        TimelyClient(Session([response])).complete_text([])
    assert e.value.code == code


def test_privacy_hashes_and_keys():
    value = "4c42131943404949dc52381c1f65c0102507321fd3d9b6a0e8dc3372959ab854"
    assert sanitize(value) == value
    assert "010-1234-5678" not in sanitize("연락처 010-1234-5678")
    assert sanitize(
        {"Authorization": "secret", "text": "tgpt_TEST_ONLY_SYNTHETIC_123456789"}
    ) == {"text": "[REDACTED_KEY]"}


@pytest.mark.parametrize("invalid", [True, 1.5, 1.0, None])
def test_invalid_usage_is_not_silently_coerced(monkeypatch, invalid):
    monkeypatch.setenv("TIMELY_API_KEY", "test-credential")
    response = Response()
    original = response.json
    response.json = lambda: {
        **original(),
        "usage": {"prompt_tokens": invalid, "completion_tokens": 1, "total_tokens": 2},
    }
    session = Session([response])
    with pytest.raises(AIError) as e:
        TimelyClient(session).complete_text([])
    assert e.value.code == "INVALID_JSON" and len(session.calls) == 1


@pytest.mark.parametrize(
    "action,value,status",
    [("VALUE", 120, "KNOWN"), ("UNKNOWN", None, "UNKNOWN"), ("SKIP", None, "SKIPPED")],
)
def test_button_states_are_distinct(action, value, status):
    field = "weekly_minutes" if action != "SKIP" else "constraints"
    q = Question(
        question_id="q",
        field=field,
        text="확인할까요",
        reason="계획 반영",
        options=[Option(option_id="o", label="답변", value=value, action=action)],
    )
    request = PersonalRequest(
        goal=proposal(members(1)).goal,
        owner_member_id="u0",
        state=PersonalState(question=q),
        conversation=[Message(message_id="m", speaker_id="u0", text="답변")],
        answer={"question_id": "q", "option_id": "o", "message_id": "m"},
    )
    out = apply_answer(request)
    assert out.fields[field].status == status and out.fields[field].value == value
    request.state = out
    with pytest.raises(ValueError):
        apply_answer(request)
