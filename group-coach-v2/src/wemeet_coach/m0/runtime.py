"""One logical model run, maximum four transports; no implicit response cache."""

import time
import json
from uuid import uuid4
from pydantic import ValidationError
from .client import TimelyClient, RunStats, AIError
from .tracing import scope, emit
from ..shared.contracts import Result, Meta, Error
from ..shared.privacy import sanitize


def code_result(task, data=None, needed=(), error=None):
    return Result(
        status="FAILED" if error else "NEEDS_INPUT" if needed else "OK",
        data=data,
        needed_inputs=list(needed),
        error=error,
        meta=Meta(run_id=str(uuid4()), task=task),
    )


def input_error(task, exception):
    """Explain invalid request paths without exposing their supplied values."""
    fields = [
        ".".join(str(part) for part in issue["loc"]) + ": " + issue["msg"]
        for issue in exception.errors(include_input=False, include_url=False)
    ]
    return code_result(
        task,
        needed=sanitize(fields),
        error=Error(code="INPUT_INVALID", message="입력 형식을 확인해주세요."),
    )


class Runtime:
    def __init__(self, client=None, *, trace_sink=None):
        self.client = client if client is not None else TimelyClient()
        self.trace_sink = trace_sink

    def run(
        self,
        task,
        request,
        output_type,
        instructions,
        *,
        text=False,
        check=None,
        owner=None,
    ):
        stats = RunStats()
        start = time.perf_counter()
        meta = Meta(
            run_id=str(uuid4()),
            task=task,
            model=self.client.model,
            prompt_version=(
                "personal-0.3-v3" if task.startswith("personal.") else "coach-0.3-v1"
            ),
        )
        payload = sanitize(request.model_dump(mode="json"))
        for member in payload.get("members", []):
            member.pop("external_user_id", None)
        messages = [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        with scope(self.trace_sink, meta.run_id, meta.run_id, None, task, owner):
            emit("input.prepared", payload)
            try:
                for repair in range(2):
                    if text:
                        completion = self.client.complete_text(
                            messages, {"max_tokens": 4096}, stats=stats
                        )
                        data = output_type(reply=completion.content)
                    else:
                        data = self.client.complete_json(
                            messages,
                            output_type,
                            {
                                "max_tokens": (
                                    8192
                                    if task in {"personal.talk", "group.coach"}
                                    else 16384
                                )
                            },
                            stats=stats,
                        ).parsed_output
                    try:
                        if check:
                            check(data)
                        break
                    except (ValueError, AIError) as e:
                        if repair or stats.transport_attempts >= 4:
                            raise AIError("PLAN_INVALID", "응답 검증 실패: " + str(e))
                        stats.repair_attempts += 1
                        emit("validation.repair", {"reason": str(e)})
                        messages += [
                            {"role": "assistant", "content": data.model_dump_json()},
                            {
                                "role": "user",
                                "content": "확인된 사실과 원래 사용자의 요청을 유지하고 다음 오류만 수정하세요: "
                                + str(e)
                                + " 최종 reply·summary·details는 원래 사용자에게 보내는 완성 답변입니다. 내부 검증·JSON·보정 과정이나 임시 과제 ID를 설명하지 말고 원래 코칭/계획 요청에 답하세요.",
                            },
                        ]
                result = Result(data=data, meta=meta)
            except AIError as e:
                result = Result(
                    status="FAILED",
                    error=Error(
                        code=e.code, message=sanitize(str(e)), retryable=e.retryable
                    ),
                    meta=meta,
                )
            except (ValueError, TypeError, KeyError, ValidationError):
                result = Result(
                    status="FAILED",
                    error=Error(
                        code="SCHEMA_INVALID", message="응답 형식 검증에 실패했습니다."
                    ),
                    meta=meta,
                )
            meta.model = stats.actual_model or self.client.model
            meta.usage = stats.usage
            meta.transport_attempts = stats.transport_attempts
            meta.repair_attempts = stats.repair_attempts
            meta.provider_request_ids = stats.request_ids
            meta.latency_ms = int((time.perf_counter() - start) * 1000)
            meta.execution_source = "LIVE" if stats.transport_attempts else "CODE"
            meta.output_expansions = stats.output_expansions
            emit("run.completed", result.model_dump(mode="json"))
            return result
