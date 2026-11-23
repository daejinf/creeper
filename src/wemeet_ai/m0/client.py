"""M0: the only module that knows the provider's HTTP contract."""
import hashlib
import json
import logging
import os
import time
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any

import requests
from pydantic import BaseModel, ValidationError

from ..shared.contracts import Usage
from .tracing import emit, current_stage

call_log = logging.getLogger("moim.provider")

# Output limit for one call (reasoning tokens count against it). Callers may pass `max_completion_tokens`.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


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


# Known output sizes (OpenAI documentation; `docs/LIVE_TESTING.md` calls 213-214 for -small through Timely).
EMBEDDING_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}


@dataclass
class Embeddings:
    vectors: Any          # numpy float32 array (n, dim); every row L2-normalized by the server
    model: str
    dim: int
    usage: Usage
    latency_ms: int


@dataclass
class RunStats:
    usage: Usage = field(default_factory=Usage)
    transport_attempts: int = 0
    repair_attempts: int = 0  # always 0 in the MVP: there is no automatic repair call
    plan_repair_attempts: int = 0
    request_ids: list[str] = field(default_factory=list)
    actual_model: str | None = None


def schema_instruction(output_schema: type[BaseModel]) -> str:
    """The system message `complete_json` puts first. Callers that size a whole prompt (RAG_DESIGN 3.2) count it."""
    return ("Return only a JSON object conforming to this schema. Example shape is described by the properties and types. "
            "Do not add Markdown. JSON Schema: " + json.dumps(output_schema.model_json_schema(), ensure_ascii=False))


