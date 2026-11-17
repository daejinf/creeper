import base64
import io
import json

import pymupdf as fitz
import pytest
from PIL import Image, PngImagePlugin
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE

from wemeet_ai.m2 import EvidenceError, EvidenceUpload, prepare_submission_evidence
from wemeet_ai.m2 import evidence as evidence_module


def image_bytes(format="PNG", *, size=(32, 24), color=(220, 20, 20), metadata=False):
    output = io.BytesIO()
    image = Image.new("RGB", size, color)
    kwargs = {}
    if format == "PNG" and metadata:
        info = PngImagePlugin.PngInfo()
        info.add_text("private", "must-not-survive")
        kwargs["pnginfo"] = info
    image.save(output, format=format, **kwargs)
    return output.getvalue()


def pdf_bytes(*texts):
    document = fitz.open()
    for text in texts:
        page = document.new_page(width=300, height=200)
        page.insert_text((30, 60), text)
    data = document.tobytes()
    document.close()
    return data


def docx_bytes(*, text="Project summary", table=True, image=True, external=False):
    document = Document()
    document.add_heading("Evidence", level=1)
    document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=1, cols=2)
        grid.cell(0, 0).text = "status"
        grid.cell(0, 1).text = "done"
    if image:
        document.add_picture(io.BytesIO(image_bytes()), width=914400)
    if external:
        document.part.relate_to("https://example.invalid/file", RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def decoded_model_image(prepared):
    item = next(part for part in prepared if part.image_data_url)
    encoded = item.image_data_url.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(encoded)))


@pytest.mark.parametrize("name,format,mime", [
    ("photo.png", "PNG", "image/png"),
    ("photo.jpg", "JPEG", "image/jpeg"),
])
def test_image_prepare_resize_strip_metadata_and_store(tmp_path, name, format, mime):
    source = image_bytes(format, size=(2400, 1200), metadata=format == "PNG")
    result = prepare_submission_evidence(
        "group-1", "submission-1", [EvidenceUpload("../../" + name, source)], storage_root=tmp_path
    )
    metadata = result.evidence[0]
    assert metadata.original_name == name
    assert metadata.mime_type == mime and metadata.image_count == 1
    model_image = decoded_model_image(result.prepared_evidence)
    assert max(model_image.size) == 1600
    assert "private" not in model_image.info
    stored = tmp_path / "group-1" / "submission-1"
    assert sorted(path.suffix for path in stored.iterdir()) == [".bin", ".json"]
    assert next(stored.glob("*.bin")).read_bytes() == source
    manifest = (stored / "manifest.json").read_text(encoding="utf-8")
    assert name not in manifest and ".." not in manifest and "base64" not in manifest


def test_pdf_extracts_page_text_and_renders_bounded_images(tmp_path):
    result = prepare_submission_evidence(
        "group-1", "submission-pdf", [EvidenceUpload("report.pdf", pdf_bytes("first page", "second page"))],
        storage_root=tmp_path,
    )
    metadata = result.evidence[0]
    assert metadata.content_kind == "PDF" and metadata.page_count == 2 and metadata.image_count == 2
    text_parts = [item for item in result.prepared_evidence if item.text]
    assert [item.location for item in text_parts] == ["PDF 1쪽", "PDF 2쪽"]
    assert "first page" in text_parts[0].text and "second page" in text_parts[1].text
    assert all(max(decoded_model_image([item]).size) <= 1600 for item in result.prepared_evidence if item.image_data_url)


def test_docx_extracts_paragraph_table_and_inserted_image_in_order(tmp_path):
    result = prepare_submission_evidence(
        "group-1", "submission-docx", [EvidenceUpload("evidence.docx", docx_bytes())], storage_root=tmp_path
    )
    metadata = result.evidence[0]
    locations = [item.location for item in result.prepared_evidence]
    assert metadata.content_kind == "DOCX" and metadata.image_count == 1
    assert "DOCX 제목 1" in locations and "DOCX 문단 1" in locations
    assert "DOCX 표 1" in locations and "DOCX 삽입 이미지 1" in locations
    assert locations.index("DOCX 표 1") < locations.index("DOCX 삽입 이미지 1")
    assert any("status\tdone" in (item.text or "") for item in result.prepared_evidence)


def test_rejects_too_many_files_before_storage(tmp_path):
    uploads = [EvidenceUpload(f"{index}.png", image_bytes()) for index in range(4)]
    with pytest.raises(EvidenceError) as error:
        prepare_submission_evidence("group-1", "submission-many", uploads, storage_root=tmp_path)
    assert error.value.code == "TOO_MANY_FILES"
    assert not (tmp_path / "group-1" / "submission-many").exists()


