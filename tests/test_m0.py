import json
from concurrent.futures import ThreadPoolExecutor
import requests
import pytest
from pydantic import BaseModel
from wemeet_ai.m0 import TimelyClient, AIError, Runtime, MemoryResultCache, MemoryTraceSink
from wemeet_ai.m0.client import RunStats
from wemeet_ai.m1.a_coaching import CoachRequest, coach_reply
from _data import sample

class Shape(BaseModel):
    answer: str

class Response:
    def __init__(self, status=200, content='{"answer":"yes"}', *, finish="stop", usage=None):
        self.status_code = status
        self.data = {"id": "request-fixture", "model": "test-model", "choices": [{"finish_reason": finish, "message": {"content": content}}], "usage": usage if usage is not None else {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}
    def json(self): return self.data

class Session:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result

@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("TIMELY_API_KEY", "sk-SYNTHETIC_ONLY_NOT_REAL_123456")
    monkeypatch.setenv("WEMEET_OFFLINE", "0")

@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_nonretry_errors(status):
    session = Session([Response(status)])
    with pytest.raises(AIError): TimelyClient(session, lambda _:None).complete_text([])
    assert len(session.calls) == 1

@pytest.mark.parametrize("failure", [Response(429), Response(500), Response(503), requests.Timeout(), requests.ConnectionError()])
def test_transient_retry(failure):
    session = Session([failure, Response()])
    result = TimelyClient(session, lambda _:None).complete_text([])
    assert result.transport_attempts == 2
    assert session.calls[0][1]["timeout"] == (10,60)

@pytest.mark.parametrize("response, code", [(Response(content=""), "EMPTY_RESPONSE"), (Response(finish="length"), "TRUNCATED_OUTPUT"), (Response(usage={"total_tokens": -1}), "INVALID_JSON"), (Response(usage={"prompt_tokens": "bad"}), "INVALID_JSON")])
def test_response_errors(response, code):
    with pytest.raises(AIError) as error: TimelyClient(Session([response])).complete_text([])
    assert error.value.code == code

def test_no_key_no_request(monkeypatch):
    monkeypatch.delenv("TIMELY_API_KEY")
    session=Session([])
    with pytest.raises(AIError): TimelyClient(session).complete_text([])
    assert not session.calls

def test_json_repair_aggregate_metrics():
    stats=RunStats()
    result=TimelyClient(Session([Response(content="{"), Response()])).complete_json([], Shape, stats=stats)
    assert result.parsed_output.answer=="yes"
    assert result.transport_attempts==stats.transport_attempts==2
    assert result.usage.total_tokens==stats.usage.total_tokens==20

def test_max_four_transports():
    session=Session([Response(500),Response(content="{"),Response(500),Response(content="{")])
    with pytest.raises(AIError): TimelyClient(session,lambda _:None).complete_json([],Shape)
    assert len(session.calls)==4

def test_cache_deduplicates_concurrent_calls():
    session=Session([Response(content="구체적인 실행안입니다.")])
    runtime=Runtime(TimelyClient(session),cache=MemoryResultCache(),scope_id="work-1")
    request=CoachRequest.model_validate(sample("A"))
    with ThreadPoolExecutor(4) as pool: results=list(pool.map(lambda _:coach_reply(request,runtime=runtime),range(4)))
    assert len(session.calls)==1
    assert sum(r.meta.cache_hit for r in results)==3
    assert sum(r.meta.usage.total_tokens for r in results)==10

def test_scope_and_force_refresh():
    session=Session([Response(content="one"),Response(content="two"),Response(content="fresh")]);cache=MemoryResultCache()
    request=CoachRequest.model_validate(sample("A"))
    first=coach_reply(request,runtime=Runtime(TimelyClient(session),cache=cache,scope_id="1"))
    second=coach_reply(request,runtime=Runtime(TimelyClient(session),cache=cache,scope_id="2"))
    refreshed=coach_reply(request,runtime=Runtime(TimelyClient(session),cache=cache,scope_id="1"),fresh=True)
    assert [r.data.reply for r in (first,second,refreshed)]==["one","two","fresh"]

def test_failed_results_not_cached():
    session=Session([Response(401),Response(content="recovered")]);runtime=Runtime(TimelyClient(session),cache=MemoryResultCache(),scope_id="1")
    req=CoachRequest.model_validate(sample("A"))
    assert coach_reply(req,runtime=runtime).status=="FAILED"
    assert coach_reply(req,runtime=runtime).status=="OK"
    assert len(session.calls)==2

def test_secret_removed_result_trace_cache(monkeypatch):
    key="sk-SYNTHETIC_ONLY_NOT_REAL_123456"
    sink=MemoryTraceSink();cache=MemoryResultCache()
    runtime=Runtime(TimelyClient(Session([Response(content=key)])),trace_sink=sink,debug=True,cache=cache,scope_id="one")
    result=coach_reply(CoachRequest.model_validate(sample("A")),runtime=runtime)
    assert key not in json.dumps([result.model_dump(),sink.events,cache.values],default=str)

def test_default_log_contains_no_conversation():
    sink=MemoryTraceSink()
    coach_reply(CoachRequest.model_validate(sample("A")),runtime=Runtime(TimelyClient(Session([Response(content="answer")])),trace_sink=sink))
    assert "교류회" not in json.dumps(sink.events,ensure_ascii=False)

def test_external_transport_budget_before_request():
    class Budget:
        def reserve(self):raise AIError('EVALUATION_BUDGET','limit')
    session=Session([]);stats=RunStats()
    with pytest.raises(AIError):TimelyClient(session,budget=Budget()).complete_text([],stats=stats)
    assert not session.calls and stats.transport_attempts==0


def test_timely_default_request(monkeypatch):
    monkeypatch.delenv('TIMELY_MODEL', raising=False)
    monkeypatch.delenv('TIMELY_BASE_URL', raising=False)
    session=Session([Response()])
    TimelyClient(session).complete_json([], Shape)
    url, request=session.calls[0]
    assert url=='https://hello.timelyai.io/api/v2/chat/bridge/openai/chat/completions'
    assert request['json']['model']=='openai/gpt-5.6-luna'
    assert request['json']['response_format']=={'type':'json_object'}
    assert 'thinking' not in request['json']
    assert request['headers']['Authorization']=='Bearer sk-SYNTHETIC_ONLY_NOT_REAL_123456'


def test_timely_explicit_settings(monkeypatch):
    monkeypatch.setenv('TIMELY_MODEL','openai/custom-test')
    monkeypatch.setenv('TIMELY_BASE_URL','https://example.invalid/bridge/')
    session=Session([Response()]);TimelyClient(session).complete_text([])
    assert session.calls[0][0]=='https://example.invalid/bridge/chat/completions'
    assert session.calls[0][1]['json']['model']=='openai/custom-test'


def test_deepseek_key_never_used_for_timely(monkeypatch):
    monkeypatch.delenv('TIMELY_API_KEY')
    monkeypatch.setenv('DEEPSEEK_API_KEY','sk-OLD_SYNTHETIC_NOT_REAL_123456')
    session=Session([])
    with pytest.raises(AIError,match='TIMELY_API_KEY'):TimelyClient(session).complete_text([])
    assert not session.calls


@pytest.mark.parametrize('status,code',[(402,'INSUFFICIENT_CREDITS'),(404,'MODEL_NOT_AVAILABLE')])
def test_timely_actionable_errors(status,code):
    session=Session([Response(status)])
    with pytest.raises(AIError) as error:TimelyClient(session).complete_text([])
    assert error.value.code==code and not error.value.retryable
    assert len(session.calls)==1


def test_timely_secret_scrubbed(monkeypatch):
    from wemeet_ai.shared.privacy import sanitize
    key='tgpt_s_SYNTHETIC_ONLY_NOT_REAL_123456'
    monkeypatch.setenv('TIMELY_API_KEY',key)
    sink=MemoryTraceSink();cache=MemoryResultCache()
    result=coach_reply(CoachRequest.model_validate(sample('A')),runtime=Runtime(
        TimelyClient(Session([Response(content=key)])),trace_sink=sink,debug=True,cache=cache))
    exported=sanitize({'TIMELY_API_KEY':key,'headers':{'Authorization':'Bearer '+key},
        'text':'tgpt_s_ANOTHER_SYNTHETIC_SECRET_123456'})
    assert key not in json.dumps([result.model_dump(),sink.events,cache.values,exported],default=str)
    assert 'TIMELY_API_KEY' not in exported and 'tgpt_s_' not in exported['text']
