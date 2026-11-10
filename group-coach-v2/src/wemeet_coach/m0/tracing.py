"""M0 scoped execution trace. No web/database imports or global mutable session state."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Protocol

from ..shared.privacy import sanitize


class TraceSink(Protocol):
    def emit(self, event: dict) -> None: ...


class MemoryTraceSink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class JsonlTraceSink:
    def __init__(self, path):
        from pathlib import Path
        from threading import Lock

        self.path = Path(path)
        self.lock = Lock()

    def emit(self, event):
        import json

        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(sanitize(event), ensure_ascii=False, default=str) + "\n"
                )


active = ContextVar("wemeet_coach_trace", default=None)


def scrub(value, owner=None):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in {
                "authorization",
                "api_key",
                "deepseek_api_key",
                "timely_api_key",
                "headers",
                "reasoning_content",
                "reasoning",
                "reasoning_details",
            }:
                continue
            if key == "private_notes" and owner is None:
                result[key] = "PRIVATE: inspect the selected member's execution"
            else:
                result[key] = scrub(item, owner)
        return sanitize(result)
    if isinstance(value, list):
        return [scrub(v, owner) for v in value]
    return sanitize(value)


@contextmanager
def scope(sink, trace_id, run_id, parent_run_id, module, owner=None):
    token = active.set((sink, trace_id, run_id, parent_run_id, module, owner))
    try:
        yield
    finally:
        active.reset(token)


def emit(event, payload, module=None):
    context = active.get()
    if context is None:
        return
    sink, trace_id, run_id, parent, task, owner = context
    if sink is None:
        return
    try:
        sink.emit(
            {
                "trace_id": trace_id,
                "run_id": run_id,
                "parent_run_id": parent,
                "module": module or task,
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "owner_member_id": owner,
                "sanitized_payload": scrub(payload, owner),
            }
        )
    except Exception:
        pass  # A diagnostic sink must never destroy a valid AI result.
