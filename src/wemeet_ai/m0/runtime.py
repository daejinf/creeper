"""Per-call execution state. No singleton workflow and no framework imports."""
import hashlib
import json
import os
import time
from contextlib import nullcontext
from uuid import uuid4
from pydantic import ValidationError
from .client import TimelyClient, RunStats, AIError
from .cache import VALIDATOR_VERSION
from .tracing import scope, emit
from .versions import loaded_revision, reload_required
from ..shared.contracts import Result, Meta, Usage, Error
from ..shared.privacy import sanitize

def code_result(task, data=None, *, needed=(), error=None):
    return Result(status="FAILED" if error else "NEEDS_INPUT" if needed else "OK", data=data,
                  needed_inputs=list(needed), error=error, meta=Meta(run_id=str(uuid4()), task=task,source_revision=loaded_revision(task)))

class Runtime:
    def __init__(self, client=None, *, cache=None, scope_id=None, trace_sink=None, debug=False):
        self.client = client if client is not None else TimelyClient()
        self.cache, self.scope_id, self.trace_sink, self.debug = cache, scope_id, trace_sink, debug

    def run(self, task, request, output_type, instructions, *, text=False, check=None, owner=None, fresh=False):
        if reload_required(task):
            return code_result(task,error=Error(code='SOURCE_RELOAD_REQUIRED',message='모듈 코드가 바뀌었습니다. 서버 코드 반영이 끝난 뒤 다시 실행해주세요.',retryable=True))
        payload = sanitize(request.model_dump(mode="json"))
        prompt_version = task + ("-0.13.1-v5-kickoff" if task == "A" else "-0.13.1-v3")
        key = hashlib.sha256(json.dumps([task, payload, output_type.model_json_schema(), self.scope_id, owner, self.client.model,
            self.client.base_url, prompt_version, VALIDATOR_VERSION, loaded_revision(task), instructions,
            hashlib.sha256(os.getenv("TIMELY_API_KEY", "").encode()).hexdigest()], sort_keys=True).encode()).hexdigest()
        lock = self.cache.lock(key) if self.cache and self.scope_id else nullcontext()
        with lock:
            if self.cache and self.scope_id and not fresh:
                try:
                    saved = self.cache.get(key)
                except Exception:
                    return code_result(task, error=Error(code="CACHE_UNAVAILABLE", message="캐시를 읽지 못해 중복 유료 호출을 중단했습니다.", retryable=True))
                if saved:
                    try: result = Result[output_type].model_validate(saved)
                    except ValidationError:
                        return code_result(task, error=Error(code="CACHE_INVALID", message="캐시 형식이 현재 계약과 다릅니다."))
                    result.meta.source_run_id = result.meta.run_id
                    result.meta.run_id = str(uuid4())
                    result.meta.cached_usage = result.meta.usage.model_copy()
                    result.meta.usage = Usage()
                    result.meta.transport_attempts = result.meta.repair_attempts = 0
                    result.meta.provider_request_ids = []
                    result.meta.execution_source = "CACHE"
                    result.meta.cache_hit = True
                    result.meta.latency_ms = 0
                    return result
            stats, started = RunStats(), time.perf_counter()
            meta = Meta(run_id=str(uuid4()), task=task, prompt_version=prompt_version, model=self.client.model,source_revision=loaded_revision(task))
            # Raw traces require explicit opt-in and must be stored in the caller's private scope.
            sink = self.trace_sink if self.debug else None
            with scope(sink, meta.run_id, meta.run_id, None, task, owner):
                emit("input.prepared", payload)
                messages = [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
                try:
                    for attempt in range(2):
                        if text:
                            completion = self.client.complete_text(messages, stats=stats)
                            data = output_type(reply=completion.content)
                        else:
                            data = self.client.complete_json(messages, output_type, {"max_tokens": 4096}, stats=stats).parsed_output
                        data = output_type.model_validate(sanitize(data.model_dump(mode="json")))
                        try:
                            if check: check(data)
                            break
                        except AIError as exc:
                            if attempt or stats.transport_attempts >= 4: raise
                            stats.repair_attempts += 1
                            emit("validation.repair", {"code": exc.code, "message": str(exc)})
                            messages += [{"role": "assistant", "content": data.model_dump_json()}, {"role": "user", "content": "Correct the output: " + str(exc)}]
                    result = Result(status="OK", data=data, meta=meta)
                except AIError as exc:
                    result = Result(status="FAILED", error=Error(code=exc.code, message=sanitize(str(exc)), retryable=exc.retryable), meta=meta)
                except (ValidationError, ValueError, TypeError, KeyError):
                    result = Result(status="FAILED", error=Error(code="SCHEMA_INVALID", message="응답 형식이 계약과 맞지 않습니다."), meta=meta)
                meta.usage = stats.usage
                meta.transport_attempts = stats.transport_attempts
                meta.repair_attempts = stats.repair_attempts
                meta.provider_request_ids = stats.request_ids
                meta.model = stats.actual_model or self.client.model
                meta.execution_source = "REPLAY" if getattr(self.client, "is_fixture", False) else "LIVE"
                meta.latency_ms = int((time.perf_counter() - started) * 1000)
                emit("run.completed", {"status": result.status, "meta": meta.model_dump(mode="json")})
            if self.trace_sink and not self.debug:
                try: self.trace_sink.emit({"event": "run.completed", "meta": meta.model_dump(mode="json"), "status": result.status})
                except Exception: result.warnings.append("실행 기록 저장 실패 · AI 결과는 유지됩니다.")
            if self.cache and self.scope_id and result.status == "OK":
                try: self.cache.put(key, result.model_dump(mode="json"))
                except Exception: result.warnings.append("캐시 저장 실패 · 생성 결과는 유지됩니다.")
            return result
