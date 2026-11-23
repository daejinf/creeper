"""Structure-first chunking (RAG_DESIGN 2.3).

Boundaries, strongest first: file → page/slide/sheet → heading section (each of these is already one block) →
paragraph → sentence. A chunk never crosses a block, never overlaps another chunk (overlap 0), and is never cut inside
a sentence. Sizes are cl100k token estimates (`tokens.estimate`): about TARGET, at most MAX. A table or slide block of
at most MAX stays whole. A larger table is split between rows; its header row goes to the chunk's `header` (used in
the index text only), so the chunk texts themselves never repeat anything. The only cut inside a sentence is a single
sentence longer than MAX on its own; it is split at whitespace (then at characters) and marked `forced`.

Image blocks (`vision`) make a chunk only once stage 8B has described them (`vision_status == "DESCRIBED"`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .tokens import estimate

TARGET = 250
MAX = 400
MIN = 100  # below this a chunk takes the next unit even across a paragraph boundary

_PARA = re.compile(r"\n[ \t]*\n+")
_SENT = re.compile(r"(?<=[.!?。！？…])\s+")


@dataclass
class Chunk:
    block_id: str
    kind: str          # the block's kind
    locator: dict
    text: str
    tokens: int
    header: str | None = None   # a split table's header row (index text only)
    forced: bool = False         # an over-long single sentence had to be split


def sentences(paragraph: str) -> list[str]:
    parts = _SENT.split(paragraph.strip())
    return [p for p in (x.strip() for x in parts) if p]


def _units(text: str) -> list[tuple[str, bool]]:
    """(unit, starts a paragraph). Units are sentences; paragraphs are separated by blank lines."""
    out = []
    for para in _PARA.split(text.strip()):
        first = True
        for s in sentences(para):
            # an over-long "sentence" with line breaks is a list or a wrapped block: its lines are the units
            lines = [ln.strip() for ln in s.split("\n") if ln.strip()] if estimate(s) > MAX and "\n" in s else [s]
            for ln in lines:
                out.append((ln, first))
                first = False
    return out


def _hard_split(unit: str) -> list[str]:
    """Only for one sentence longer than MAX: at line breaks, then whitespace, then characters."""
    pieces: list[str] = []
    cur = ""
    for word in re.split(r"(\s+)", unit):
        cand = cur + word
        if cur and estimate(cand) > TARGET:
            pieces.append(cur.strip())
            cur = word.lstrip()
        else:
            cur = cand
        while estimate(cur) > MAX:  # one "word" with no whitespace at all
            cut = len(cur)
            while cut > 1 and estimate(cur[:cut]) > TARGET:
                cut = cut * 3 // 4
            pieces.append(cur[:cut])
            cur = cur[cut:]
    if cur.strip():
        pieces.append(cur.strip())
    return pieces


def _pack(units: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """Greedy packing of sentences: (text, forced)."""
    chunks: list[tuple[str, bool]] = []
    cur: list[str] = []
    cur_t = 0
    sep_para: list[bool] = []

    def flush():
        nonlocal cur, cur_t, sep_para
        if cur:
            text = cur[0]
            for u, para in zip(cur[1:], sep_para[1:]):
                text += ("\n\n" if para else " ") + u
            chunks.append((text, False))
        cur, cur_t, sep_para = [], 0, []

    for unit, para in units:
        t = estimate(unit)
        if t > MAX:
            flush()
            chunks.extend((p, True) for p in _hard_split(unit))
            continue
        if cur and (cur_t + t > MAX or (cur_t >= MIN and (para or cur_t + t > TARGET))):
            flush()
        cur.append(unit)
        sep_para.append(para)
        cur_t += t
    flush()
    return chunks


def _table(block: dict) -> list[Chunk]:
    """Between rows only. The first chunk starts with the header row; later chunks carry it in `header`."""
    lines = [ln for ln in block["text"].split("\n") if ln.strip()]
    header = lines[0]
    out: list[Chunk] = []
    cur: list[str] = []
    cur_t = 0

    def emit(rows: list[str], forced: bool = False):
        text = "\n".join(rows)
        out.append(Chunk(block["block_id"], block["kind"], dict(block["locator"]), text, estimate(text),
                         header=header if out else None, forced=forced))

    for row in lines:
        t = estimate(row)
        if t > MAX:
            if cur:
                emit(cur)
            cur, cur_t = [], 0
            for piece in _hard_split(row):
                emit([piece], forced=True)
            continue
        if cur and (cur_t + t > MAX or (cur_t >= MIN and cur_t + t > TARGET)):
            emit(cur)
            cur, cur_t = [], 0
        cur.append(row)
        cur_t += t
    if cur:
        emit(cur)
    return out


def chunk_blocks(blocks: list[dict]) -> list[Chunk]:
    out: list[Chunk] = []
    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text or (block.get("vision") and block.get("vision_status") != "DESCRIBED"):
            continue  # an image without its stage 8B description (or one skipped by the cap) makes no chunk
        t = estimate(text)
        if block["kind"] in ("table", "slide", "meta") and t <= MAX:
            parts = [Chunk(block["block_id"], block["kind"], dict(block["locator"]), text, t)]
        elif block["kind"] == "table":
            parts = _table(block)
        else:
            src = text if block["kind"] != "slide" else "\n\n".join(text.split("\n"))
            parts = [Chunk(block["block_id"], block["kind"], dict(block["locator"]), p, estimate(p), forced=f)
                     for p, f in _pack(_units(src))]
        if len(parts) > 1:
            for i, c in enumerate(parts):
                c.locator["part"] = i + 1
        out.extend(parts)
    return out
