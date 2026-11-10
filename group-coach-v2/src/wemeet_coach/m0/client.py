"""M0: the only module that knows the provider's HTTP contract."""

import json
import os
import time
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any

import requests
from pydantic import BaseModel, ValidationError

from ..shared.contracts import Usage
from .tracing import emit


class AIError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class Completion:
    content: str
    model: str
    provider_request_id: str | None
    finish_reason: str
    usage: Usage
    latency_ms: int
    transport_attempts: int
    parsed_output: Any = None


@dataclass
class RunStats:
    usage: Usage = field(default_factory=Usage)
    transport_attempts: int = 0
    repair_attempts: int = 0
    output_expansions: int = 0
    request_ids: list[str] = field(default_factory=list)
    actual_model: str | None = None


class TimelyClient:
    def __init__(self, session=None, sleeper=time.sleep, *, budget=None):
        self.session = session if session is not None else requests.Session()
        self.sleeper = sleeper
        self.budget = budget
        self.model = os.getenv("TIMELY_MODEL", "openai/gpt-5.6-luna")
        self.base_url = os.getenv(
            "TIMELY_BASE_URL", "https://hello.timelyai.io/api/v2/chat/bridge/openai"
        ).rstrip("/")

    def complete_text(
        self,
        messages: list[dict],
        options: dict | None = None,
        *,
        stats: RunStats | None = None,
    ) -> Completion:
        stats = stats or RunStats()
        result = self._request(messages, options or {}, stats)
        result.usage = stats.usage.model_copy()
        result.transport_attempts = stats.transport_attempts
        return result

    def complete_json(
        self,
        messages: list[dict],
        output_schema: type[BaseModel],
        options: dict | None = None,
        *,
        stats: RunStats | None = None,
    ) -> Completion:
        stats = stats or RunStats()
        schema_prompt = {
            "role": "system",
            "content": "Return only a JSON object conforming to this schema. Example shape is described by the properties and types. Do not add Markdown. JSON Schema: "
            + json.dumps(output_schema.model_json_schema(), ensure_ascii=False),
        }
        history = [schema_prompt, *messages]
        options = dict(options or {})
        for attempt in range(2):
            try:
                result = self._request(
                    history,
                    {**options, "response_format": {"type": "json_object"}},
                    stats,
                )
            except AIError as exc:
                if (
                    exc.code != "TRUNCATED_OUTPUT"
                    or stats.output_expansions
                    or stats.transport_attempts >= 4
                ):
                    raise
                options["max_tokens"] = min(
                    int(options.get("max_tokens", 1500)) * 2, 32768
                )
                stats.output_expansions += 1
                emit("output.expanded", {"max_tokens": options["max_tokens"]}, "M0")
                result = self._request(
                    history,
                    {**options, "response_format": {"type": "json_object"}},
                    stats,
                )
            try:
                result.parsed_output = output_schema.model_validate(
                    json.loads(result.content)
                )
                emit("json.parsed", result.parsed_output.model_dump(mode="json"), "M0")
                result.usage = stats.usage.model_copy()
                result.transport_attempts = stats.transport_attempts
                return result
            except json.JSONDecodeError:
                code, details = "INVALID_JSON", "Response is not a valid JSON object"
            except ValidationError as exc:
                code = "SCHEMA_INVALID"
                details = json.dumps(
                    [
                        {"path": list(e["loc"]), "message": e["msg"]}
                        for e in exc.errors()
                    ],
                    ensure_ascii=False,
                )
            if attempt == 1:
                raise AIError(code, details)
            stats.repair_attempts += 1
            emit(
                "json.repair",
                {"attempt": attempt + 1, "code": code, "validation_errors": details},
                "M0",
            )
            history += [
                {"role": "assistant", "content": result.content},
                {
                    "role": "user",
                    "content": "Correct only the JSON structure using the supplied facts. Validation errors: "
                    + details,
                },
            ]
        raise AssertionError("unreachable")

    def _request(
        self, messages: list[dict], options: dict, stats: RunStats
    ) -> Completion:
        key = os.getenv("TIMELY_API_KEY")
        if not key:
            raise AIError("AUTH_FAILED", "TIMELY_API_KEY is not configured")
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1500,
            **options,
        }
        started = time.perf_counter()
        for attempt in range(2):
            if stats.transport_attempts >= 4:
                raise AIError(
                    "RETRY_BUDGET_EXCEEDED",
                    "모듈 실행의 실제 HTTP 요청 한도(4회)에 도달했습니다.",
                )
            if self.budget is not None:
                self.budget.reserve()
            stats.transport_attempts += 1
            call_id = str(uuid4())
            try:
                emit(
                    "provider.request",
                    {
                        "call_id": call_id,
                        "url": self.base_url + "/chat/completions",
                        "body": body,
                        "attempt": stats.transport_attempts,
                    },
                    "M0",
                )
                response = self.session.post(
                    self.base_url + "/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + key,
                    },
                    json=body,
                    timeout=(10, 60),
                )
                status = response.status_code
                if status >= 400:
                    try:
                        error_body = response.json()
                    except ValueError:
                        error_body = getattr(
                            response, "text", "Response body could not be parsed"
                        )
                    emit(
                        "provider.response",
                        {"call_id": call_id, "status": status, "body": error_body},
                        "M0",
                    )
                if status in (401, 403):
                    raise AIError("AUTH_FAILED", "Timely rejected the API credentials")
                if status == 402:
                    raise AIError(
                        "INSUFFICIENT_CREDITS", "Timely credits are insufficient"
                    )
                if status == 404:
                    raise AIError(
                        "MODEL_NOT_AVAILABLE", "Timely endpoint or model was not found"
                    )
                if status == 429 or status >= 500:
                    raise AIError(
                        "RATE_LIMITED" if status == 429 else "UPSTREAM_ERROR",
                        f"Timely returned HTTP {status}",
                        True,
                    )
                if status >= 400:
                    raise AIError("INVALID_REQUEST", f"Timely returned HTTP {status}")
                try:
                    data = response.json()
                except ValueError:
                    emit(
                        "provider.response",
                        {
                            "call_id": call_id,
                            "status": status,
                            "body": getattr(response, "text", "Invalid JSON response"),
                        },
                        "M0",
                    )
                    raise AIError(
                        "INVALID_JSON", "Timely returned invalid response JSON"
                    ) from None
                if not isinstance(data, dict):
                    raise AIError("INVALID_JSON", "Timely response must be an object")
                emit(
                    "provider.response",
                    {"call_id": call_id, "status": status, "body": data},
                    "M0",
                )
                usage = data.get("usage") or {}
                if not isinstance(usage, dict):
                    raise AIError("INVALID_JSON", "Timely usage must be an object")
                try:
                    raw_counts = [
                        usage.get(name, 0)
                        for name in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                        )
                    ]
                    if any(
                        type(v) is not int
                        and not (isinstance(v, str) and v.isdecimal())
                        for v in raw_counts
                    ):
                        raise ValueError()
                    counts = {
                        name: int(usage.get(name, 0))
                        for name in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                        )
                    }
                    if any(v < 0 for v in counts.values()):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise AIError(
                        "INVALID_JSON", "Timely returned invalid usage counters"
                    ) from None
                for name, count in counts.items():
                    setattr(stats.usage, name, getattr(stats.usage, name) + count)
                request_id = data.get("id")
                if isinstance(request_id, str):
                    stats.request_ids.append(request_id)
                stats.actual_model = (
                    data.get("model")
                    if isinstance(data.get("model"), str)
                    else self.model
                )
                choices = data.get("choices") or []
                if not choices:
                    raise AIError("EMPTY_RESPONSE", "Timely returned no choices")
                if not isinstance(choices, list) or not isinstance(choices[0], dict):
                    raise AIError(
                        "INVALID_JSON", "Timely choices must contain message objects"
                    )
                choice = choices[0]
                finish = choice.get("finish_reason")
                if finish == "length":
                    raise AIError(
                        "TRUNCATED_OUTPUT",
                        "Output exceeded max_tokens; rerun this stage with a larger limit",
                    )
                if finish != "stop":
                    raise AIError(
                        "INCOMPLETE_OUTPUT", "Unexpected finish reason: " + str(finish)
                    )
                message = choice.get("message") or {}
                if not isinstance(message, dict):
                    raise AIError("INVALID_JSON", "Timely message must be an object")
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise AIError("EMPTY_RESPONSE", "Timely returned empty content")
                return Completion(
                    content,
                    stats.actual_model,
                    request_id if isinstance(request_id, str) else None,
                    finish,
                    Usage(**counts),
                    int((time.perf_counter() - started) * 1000),
                    attempt + 1,
                )
            except requests.Timeout:
                error = AIError("TIMEOUT", "Timely request timed out", True)
            except requests.ConnectionError:
                error = AIError("NETWORK_ERROR", "Could not connect to Timely", True)
            except requests.RequestException:
                error = AIError("NETWORK_ERROR", "Timely HTTP transport failed", True)
            except AIError as exc:
                error = exc
            if not error.retryable or attempt == 1:
                raise error
            emit(
                "provider.retry",
                {"code": error.code, "next_attempt": attempt + 2},
                "M0",
            )
            self.sleeper(1)
        raise AssertionError("unreachable")