@pytest.mark.parametrize("name,data,code", [
    ("bad.png", b"not-a-png", "CORRUPT_IMAGE"),
    ("bad.pdf", b"not-a-pdf", "CORRUPT_EVIDENCE"),
    ("bad.docx", b"not-a-docx", "CORRUPT_EVIDENCE"),
    ("macro.docm", b"PK\x03\x04x", "UNSUPPORTED_FILE_TYPE"),
    ("script.exe", b"MZ", "UNSUPPORTED_FILE_TYPE"),
])
def test_rejects_signature_mismatch_and_unsupported_types(tmp_path, name, data, code):
    with pytest.raises(EvidenceError) as error:
        prepare_submission_evidence("group-1", "submission-bad", [EvidenceUpload(name, data)], storage_root=tmp_path)
    assert error.value.code == code


def test_rejects_file_total_text_image_and_page_limits_without_final_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_module, "MAX_FILE_BYTES", 20)
    with pytest.raises(EvidenceError) as file_error:
        prepare_submission_evidence(
            "group-1", "too-big", [EvidenceUpload("large.png", image_bytes())], storage_root=tmp_path
        )
    assert file_error.value.code == "FILE_TOO_LARGE"

    monkeypatch.setattr(evidence_module, "MAX_FILE_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(evidence_module, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(EvidenceError) as total_error:
        prepare_submission_evidence(
            "group-1", "total-big", [EvidenceUpload("a.png", image_bytes()), EvidenceUpload("b.png", image_bytes())],
            storage_root=tmp_path,
        )
    assert total_error.value.code == "EVIDENCE_TOO_LARGE"

    monkeypatch.setattr(evidence_module, "MAX_TOTAL_BYTES", 20 * 1024 * 1024)
    monkeypatch.setattr(evidence_module, "MAX_TEXT_CHARACTERS", 5)
    with pytest.raises(EvidenceError) as text_error:
        prepare_submission_evidence(
            "group-1", "text-big", [EvidenceUpload("text.docx", docx_bytes(text="long evidence text", image=False))],
            storage_root=tmp_path,
        )
    assert text_error.value.code == "EVIDENCE_TOO_LARGE"

    monkeypatch.setattr(evidence_module, "MAX_TEXT_CHARACTERS", 30_000)
    monkeypatch.setattr(evidence_module, "MAX_IMAGES", 0)
    with pytest.raises(EvidenceError) as image_error:
        prepare_submission_evidence(
            "group-1", "image-big", [EvidenceUpload("image.png", image_bytes())], storage_root=tmp_path
        )
    assert image_error.value.code == "EVIDENCE_TOO_LARGE"

    monkeypatch.setattr(evidence_module, "MAX_IMAGES", 20)
    monkeypatch.setattr(evidence_module, "MAX_PDF_PAGES", 1)
    with pytest.raises(EvidenceError) as page_error:
        prepare_submission_evidence(
            "group-1", "page-big", [EvidenceUpload("pages.pdf", pdf_bytes("one", "two"))], storage_root=tmp_path
        )
    assert page_error.value.code == "PDF_TOO_MANY_PAGES"
    assert not any((tmp_path / "group-1" / name).exists() for name in ["too-big", "total-big", "text-big", "image-big", "page-big"])


def test_rejects_animated_png_and_external_docx_relationship(tmp_path):
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
    animated = io.BytesIO()
    frames[0].save(animated, format="PNG", save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(EvidenceError) as image_error:
        prepare_submission_evidence(
            "group-1", "animated", [EvidenceUpload("animated.png", animated.getvalue())], storage_root=tmp_path
        )
    assert image_error.value.code == "UNSUPPORTED_IMAGE"

    with pytest.raises(EvidenceError) as docx_error:
        prepare_submission_evidence(
            "group-1", "external", [EvidenceUpload("external.docx", docx_bytes(external=True))], storage_root=tmp_path
        )
    assert docx_error.value.code == "CORRUPT_EVIDENCE"


def test_rejects_unsafe_storage_identifiers(tmp_path):
    with pytest.raises(EvidenceError) as error:
        prepare_submission_evidence("../group", "submission", [EvidenceUpload("a.png", image_bytes())], storage_root=tmp_path)
    assert error.value.code == "STORAGE_FAILED"
    assert list(tmp_path.iterdir()) == []


def test_single_long_text_uses_total_character_limit(tmp_path):
    with pytest.raises(EvidenceError) as error:
        prepare_submission_evidence(
            "group-1", "long-text",
            [EvidenceUpload("long.docx", docx_bytes(text="x" * 30_001, image=False))],
            storage_root=tmp_path,
        )
    assert error.value.code == "EVIDENCE_TOO_LARGE"
    assert not (tmp_path / "group-1" / "long-text").exists()
