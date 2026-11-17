"""Public, persistence-free contracts for M2 completion review."""
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from ..shared.contracts import Contract


SUBMISSION_DESCRIPTION_EVIDENCE_ID = "submission_description"


class TaskSnapshot(Contract):
    title: str = Field(min_length=1)
    deliverable: str = Field(min_length=1)
    completion_criteria: list[str] = Field(min_length=1)
    due_date: date
    estimated_minutes: int = Field(gt=0)
    difficulty: Literal["EASY", "MEDIUM", "HARD"]


class SubmissionSnapshot(Contract):
    description: str = Field(min_length=1, max_length=5000)
    submitted_at: datetime


class EvidenceMetadata(Contract):
    evidence_id: str = Field(min_length=1)
    original_name: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    content_kind: Literal["IMAGE", "PDF", "DOCX"]
    page_count: int | None = Field(default=None, ge=1)
    extracted_character_count: int = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0)


class CompletionReviewRequest(Contract):
    submission_id: str = Field(min_length=1)
    baseline_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    plan_version_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    owner_member_id: str = Field(min_length=1)
    task: TaskSnapshot
    submission: SubmissionSnapshot
    evidence: list[EvidenceMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_evidence(self):
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)) or SUBMISSION_DESCRIPTION_EVIDENCE_ID in ids:
            raise ValueError("Evidence IDs must be unique and may not use the reserved submission description ID")
        return self


class PreparedEvidence(Contract):
    evidence_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    text: str | None = None
    image_data_url: str | None = None

    @model_validator(mode="after")
    def valid_content(self):
        if not self.text and not self.image_data_url:
            raise ValueError("Prepared evidence requires text or image data")
        if self.image_data_url and not self.image_data_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
            raise ValueError("Prepared image must be a PNG or JPEG data URL")
        return self


class EvidenceReference(Contract):
    evidence_id: str = Field(min_length=1)
    location: str | None = None
    description: str = Field(min_length=1)


class CriterionResult(Contract):
    criterion_index: int = Field(ge=0)
    criterion: str = Field(min_length=1)
    status: Literal["MET", "NOT_MET", "UNKNOWN"]
    reason: str = Field(min_length=1)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)


class CompletionReview(Contract):
    decision: Literal["PASS", "REVIEW_NEEDED", "NEEDS_INPUT"]
    summary: str = Field(min_length=1)
    criteria_results: list[CriterionResult]
    missing_items: list[str] = Field(default_factory=list)
    user_message: str = Field(min_length=1)
