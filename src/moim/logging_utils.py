"""Logging filter that masks secrets (DB URL passwords, keys, tokens) in every record."""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://[^:/\s@]+:)[^@\s]+@", re.I), r"\g<scheme>***@"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*)[^\s,;'\"]+"),
        r"\1\2***",
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the filter to every handler so records from all loggers are masked."""
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
