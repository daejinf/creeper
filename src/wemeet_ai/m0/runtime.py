"""Per-call execution state. No singleton workflow, no cache, no framework imports.

One `run` is one model call. There is no result cache (a stored result is the caller's job), no source-reload
gate and no hidden token override: the output limit is `max_output_tokens` or the client default.
"""
import hashlib
import json
import time
from uuid import uuid4
from pydantic import ValidationError
from .client import TimelyClient, RunStats, AIError
from .tracing import scope, emit
from ..shared.contracts import Result, Meta, Error
from ..shared.privacy import sanitize

PROMPT_VERSION = "0.13.1-v3"


def code_result(task, data=None, *, needed=(), error=None):
    return Result(status="FAILED" if error else "NEEDS_INPUT" if needed else "OK", data=data,
                  needed_inputs=list(needed), error=error, meta=Meta(run_id=str(uuid4()), task=task))


class Runtime:
    def __init__(self, client=None, *, trace_sink=None, debug=False, max_output_tokens=None):
        self.client = client if client is not None else TimelyClient()
        self.trace_sink, self.debug, self.max_output_tokens = trace_sink, debug, max_output_tokens

    def run(self, task, request, output_type, instructions, *, text=False, check=None, owner=None,
            content_parts=None, max_output_tokens=None):
        payload = sanitize(request.model_dump(mode="json"))
        stats, started = RunStats(), time.perf_counter()
        meta = Meta(run_id=str(uuid4()), task=task, prompt_version=f"{task}-{PROMPT_VERSION}", model=self.client.model)
        limit = max_output_tokens or self.max_output_tokens
        options = {"max_completion_tokens": limit} if limit else {}
        # Raw traces require explicit opt-in and must be stored in the caller's private scope.
        sink = self.trace_sink if self.debug else None
        with scope(sink, meta.run_id, meta.run_id, None, task, owner):
            user_content = json.dumps(payload, ensure_ascii=False)
            # Size and hash only: the request body is private user input and is never traced.
            emit("input.prepared", {"content_bytes": len(user_content.encode("utf-8")),
                                    "content_sha256": hashlib.sha256(user_content.encode("utf-8")).hexdigest()})
            if content_parts is not None:
                user_content = [{"type": "text", "text": user_content}, *content_parts]
            messages = [{"role": "system", "content": instructions}, {"role": "user", "content": user_content}]
            try:
                # One model call per run. A failed check is a failure, never a second call.
                if text:
                    completion = self.client.complete_text(messages, options, stats=stats)
                    data = output_type(reply=completion.content)
                else:
                    data = self.client.complete_json(messages, output_type, options, stats=stats).parsed_output
                data = output_type.model_validate(sanitize(data.model_dump(mode="json")))
                if check: check(data)
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
        return result
