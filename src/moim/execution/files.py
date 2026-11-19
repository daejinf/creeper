"""Team attachments (D-D1 D4): content-verified formats, size and archive limits, safe storage, and the M2 review copy.

Team sharing accepts 11 formats, checked by content (the extension must agree with what the bytes are). Only some
of them can be read by M2: images (a metadata-free copy, long side at most 2048px), PDF (a metadata-free copy sent as
a file part), DOCX text (embedded images are not reviewed), TXT and MD. HWP, HWPX, PPTX and XLSX are stored and
shared normally but are not reviewed (HWPX stays unsupported until a deterministic extraction test on real files
exists). Nothing here logs a file name, file content or a data URL.

Stored files are named by a server uuid under `<root>/<group_id>/`; the user's file name is kept in the database for
display only and never becomes a path.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import shutil
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import pymupdf as fitz
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 5
GROUP_TOTAL_BYTES = 500 * 1024 * 1024
MAX_MEMO = 1000
MAX_TITLE = 40
MAX_NAME = 255

# Image safety limits (5단계 기술 세부): per side and total pixels, checked before decoding the pixel data.
MAX_IMAGE_SIDE = 12_000
MAX_IMAGE_PIXELS = 40_000_000
REVIEW_IMAGE_SIDE = 2048
IMAGE_DECODERS = ("PNG", "JPEG", "WEBP")  # stage 7: the only Pillow decoders an upload may ever reach

# Archive limits for DOCX, HWPX, PPTX, XLSX (5단계 기술 세부). Nothing is ever extracted to disk.
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_TOTAL = 200 * 1024 * 1024
MAX_ZIP_MEMBER = 100 * 1024 * 1024
MAX_ZIP_RATIO = 150

# One M2 review (not an upload limit, D-D1 D4).
REVIEW_MAX_PDF_PAGES = 20
REVIEW_MAX_TEXT = 20_000

EXTENSIONS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".pdf": "PDF", ".docx": "DOCX", ".hwp": "HWP",
    ".hwpx": "HWPX", ".txt": "TXT", ".md": "MD", ".pptx": "PPTX", ".xlsx": "XLSX",
}
ZIP_KINDS = {"DOCX": "word/document.xml", "PPTX": "ppt/presentation.xml", "XLSX": "xl/workbook.xml"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

MSG_TYPE = "PNG, JPG, WEBP, PDF, DOCX, HWP, HWPX, TXT, MD, PPTX, XLSX 파일만 첨부할 수 있어요."
MSG_MISMATCH = "파일 확장자와 실제 내용이 달라 첨부하지 않았어요."
MSG_CORRUPT = "파일을 읽을 수 없어 첨부하지 않았어요."
MSG_SIZE = "파일 하나는 10MB까지 첨부할 수 있어요."
MSG_EMPTY = "빈 파일은 첨부할 수 없어요."
MSG_IMAGE = "이미지 해상도가 너무 커요."
MSG_ARCHIVE = "압축 구조가 비정상적이라 첨부하지 않았어요."
MSG_MACRO = "매크로가 들어 있는 문서는 첨부할 수 없어요."
MSG_NAME = "파일 이름을 확인해 주세요."


class FileRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


@dataclass(frozen=True)
class CheckedFile:
    name: str          # display only, never a path
    kind: str          # one of models.FILE_KINDS
    data: bytes
    sha256: str
    review_kind: str | None  # IMAGE | PDF | TEXT, None = stored and shared but not reviewed by M2
    embedded_images: bool    # DOCX with pictures: only its text is reviewed


def safe_name(raw: str) -> str:
    """The last path segment, without control characters, at most 255 characters. Rejects names with nothing left."""
    if not isinstance(raw, str):
        raise FileRejected("BAD_NAME", MSG_NAME)
    name = PurePosixPath(PureWindowsPath(raw.strip()).name).name
    name = CONTROL.sub("", name).strip()
    if not name or name in {".", ".."} or name.startswith("."):
        raise FileRejected("BAD_NAME", MSG_NAME)
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: MAX_NAME - len(ext) - 1] + "." + ext) if dot and len(ext) <= 5 else name[:MAX_NAME]
    return name


def _zip_check(data: bytes, kind: str) -> bool:
    """Validates the archive without extracting it. Returns whether it holds embedded media (DOCX pictures)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise FileRejected("ARCHIVE_LIMIT", MSG_ARCHIVE)
            total = 0
            names = set()
            for info in infos:
                n = info.filename
                if info.flag_bits & 0x1:
                    raise FileRejected("ENCRYPTED", MSG_CORRUPT)
                if n.startswith(("/", "\\")) or ".." in PurePosixPath(n.replace("\\", "/")).parts:
                    raise FileRejected("ARCHIVE_LIMIT", MSG_ARCHIVE)
                total += info.file_size
                if info.file_size > MAX_ZIP_MEMBER or total > MAX_ZIP_TOTAL:
                    raise FileRejected("ARCHIVE_LIMIT", MSG_ARCHIVE)
                if info.file_size > 1024 * 1024 and info.file_size > MAX_ZIP_RATIO * max(info.compress_size, 1):
                    raise FileRejected("ARCHIVE_LIMIT", MSG_ARCHIVE)
                names.add(n)
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                raise FileRejected("MACRO", MSG_MACRO)
            if kind == "HWPX":
                if "mimetype" not in names or z.read("mimetype").strip() != b"application/hwp+zip":
                    raise FileRejected("MISMATCH", MSG_MISMATCH)
                if not any(n.startswith("Contents/") for n in names):
                    raise FileRejected("MISMATCH", MSG_MISMATCH)
                return False
            if "[Content_Types].xml" not in names or ZIP_KINDS[kind] not in names:
                raise FileRejected("MISMATCH", MSG_MISMATCH)
            if b"macroEnabled" in z.read("[Content_Types].xml"):
                raise FileRejected("MACRO", MSG_MACRO)
            return kind == "DOCX" and any(n.startswith("word/media/") for n in names)
    except FileRejected:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, OSError, ValueError, NotImplementedError,
            RuntimeError, EOFError):
        raise FileRejected("CORRUPT", MSG_CORRUPT) from None


