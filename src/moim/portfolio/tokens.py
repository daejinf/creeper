"""Token arithmetic without a tokenizer at runtime (RAG_DESIGN 2.3, 3.3).

`estimate()` turns a text into a cl100k token estimate from character counts only, so the runtime needs neither
`tiktoken` nor its downloaded BPE file. The constants were measured with `tiktoken cl100k_base` on the fixtures in
`tests/portfolio_fixtures.py` (`test_korean_chars_per_token_measurement` re-measures them) and chosen as the smallest
values on a 0.05/0.02/0.1 grid that keep the estimate at or above the real count on every fixture: a chunk is never
larger than the cap we think it has. Measured values are recorded in RAG_DESIGN 9.

`bm25_tokens()` is the lexical side of retrieval (RAG_DESIGN 3.3): no Korean morphological analyser, so Hangul runs
become syllable 2-grams (a lone syllable stays one token), every other letter/digit run becomes one lowercase word.
Whitespace and punctuation only separate. The list is stored per node at ingest time; BM25 itself is stage 8C.
"""
from __future__ import annotations

import math
import re

# Measured 2026-11-23 (tiktoken 0.14.0, cl100k_base), RAG_DESIGN 9. Korean prose: 1.03 characters per token,
# 1.37 tokens per Hangul syllable. Whitespace is not counted (it mostly merges into the next token).
TOKENS_PER_HANGUL = 1.45
TOKENS_PER_LATIN = 0.22   # ASCII letters
TOKENS_PER_OTHER = 1.2    # digits, punctuation and every other non-space character

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_SPACE = re.compile(r"\s")
_WORDS = re.compile(r"[가-힣]+|[^\W_가-힣]+")


def estimate(text: str) -> int:
    """cl100k token estimate (an upper bound on the measured fixtures)."""
    if not text:
        return 0
    hangul = len(_HANGUL.findall(text))
    latin = len(_LATIN.findall(text))
    other = len(text) - hangul - latin - len(_SPACE.findall(text))
    return math.ceil(hangul * TOKENS_PER_HANGUL + latin * TOKENS_PER_LATIN + other * TOKENS_PER_OTHER)


def bm25_tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _WORDS.finditer(text or ""):
        w = m.group()
        if "가" <= w[0] <= "힣":
            out.extend([w] if len(w) == 1 else [w[i:i + 2] for i in range(len(w) - 1)])
        else:
            out.append(w.lower())
    return out
