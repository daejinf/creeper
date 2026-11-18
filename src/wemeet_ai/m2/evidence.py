"""Safe, persistence-independent preparation of completion evidence files."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

import pymupdf as fitz
from PIL import Image, ImageOps, UnidentifiedImageError
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import Field

from ..shared.contracts import Contract
from .contracts import EvidenceMetadata, PreparedEvidence


MAX_FILES = 3
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_TEXT_CHARACTERS = 30_000
MAX_IMAGES = 20
MAX_IMAGE_DIMENSION = 1600
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 30 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class EvidenceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EvidenceUpload:
    original_name: str
    content: bytes


class PreparedSubmission(Contract):
    evidence: list[EvidenceMetadata] = Field(default_factory=list)
    prepared_evidence: list[PreparedEvidence] = Field(default_factory=list)


@dataclass
class StagedSubmissionEvidence:
    prepared: PreparedSubmission
    staging_path: Path
    target_path: Path
    committed: bool = False

    def commit(self) -> None:
        if self.committed:
            return
        if self.target_path.exists():
            raise EvidenceError("STORAGE_FAILED", "동일한 제출 저장 위치가 이미 존재합니다.")
        os.replace(self.staging_path, self.target_path)
        self.committed = True

    def discard(self) -> None:
        for path in (self.staging_path, self.target_path if self.committed else None):
            if path is not None and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


def _safe_identifier(value: str, label: str) -> str:
    if value in {".", ".."} or not SAFE_ID.fullmatch(value):
        raise EvidenceError("STORAGE_FAILED", f"Invalid {label}")
    return value


def _safe_original_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise EvidenceError("UNSUPPORTED_FILE_TYPE", "파일 이름을 확인해주세요.")
    name = PurePosixPath(PureWindowsPath(value.strip()).name).name
    if not name or name in {".", ".."}:
        raise EvidenceError("UNSUPPORTED_FILE_TYPE", "파일 이름을 확인해주세요.")
    return name[:255]


def _detect_kind(name: str, data: bytes) -> tuple[str, str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".docm":
        raise EvidenceError("UNSUPPORTED_FILE_TYPE", "DOCM 파일은 지원하지 않습니다.")
    expected = {".png", ".jpg", ".jpeg", ".pdf", ".docx"}
    if suffix not in expected:
        raise EvidenceError("UNSUPPORTED_FILE_TYPE", "PNG, JPG/JPEG, PDF, DOCX 파일만 지원합니다.")
    valid = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".pdf": data.startswith(b"%PDF-"),
        ".docx": data.startswith(b"PK\x03\x04"),
    }[suffix]
    if not valid:
        code = "CORRUPT_IMAGE" if suffix in {".png", ".jpg", ".jpeg"} else "CORRUPT_EVIDENCE"
        raise EvidenceError(code, "파일 확장자와 실제 내용이 일치하지 않습니다.")
    if suffix == ".png": return "IMAGE", "image/png"
    if suffix in {".jpg", ".jpeg"}: return "IMAGE", "image/jpeg"
    if suffix == ".pdf": return "PDF", "application/pdf"
    return "DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _image_data_url(image: Image.Image, *, prefer_png: bool) -> str:
    if getattr(image, "n_frames", 1) != 1:
        raise EvidenceError("UNSUPPORTED_IMAGE", "애니메이션 이미지는 지원하지 않습니다.")
    image = ImageOps.exif_transpose(image)
    image.load()
    image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    if prefer_png and has_alpha:
        clean = Image.new("RGBA", image.size)
        clean.paste(image.convert("RGBA"))
        clean.save(output, format="PNG", optimize=True)
        mime = "image/png"
    else:
        clean = Image.new("RGB", image.size, "white")
        if has_alpha:
            rgba = image.convert("RGBA")
            clean.paste(rgba, mask=rgba.getchannel("A"))
        else:
            clean.paste(image.convert("RGB"))
        clean.save(output, format="JPEG", quality=85, optimize=True)
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _prepare_image(data: bytes, evidence_id: str, location: str, *, prefer_png: bool) -> list[PreparedEvidence]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                url = _image_data_url(image, prefer_png=prefer_png)
    except EvidenceError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise EvidenceError("CORRUPT_IMAGE", "이미지 내용을 읽을 수 없습니다.") from exc
    return [PreparedEvidence(evidence_id=evidence_id, location=location, image_data_url=url)]


def _prepare_pdf(data: bytes, evidence_id: str) -> tuple[list[PreparedEvidence], int, int, int]:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except (fitz.FileDataError, RuntimeError, ValueError) as exc:
        raise EvidenceError("CORRUPT_EVIDENCE", "PDF를 열 수 없습니다.") from exc
    try:
        if document.needs_pass or document.is_encrypted:
            raise EvidenceError("CORRUPT_EVIDENCE", "암호화된 PDF는 지원하지 않습니다.")
        page_count = document.page_count
        if page_count < 1:
            raise EvidenceError("CORRUPT_EVIDENCE", "PDF에 페이지가 없습니다.")
        if page_count > MAX_PDF_PAGES:
            raise EvidenceError("PDF_TOO_MANY_PAGES", f"PDF는 최대 {MAX_PDF_PAGES}쪽까지 지원합니다.")
        prepared: list[PreparedEvidence] = []
        character_count = 0
        for index, page in enumerate(document, start=1):
            location = f"PDF {index}쪽"
            text = page.get_text("text").strip()
            if text:
                character_count += len(text)
                prepared.append(PreparedEvidence(evidence_id=evidence_id, location=location, text=text))
            longest = max(float(page.rect.width), float(page.rect.height), 1.0)
            scale = min(2.0, MAX_IMAGE_DIMENSION / longest)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            prepared.extend(_prepare_image(pixmap.tobytes("png"), evidence_id, location, prefer_png=False))
        return prepared, page_count, character_count, page_count
    except EvidenceError:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise EvidenceError("CORRUPT_EVIDENCE", "PDF 내용을 추출할 수 없습니다.") from exc
    finally:
        document.close()


def _validate_docx_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise EvidenceError("CORRUPT_EVIDENCE", "유효한 DOCX 구조가 아닙니다.")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise EvidenceError("UNSUPPORTED_FILE_TYPE", "매크로 포함 문서는 지원하지 않습니다.")
            total = 0
            for info in archive.infolist():
                total += info.file_size
                if info.file_size > MAX_DOCX_MEMBER_BYTES or total > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise EvidenceError("EVIDENCE_TOO_LARGE", "DOCX 압축 해제 크기가 너무 큽니다.")
                if info.compress_size and info.file_size > 1024 * 1024 and info.file_size / info.compress_size > MAX_DOCX_COMPRESSION_RATIO:
                    raise EvidenceError("EVIDENCE_TOO_LARGE", "비정상적인 DOCX 압축 비율입니다.")
            if archive.testzip() is not None:
                raise EvidenceError("CORRUPT_EVIDENCE", "손상된 DOCX ZIP 구조입니다.")
            for name in names:
                if name.endswith(".rels"):
                    relationships = archive.read(name)
                    if b'TargetMode="External"' in relationships or b"TargetMode='External'" in relationships:
                        raise EvidenceError("CORRUPT_EVIDENCE", "외부 파일 관계가 있는 DOCX는 지원하지 않습니다.")
    except EvidenceError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise EvidenceError("CORRUPT_EVIDENCE", "DOCX 압축 구조를 읽을 수 없습니다.") from exc


def _block_image_blobs(document: Document, block: Paragraph | Table):
    for blip in block._element.xpath(".//a:blip"):
        relation_id = blip.get(f"{{{REL_NS}}}embed")
        if relation_id and relation_id in document.part.related_parts:
            part = document.part.related_parts[relation_id]
            blob = getattr(part, "blob", None)
            if isinstance(blob, bytes):
                yield blob


def _prepare_docx(data: bytes, evidence_id: str) -> tuple[list[PreparedEvidence], int, int]:
    _validate_docx_archive(data)
    try:
        document = Document(io.BytesIO(data))
        prepared: list[PreparedEvidence] = []
        character_count = paragraph_index = heading_index = table_index = image_index = 0
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if text:
                    character_count += len(text)
                    is_heading = bool(block.style and block.style.name.lower().startswith("heading"))
                    if is_heading:
                        heading_index += 1
                        location = f"DOCX 제목 {heading_index}"
                    else:
                        paragraph_index += 1
                        location = f"DOCX 문단 {paragraph_index}"
                    prepared.append(PreparedEvidence(
                        evidence_id=evidence_id, location=location, text=text
                    ))
            elif isinstance(block, Table):
                rows = ["\t".join(cell.text.strip() for cell in row.cells) for row in block.rows]
                text = "\n".join(row for row in rows if row.strip()).strip()
                if text:
                    table_index += 1
                    character_count += len(text)
                    prepared.append(PreparedEvidence(
                        evidence_id=evidence_id, location=f"DOCX 표 {table_index}", text=text
                    ))
            for blob in _block_image_blobs(document, block):
                image_index += 1
                prepared.extend(_prepare_image(
                    blob, evidence_id, f"DOCX 삽입 이미지 {image_index}", prefer_png=True
                ))
        if not prepared:
            raise EvidenceError("CORRUPT_EVIDENCE", "DOCX에서 판정 가능한 내용을 추출하지 못했습니다.")
        return prepared, character_count, image_index
    except EvidenceError:
        raise
    except (KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise EvidenceError("CORRUPT_EVIDENCE", "DOCX 내용을 추출할 수 없습니다.") from exc


def _prepare_one(upload: EvidenceUpload, evidence_id: str) -> tuple[EvidenceMetadata, list[PreparedEvidence]]:
    name = _safe_original_name(upload.original_name)
    data = upload.content
    if not isinstance(data, bytes) or not data:
        raise EvidenceError("CORRUPT_EVIDENCE", "빈 파일은 제출할 수 없습니다.")
    if len(data) > MAX_FILE_BYTES:
        raise EvidenceError("FILE_TOO_LARGE", "파일당 최대 크기는 10MB입니다.")
    kind, mime_type = _detect_kind(name, data)
    if kind == "IMAGE":
        prepared = _prepare_image(data, evidence_id, "image", prefer_png=mime_type == "image/png")
        page_count, character_count, image_count = None, 0, 1
    elif kind == "PDF":
        prepared, page_count, character_count, image_count = _prepare_pdf(data, evidence_id)
    else:
        prepared, character_count, image_count = _prepare_docx(data, evidence_id)
        page_count = None
    metadata = EvidenceMetadata(
        evidence_id=evidence_id,
        original_name=name,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content_kind=kind,
        page_count=page_count,
        extracted_character_count=character_count,
        image_count=image_count,
    )
    return metadata, prepared


def prepare_submission_evidence(
    group_id: str,
    submission_id: str,
    uploads: list[EvidenceUpload],
    *,
    storage_root: str | Path = "runtime/submissions",
) -> PreparedSubmission:
    """Validate, prepare, and atomically store one submission's original files."""
    staged = stage_submission_evidence(
        group_id, submission_id, uploads, storage_root=storage_root)
    try:
        staged.commit()
        return staged.prepared
    except Exception:
        staged.discard()
        raise