def _image_check(data: bytes, kind: str) -> bool:
    """Format, dimensions and a full decode. Returns whether M2 can review it (a single static frame)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # Stage 7: only the expected decoder may touch the bytes. Without `formats=` Pillow sniffs any of its
            # ~40 formats (TIFF, EPS, GIF, ...) before the format check below runs.
            with Image.open(io.BytesIO(data), formats=[kind]) as im:
                if im.format != {"PNG": "PNG", "JPEG": "JPEG", "WEBP": "WEBP"}[kind]:
                    raise FileRejected("MISMATCH", MSG_MISMATCH)
                w, h = im.size
                if w < 1 or h < 1 or w > MAX_IMAGE_SIDE or h > MAX_IMAGE_SIDE or w * h > MAX_IMAGE_PIXELS:
                    raise FileRejected("IMAGE_LIMIT", MSG_IMAGE)
                im.load()
                return getattr(im, "n_frames", 1) == 1
    except FileRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise FileRejected("CORRUPT", MSG_CORRUPT) from None


def _pdf_check(data: bytes) -> bool:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:  # pymupdf raises several types for broken input
        raise FileRejected("CORRUPT", MSG_CORRUPT) from None
    try:
        if doc.needs_pass or doc.is_encrypted:
            return False  # shared as is; M2 cannot read it
        return doc.page_count >= 1
    finally:
        doc.close()


def _text_check(data: bytes) -> None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileRejected("MISMATCH", MSG_MISMATCH) from None
    if CONTROL.search(text):
        raise FileRejected("MISMATCH", MSG_MISMATCH)


def check_file(raw_name: str, data: bytes) -> CheckedFile:
    """Content-verified team attachment, or FileRejected. The extension only selects which check must pass."""
    name = safe_name(raw_name)
    if not data:
        raise FileRejected("EMPTY", MSG_EMPTY)
    if len(data) > MAX_FILE_BYTES:
        raise FileRejected("FILE_TOO_LARGE", MSG_SIZE)
    kind = EXTENSIONS.get(os.path.splitext(name)[1].lower())
    if kind is None:
        raise FileRejected("TYPE", MSG_TYPE)
    review: str | None = None
    embedded = False
    if kind == "PNG":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FileRejected("MISMATCH", MSG_MISMATCH)
        review = "IMAGE" if _image_check(data, kind) else None
    elif kind == "JPEG":
        if not data.startswith(b"\xff\xd8\xff"):
            raise FileRejected("MISMATCH", MSG_MISMATCH)
        review = "IMAGE" if _image_check(data, kind) else None
    elif kind == "WEBP":
        if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            raise FileRejected("MISMATCH", MSG_MISMATCH)
        review = "IMAGE" if _image_check(data, kind) else None
    elif kind == "PDF":
        if not data.startswith(b"%PDF-"):
            raise FileRejected("MISMATCH", MSG_MISMATCH)
        review = "PDF" if _pdf_check(data) else None
    elif kind in ("DOCX", "PPTX", "XLSX", "HWPX"):
        if not data.startswith(b"PK\x03\x04"):
            raise FileRejected("MISMATCH", MSG_MISMATCH)
        embedded = _zip_check(data, kind)
        review = "TEXT" if kind == "DOCX" else None
    elif kind == "HWP":
        if not data.startswith(OLE_MAGIC) or b"HWP Document File" not in data:
            raise FileRejected("MISMATCH", MSG_MISMATCH)
    else:  # TXT, MD
        _text_check(data)
        review = "TEXT"
    return CheckedFile(name, kind, data, hashlib.sha256(data).hexdigest(), review, embedded)


# ---- storage ------------------------------------------------------------------------------------------------------


class Storage:
    """`<root>/<group_id>/<file_id>`; both ids are server uuids, checked again before every path is built."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def path(self, group_id: str, file_id: str) -> Path:
        if not UUID_RE.fullmatch(group_id or "") or not UUID_RE.fullmatch(file_id or ""):
            raise ValueError("invalid storage id")
        p = (self.root / group_id / file_id).resolve()
        if p.parent.parent != self.root:
            raise ValueError("invalid storage path")
        return p

    def write(self, group_id: str, file_id: str, data: bytes) -> Path:
        p = self.path(group_id, file_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        with open(tmp, "xb") as f:
            f.write(data)
        os.replace(tmp, p)
        return p

    def read(self, group_id: str, file_id: str) -> bytes:
        return self.path(group_id, file_id).read_bytes()

    def delete(self, group_id: str, file_id: str) -> bool:
        """True when the file is gone (also when it was never there). False only when removing it failed."""
        try:
            self.path(group_id, file_id).unlink(missing_ok=True)
            return True
        except OSError:
            return False  # an orphan file is harmless and unreachable (no row points at it)

    def delete_group_dir(self, group_id: str) -> bool:
        """Stage 6: a deleted group's whole folder (every file in it belongs to that group only)."""
        if not UUID_RE.fullmatch(group_id or ""):
            raise ValueError("invalid storage id")
        d = (self.root / group_id).resolve()
        if d.parent != self.root:
            raise ValueError("invalid storage path")
        try:
            shutil.rmtree(d, ignore_errors=False)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


# ---- M2 review copies -----------------------------------------------------------------------------------------------


def review_image(data: bytes) -> str:
    """A metadata-free copy, upright, long side at most 2048px, as a PNG (alpha) or JPEG data URL."""
    with Image.open(io.BytesIO(data), formats=IMAGE_DECODERS) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((REVIEW_IMAGE_SIDE, REVIEW_IMAGE_SIDE), Image.Resampling.LANCZOS)
        alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        out = io.BytesIO()
        if alpha:
            clean = Image.new("RGBA", im.size)
            clean.paste(im.convert("RGBA"))
            clean.save(out, format="PNG", optimize=True)  # a new image: no EXIF, text chunks or ICC profile
            mime = "image/png"
        else:
            clean = Image.new("RGB", im.size, "white")
            clean.paste(im.convert("RGB"))
            clean.save(out, format="JPEG", quality=85, optimize=True)
            mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(out.getvalue()).decode("ascii")


def review_pdf(data: bytes) -> tuple[str, int]:
    """A copy without document metadata (info dictionary and XMP) and its page count."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages = doc.page_count
        doc.set_metadata({})
        doc.del_xml_metadata()
        clean = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
    return "data:application/pdf;base64," + base64.b64encode(clean).decode("ascii"), pages


def review_text(kind: str, data: bytes) -> str:
    if kind in ("TXT", "MD"):
        return data.decode("utf-8-sig").strip()
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    parts: list[str] = []
    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            t = block.text.strip()
            if t:
                parts.append(t)
        elif isinstance(block, Table):
            rows = ["\t".join(c.text.strip() for c in row.cells) for row in block.rows]
            t = "\n".join(r for r in rows if r.strip()).strip()
            if t:
                parts.append(t)
    return "\n".join(parts)
