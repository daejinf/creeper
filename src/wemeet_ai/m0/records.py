import json
import threading
from pathlib import Path

from ..shared.privacy import sanitize


class JsonlRecorder:
    """Opt-in local sink. Default records never contain conversation/profile text."""
    def __init__(self, path: str | Path, *, debug_anonymous: bool = False):
        self.path = Path(path)
        self.debug_anonymous = debug_anonymous
        self.lock = threading.Lock()

    def record(self, record: dict, debug: dict | None = None) -> None:
        payload = sanitize(dict(record))
        if self.debug_anonymous and debug is not None:
            payload["debug_anonymous"] = sanitize(debug)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
