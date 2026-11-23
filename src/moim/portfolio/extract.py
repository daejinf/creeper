"""Deterministic attachment extraction for the portfolio copy (RAG_DESIGN 2.1). No AI, no network, no disk writes.

Every file goes through `execution.files.check_file` again first (content-verified type, size, image limits, and
`_zip_check` for DOCX/PPTX/XLSX/HWPX: entry count, total and per-member size, compression ratio, path traversal,
encryption, macros). A file that fails is kept as a `REJECTED` document with the check's code and no text.

The result is a list of blocks `{block_id, kind, locator, text, vision}`:
- kind `text` (a page, a heading section), `table` (TSV rows), `slide`, `image`, `meta` (file name only);
- `locator` is where the block came from (`file` plus `page` / `section` / `table` / `slide` / `image` / `sheet`
  and `rows`); every later citation resolves to it;
- `vision: true` marks the targets of the stage 8B visual description: an image file, a PDF page with almost no
  text (a figure page) and an image inserted in a PPTX slide (never the whole slide, RAG_DESIGN 2.2).

HWP and HWPX are not extracted (no deterministic extractor): status `NAME_ONLY`, indexed by file name and memo.
Caps per file (pages, slides, sheets, rows, columns, characters) cut the extraction; the cut is recorded in
`truncated_reason`, never hidden. Nothing here logs a file name or content.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from ..execution import files as F

MAX_PDF_PAGES = 100
MAX_SLIDES = 100
MAX_SHEETS = 10
MAX_ROWS = 300            # per sheet
MAX_COLS = 30
MAX_CELL = 500            # characters per cell
MAX_DOC_CHARS = 200_000   # per file
FIGURE_PAGE_CHARS = 50    # a PDF page with fewer non-space characters is a figure page (vision target)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DOCX_HEADING = re.compile(r"^(heading|title|제목)", re.I)
_NONSPACE = re.compile(r"\S")


@dataclass
class Extracted:
    kind: str
    status: str                      # EXTRACTED | NAME_ONLY | REJECTED
    blocks: list[dict] = field(default_factory=list)
    error_code: str | None = None
    truncated_reason: str | None = None

    @property
    def char_count(self) -> int:
        return sum(len(b["text"]) for b in self.blocks)


class _Builder:
    def __init__(self, prefix: str, name: str, cap: int):
        self.prefix, self.name, self.left = prefix, name, cap
        self.blocks: list[dict] = []
        self.truncated: str | None = None

    def cut(self, reason: str) -> None:
        self.truncated = self.truncated or reason

    @property
    def full(self) -> bool:
        return self.left <= 0

    def add(self, kind: str, text: str, vision: bool = False, **where) -> None:
        text = (text or "").strip()
        if len(text) > self.left:
            # keep whole lines where possible; the rest is cut and recorded
            head = text[: self.left]
            nl = head.rfind("\n")
            text = head[:nl] if nl > self.left // 2 else head
            self.cut("CHAR_LIMIT")
        self.left -= len(text)
        if not text and not vision:
            return
        locator = {"file": self.name, **where}
        self.blocks.append({"block_id": f"{self.prefix}b{len(self.blocks) + 1}", "kind": kind, "locator": locator,
                            "text": text, "vision": vision})


def extract_file(prefix: str, name: str, data: bytes, cap: int = MAX_DOC_CHARS) -> Extracted:
    """One attachment. `prefix` makes block ids unique within one submission (e.g. "d1")."""
    try:
        checked = F.check_file(name, data)
    except F.FileRejected as exc:
        return Extracted(kind=_kind_of(name), status="REJECTED", error_code=exc.code[:32])
    kind = checked.kind
    if kind in ("HWP", "HWPX"):
        return Extracted(kind=kind, status="NAME_ONLY")
    b = _Builder(prefix, checked.name, cap)
    try:
        if kind in ("PNG", "JPEG", "WEBP"):
            b.add("image", "", vision=True)
        elif kind == "PDF":
            _pdf(b, checked.data)
        elif kind == "DOCX":
            _docx(b, checked.data)
        elif kind in ("TXT", "MD"):
            _plain(b, checked.data.decode("utf-8-sig"), markdown=kind == "MD")
        elif kind == "PPTX":
            _pptx(b, checked.data)
        elif kind == "XLSX":
            _xlsx(b, checked.data)
    except Exception:  # a file that passed the check but cannot be parsed: kept, without text (never the message)
        return Extracted(kind=kind, status="REJECTED", error_code="PARSE_FAILED")
    return Extracted(kind=kind, status="EXTRACTED", blocks=b.blocks, truncated_reason=b.truncated)


def _kind_of(name: str) -> str:
    import os

    return F.EXTENSIONS.get(os.path.splitext(name or "")[1].lower(), "TXT") if name else "TXT"


# ---- formats --------------------------------------------------------------------------------------------------------


def _pdf(b: _Builder, data: bytes) -> None:
    import pymupdf as fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.needs_pass or doc.is_encrypted:
            return  # shared as is; nothing can be read
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                b.cut("PAGE_LIMIT")
                break
            if b.full:
                b.cut("CHAR_LIMIT")
                break
            text = page.get_text("text") or ""
            figure = len(_NONSPACE.findall(text)) < FIGURE_PAGE_CHARS
            b.add("image" if figure else "text", text, vision=figure, page=i + 1)
    finally:
        doc.close()


def _docx(b: _Builder, data: bytes) -> None:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    heading: str | None = None
    buf: list[str] = []
    tables = 0

    def flush():
        if buf:
            b.add("text", "\n".join(buf), section=heading)
            buf.clear()

    for block in doc.iter_inner_content():
        if b.full:
            b.cut("CHAR_LIMIT")
            break
        if isinstance(block, Paragraph):
            t = block.text.strip()
            if not t:
                continue
            style = (block.style.name if block.style is not None else "") or ""
            if _DOCX_HEADING.match(style):
                flush()
                heading = t[:120]
            buf.append(t)
        elif isinstance(block, Table):
            flush()
            tables += 1
            rows = ["\t".join(c.text.strip()[:MAX_CELL] for c in row.cells[:MAX_COLS]) for row in block.rows]
            b.add("table", "\n".join(r for r in rows if r.strip()), section=heading, table=tables)
    flush()


def _plain(b: _Builder, text: str, markdown: bool) -> None:
    if not markdown:
        b.add("text", text)
        return
    heading: str | None = None
    buf: list[str] = []
    fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
        m = None if fence else _HEADING.match(line)
        if m:
            if "\n".join(buf).strip():
                b.add("text", "\n".join(buf), section=heading)
            buf = [line]
            heading = m.group(2)[:120]
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        b.add("text", "\n".join(buf), section=heading)


def _shape_texts(shapes, out: list[str], images: list):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            _shape_texts(sh.shapes, out, images)
            continue
        if getattr(sh, "has_table", False) and sh.has_table:
            rows = ["\t".join(c.text.strip()[:MAX_CELL] for c in list(r.cells)[:MAX_COLS]) for r in sh.table.rows]
            t = "\n".join(r for r in rows if r.strip())
            if t:
                out.append(t)
        elif getattr(sh, "has_text_frame", False) and sh.has_text_frame:
            t = sh.text_frame.text.strip()
            if t:
                out.append(t)
        if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                images.append(sh.image.sha1)
            except Exception:  # a linked (external) picture has no blob: nothing to describe
                pass


def _pptx(b: _Builder, data: bytes) -> None:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    for i, slide in enumerate(prs.slides):
        if i >= MAX_SLIDES:
            b.cut("SLIDE_LIMIT")
            break
        if b.full:
            b.cut("CHAR_LIMIT")
            break
        parts: list[str] = []
        images: list[str] = []
        _shape_texts(slide.shapes, parts, images)
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                parts.append("[노트] " + notes)
        b.add("slide", "\n".join(parts), slide=i + 1)
        for k, sha1 in enumerate(images):
            b.add("image", "", vision=True, slide=i + 1, image=k + 1, sha1=sha1)


def _cell(v) -> str:
    if v is None:
        return ""
    s = str(v).replace("\t", " ").replace("\n", " ").strip()
    return s[:MAX_CELL]


def _xlsx(b: _Builder, data: bytes) -> None:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        for si, ws in enumerate(wb.worksheets):
            if si >= MAX_SHEETS:
                b.cut("SHEET_LIMIT")
                break
            if b.full:
                b.cut("CHAR_LIMIT")
                break
            lines: list[str] = []
            first = last = None
            for ri, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if row is not None and len(row) > MAX_COLS and any(v is not None for v in row[MAX_COLS:]):
                    b.cut("COLUMN_LIMIT")
                cells = [_cell(v) for v in (row or ())[:MAX_COLS]]
                while cells and not cells[-1]:
                    cells.pop()
                if not any(cells):
                    continue
                if len(lines) >= MAX_ROWS:
                    b.cut("ROW_LIMIT")
                    break
                lines.append("\t".join(cells))
                first = first or ri
                last = ri
            if lines:
                b.add("table", "\n".join(lines), sheet=ws.title[:64], rows=[first, last])
    finally:
        wb.close()