def stage_submission_evidence(
    group_id: str,
    submission_id: str,
    uploads: list[EvidenceUpload],
    *,
    storage_root: str | Path = "runtime/submissions",
) -> StagedSubmissionEvidence:
    """Validate and stage originals; caller commits them with the DB transaction."""
    group_id = _safe_identifier(group_id, "group_id")
    submission_id = _safe_identifier(submission_id, "submission_id")
    if not uploads:
        raise EvidenceError("CORRUPT_EVIDENCE", "제출 파일이 없습니다.")
    if len(uploads) > MAX_FILES:
        raise EvidenceError("TOO_MANY_FILES", f"제출당 파일은 최대 {MAX_FILES}개입니다.")
    total_size = sum(len(item.content) for item in uploads if isinstance(item.content, bytes))
    if total_size > MAX_TOTAL_BYTES:
        raise EvidenceError("EVIDENCE_TOO_LARGE", "제출 파일 전체 크기는 최대 20MB입니다.")

    root = Path(storage_root).resolve()
    group_directory = root / group_id
    target = group_directory / submission_id
    root.mkdir(parents=True, exist_ok=True)
    group_directory.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise EvidenceError("STORAGE_FAILED", "동일한 제출 저장 위치가 이미 존재합니다.")
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        metadata_items: list[EvidenceMetadata] = []
        prepared_items: list[PreparedEvidence] = []
        manifest_items = []
        for upload in uploads:
            evidence_id = "ev-" + uuid4().hex
            metadata, prepared = _prepare_one(upload, evidence_id)
            metadata_items.append(metadata)
            prepared_items.extend(prepared)
            storage_name = evidence_id + ".bin"
            (staging / storage_name).write_bytes(upload.content)
            manifest_items.append({
                "evidence_id": evidence_id,
                "storage_name": storage_name,
                "mime_type": metadata.mime_type,
                "size_bytes": metadata.size_bytes,
                "sha256": metadata.sha256,
            })
        character_count = sum(item.extracted_character_count for item in metadata_items)
        image_count = sum(item.image_count for item in metadata_items)
        if character_count > MAX_TEXT_CHARACTERS:
            raise EvidenceError("EVIDENCE_TOO_LARGE", "모델 입력 추출문은 최대 30,000자입니다.")
        if image_count > MAX_IMAGES:
            raise EvidenceError("EVIDENCE_TOO_LARGE", "모델 입력 이미지는 최대 20개입니다.")
        (staging / "manifest.json").write_text(
            json.dumps({"version": 1, "evidence": manifest_items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        prepared = PreparedSubmission(evidence=metadata_items, prepared_evidence=prepared_items)
        return StagedSubmissionEvidence(prepared=prepared, staging_path=staging, target_path=target)
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, EvidenceError):
            raise
        if isinstance(exc, (OSError, ValueError)):
            raise EvidenceError("STORAGE_FAILED", "증빙 파일을 저장하지 못했습니다.") from exc
        raise


def _stored_submission_path(storage_root: str | Path, group_id: str, submission_id: str) -> Path:
    group_id = _safe_identifier(group_id, "group_id")
    submission_id = _safe_identifier(submission_id, "submission_id")
    root = Path(storage_root).resolve()
    target = (root / group_id / submission_id).resolve()
    if root not in target.parents:
        raise EvidenceError("STORAGE_FAILED", "Invalid submission storage path")
    return target


def load_prepared_evidence(
    group_id: str,
    submission_id: str,
    evidence: list[EvidenceMetadata | dict],
    *,
    storage_root: str | Path = "runtime/submissions",
) -> list[PreparedEvidence]:
    """Rebuild model inputs from private originals after a process restart."""
    target = _stored_submission_path(storage_root, group_id, submission_id)
    items = [EvidenceMetadata.model_validate(item) for item in evidence]
    prepared: list[PreparedEvidence] = []
    characters = images = 0
    for expected in items:
        evidence_id = _safe_identifier(expected.evidence_id, "evidence_id")
        path = target / f"{evidence_id}.bin"
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise EvidenceError("STORAGE_FAILED", "저장된 증빙 원본을 읽지 못했습니다.") from exc
        if len(data) > MAX_FILE_BYTES or len(data) != expected.size_bytes or hashlib.sha256(data).hexdigest() != expected.sha256:
            raise EvidenceError("CORRUPT_EVIDENCE", "저장된 증빙의 크기 또는 해시가 다릅니다.")
        actual, parts = _prepare_one(EvidenceUpload(expected.original_name, data), expected.evidence_id)
        compared = ('mime_type','content_kind','page_count','extracted_character_count','image_count')
        if any(getattr(actual, field) != getattr(expected, field) for field in compared):
            raise EvidenceError("CORRUPT_EVIDENCE", "저장된 증빙 메타데이터와 추출 결과가 다릅니다.")
        prepared.extend(parts)
        characters += actual.extracted_character_count
        images += actual.image_count
    if characters > MAX_TEXT_CHARACTERS or images > MAX_IMAGES:
        raise EvidenceError("EVIDENCE_TOO_LARGE", "저장된 증빙이 모델 입력 제한을 초과합니다.")
    return prepared


def discard_submission_evidence(
    group_id: str,
    submission_id: str,
    *,
    storage_root: str | Path = "runtime/submissions",
) -> None:
    """Remove one exact uncommitted/reused upload directory; never a group or root."""
    target = _stored_submission_path(storage_root, group_id, submission_id)
    if target.is_dir():
        shutil.rmtree(target)
