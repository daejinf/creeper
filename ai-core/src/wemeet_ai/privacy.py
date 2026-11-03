import re
import os
from copy import deepcopy


def redact(text: str) -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if key: text = text.replace(key, "[REDACTED_KEY]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED_KEY]", text)
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", text)
    return re.sub(r"(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)", "[REDACTED_PHONE]", text)


def sanitize(value):
    if isinstance(value, str): return redact(value)
    if isinstance(value, list): return [sanitize(v) for v in value]
    if isinstance(value, dict): return {k: sanitize(v) for k, v in value.items() if k.lower().replace('-','_') not in {'authorization','api_key','deepseek_api_key','headers','key_fingerprint'}}
    return deepcopy(value)