class TimelyClient:
    """One `complete_*` call is one HTTP request. No retry, no backoff, no JSON repair call.

    Every failure (timeout, 429/5xx, bad JSON, schema error) is raised as AIError. The caller stores
    the error and shows a retry button; a user retry is a new call.
    """

    def __init__(self, session=None):
        self.session = session if session is not None else requests.Session()
        # The old `.../chat/bridge/openai` endpoint is retired (HTTP 410 endpoint_retired). The direct API takes
        # official model names without a provider prefix. Both values can be overridden by environment.
        self.model = os.getenv("TIMELY_MODEL", "gpt-5.6-luna")
        self.embedding_model = os.getenv("TIMELY_EMBEDDING_MODEL", "text-embedding-3-small")
        self.base_url = os.getenv("TIMELY_BASE_URL", "https://hello.timelyai.io/api/v2/chat/direct/openai/v1").rstrip("/")

    def complete_text(self, messages: list[dict], options: dict | None = None, *, stats: RunStats | None = None) -> Completion:
        stats = stats or RunStats()
        result = self._request(messages, options or {}, stats)
        result.usage = stats.usage.model_copy()
        result.transport_attempts = stats.transport_attempts
        return result

    def complete_json(self, messages: list[dict], output_schema: type[BaseModel], options: dict | None = None, *, stats: RunStats | None = None) -> Completion:
        stats = stats or RunStats()
        schema_prompt = {"role": "system", "content": schema_instruction(output_schema)}

        def parse(content: str):
            try:
                return output_schema.model_validate(json.loads(content))
            except json.JSONDecodeError:
                raise AIError("INVALID_JSON", "Response is not a valid JSON object") from None
            except ValidationError as exc:
                paths = [".".join(str(part) for part in e["loc"]) for e in exc.errors()]
                raise AIError("SCHEMA_INVALID", "Response does not match the schema at: " + ", ".join(paths)) from None

        result = self._request([schema_prompt, *messages], {**(options or {}), "response_format": {"type": "json_object"}}, stats, parse)
        emit("json.parsed", {"schema": output_schema.__name__, "content_bytes": len(result.content.encode("utf-8"))}, "M0")
        result.usage = stats.usage.model_copy()
        result.transport_attempts = stats.transport_attempts
        return result

    def _request(self, messages: list[dict], options: dict, stats: RunStats, parse=None) -> Completion:
        if os.getenv('WEMEET_OFFLINE')=='1':
            raise AIError('LIVE_CALLS_PAUSED','추가 LIVE 호출 보류 중입니다. 저장 결과·로컬 검사를 사용하세요.')
        key = os.getenv("TIMELY_API_KEY")
        if not key:
            raise AIError("AUTH_FAILED", "TIMELY_API_KEY is not configured")
        body = {"model": self.model, "messages": messages, "max_completion_tokens": DEFAULT_MAX_OUTPUT_TOKENS, **options}  # gpt-5.x rejects `max_tokens`
        call = {"call_id": str(uuid4()), "model": self.model, "usage": Usage(), "request_id": None}
        started = time.perf_counter()
        stats.transport_attempts += 1
        try:
            completion = self._send(body, key, messages, stats, call, started)
            if parse is not None:
                completion.parsed_output = parse(completion.content)
            return completion
        except AIError as exc:
            call["error"] = exc.code
            raise
        finally:
            self._log_call(call, started)

    def _send(self, body: dict, key: str, messages: list[dict], stats: RunStats, call: dict, started: float) -> Completion:
        has_image = any(
            isinstance(message.get("content"), list)
            and any(isinstance(part, dict) and part.get("type") in ("image_url", "file") for part in message["content"])
            for message in messages
            if isinstance(message, dict)
        )
        data = self._post("/chat/completions", body, key, call, len(messages), has_image)
        counts = self._usage(data, stats, call)
        stats.actual_model = data.get("model") if isinstance(data.get("model"), str) else self.model
        call["model"] = stats.actual_model
        choices = data.get("choices") or []
        if not choices: raise AIError("EMPTY_RESPONSE", "Timely returned no choices")
        if not isinstance(choices, list) or not isinstance(choices[0], dict): raise AIError("INVALID_JSON", "Timely choices must contain message objects")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish == "length": raise AIError("TRUNCATED_OUTPUT", "Output exceeded max_completion_tokens; rerun this stage with a larger limit")
        if finish != "stop": raise AIError("INCOMPLETE_OUTPUT", "Unexpected finish reason: " + str(finish))
        message = choice.get("message") or {}
        if not isinstance(message, dict): raise AIError("INVALID_JSON", "Timely message must be an object")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip(): raise AIError("EMPTY_RESPONSE", "Timely returned empty content")
        return Completion(content, stats.actual_model, call["request_id"], finish, Usage(**counts), int((time.perf_counter() - started) * 1000), 1)

    def _post(self, path: str, body: dict, key: str, call: dict, message_count: int, has_image: bool = False) -> dict:
        """One HTTP POST with the shared error classification. Returns the response object (a dict)."""
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        # Only sizes, a hash and IDs are recorded. Never the messages or inputs, the key or any response text.
        emit("provider.request", {"call_id": call["call_id"], "model": body.get("model"), "message_count": message_count,
                                  "body_bytes": len(payload), "body_sha256": hashlib.sha256(payload).hexdigest()}, "M0")
        try:
            response = self.session.post(self.base_url + path, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, json=body, timeout=(10, 60))
        except requests.Timeout:
            raise AIError("TIMEOUT", "Timely request timed out", True) from None
        except requests.ConnectionError:
            raise AIError("NETWORK_ERROR", "Could not connect to Timely", True) from None
        except requests.RequestException:
            raise AIError("NETWORK_ERROR", "Timely HTTP transport failed", True) from None
        status = response.status_code
        emit("provider.response", {"call_id": call["call_id"], "status": status, "body_bytes": len(getattr(response, "text", "") or "")}, "M0")
        if status in (401, 403):
            raise AIError("AUTH_FAILED", "Timely rejected the API credentials")
        if status == 402:
            raise AIError("INSUFFICIENT_CREDITS", "Timely credits are insufficient")
        if status == 404:
            raise AIError("MODEL_NOT_AVAILABLE", "Timely endpoint or model was not found")
        if status == 429 or status >= 500:
            raise AIError("RATE_LIMITED" if status == 429 else "UPSTREAM_ERROR", f"Timely returned HTTP {status}", True)
        if status >= 400:
            code = "VISION_NOT_SUPPORTED" if status == 400 and has_image else "INVALID_REQUEST"
            raise AIError(code, f"Timely returned HTTP {status}")
        try:
            data = response.json()
        except ValueError:
            raise AIError("INVALID_JSON", "Timely returned invalid response JSON") from None
        if not isinstance(data, dict):
            raise AIError("INVALID_JSON", "Timely response must be an object")
        return data

    @staticmethod
    def _usage(data: dict, stats: RunStats, call: dict) -> dict:
        usage = data.get("usage") or {}
        if not isinstance(usage, dict): raise AIError("INVALID_JSON", "Timely usage must be an object")
        try:
            counts = {name: int(usage.get(name, 0)) for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
            if any(v < 0 for v in counts.values()): raise ValueError()
        except (ValueError, TypeError):
            raise AIError("INVALID_JSON", "Timely returned invalid usage counters") from None
        for name, count in counts.items():
            setattr(stats.usage, name, getattr(stats.usage, name) + count)
        call["usage"] = Usage(**counts)
        request_id = data.get("id")
        if isinstance(request_id, str):
            stats.request_ids.append(request_id)
            call["request_id"] = request_id
        return counts

    def embed(self, texts: list[str], *, model: str | None = None, stats: RunStats | None = None) -> "Embeddings":
        """`POST /embeddings` (stage 8B, RAG_DESIGN 2.6): one HTTP request, no retry. The inputs are never logged.

        The response is checked (one vector per input, each index once, one dimension, the model's known dimension,
        finite numbers, no zero vector) and every vector is L2-normalized here, whatever the proxy did. The rows come
        back in input order as float32."""
        import numpy as np

        stats = stats or RunStats()
        if not texts or not all(isinstance(t, str) and t.strip() for t in texts):
            raise AIError("INVALID_REQUEST", "embedding inputs must be non-empty strings")
        if os.getenv("WEMEET_OFFLINE") == "1":
            raise AIError("LIVE_CALLS_PAUSED", "추가 LIVE 호출 보류 중입니다. 저장 결과·로컬 검사를 사용하세요.")
        key = os.getenv("TIMELY_API_KEY")
        if not key:
            raise AIError("AUTH_FAILED", "TIMELY_API_KEY is not configured")
        name = model or self.embedding_model
        body = {"model": name, "input": list(texts), "encoding_format": "float"}
        call = {"call_id": str(uuid4()), "model": name, "usage": Usage(), "request_id": None}
        started = time.perf_counter()
        stats.transport_attempts += 1
        try:
            data = self._post("/embeddings", body, key, call, len(texts))
            self._usage(data, stats, call)
            actual = data.get("model") if isinstance(data.get("model"), str) else name
            call["model"] = stats.actual_model = actual
            rows = data.get("data")
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise AIError("INVALID_EMBEDDING", "embedding count does not match the inputs")
            ordered: list = [None] * len(texts)
            for pos, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise AIError("INVALID_EMBEDDING", "embedding rows must be objects")
                index = row.get("index", pos)
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(texts) or ordered[index] is not None:
                    raise AIError("INVALID_EMBEDDING", "embedding index is invalid")
                vec = row.get("embedding")
                if not isinstance(vec, list) or not vec or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vec):
                    raise AIError("INVALID_EMBEDDING", "embedding must be a list of numbers")
                ordered[index] = vec
            dims = {len(v) for v in ordered}
            if len(dims) != 1:
                raise AIError("INVALID_EMBEDDING", "embedding dimensions differ")
            dim = dims.pop()
            expected = EMBEDDING_DIMENSIONS.get(name)
            if expected is not None and dim != expected:
                raise AIError("INVALID_EMBEDDING", f"embedding dimension {dim} is not {expected}")
            matrix = np.asarray(ordered, dtype=np.float64)
            if not np.isfinite(matrix).all():
                raise AIError("INVALID_EMBEDDING", "embedding has non-finite numbers")
            norms = np.linalg.norm(matrix, axis=1)
            if (norms <= 1e-12).any():
                raise AIError("INVALID_EMBEDDING", "embedding has a zero vector")
            vectors = (matrix / norms[:, None]).astype(np.float32)
            return Embeddings(vectors, actual, dim, stats.usage.model_copy(), int((time.perf_counter() - started) * 1000))
        except AIError as exc:
            call["error"] = exc.code
            raise
        finally:
            self._log_call(call, started)

    @staticmethod
    def _log_call(call: dict, started: float) -> None:
        """One line per real HTTP call, success or failure, so calls can be counted."""
        usage = call["usage"]
        error = call.get("error")
        call_log.info(
            "provider_call call_id=%s stage=%s model=%s ok=%s%s latency_ms=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d request_id=%s",
            call["call_id"], current_stage(), call["model"], "false" if error else "true", f" error={error}" if error else "",
            int((time.perf_counter() - started) * 1000), usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, call["request_id"] or "-")
