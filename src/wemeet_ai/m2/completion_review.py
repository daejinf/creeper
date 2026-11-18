"""M2 completion decision with deterministic post-validation."""
from ..m0 import AIError, Runtime
from ..shared.contracts import Result
from .contracts import (
    SUBMISSION_DESCRIPTION_EVIDENCE_ID,
    CompletionReview,
    CompletionReviewRequest,
    PreparedEvidence,
)
from .prompts import REVIEW_POLICY


def _content_parts(prepared_evidence: list[PreparedEvidence]) -> list[dict]:
    parts: list[dict] = []
    for item in prepared_evidence:
        marker = f"[evidence:{item.evidence_id}][{item.location}]"
        if item.text:
            parts.append({"type": "text", "text": marker + " " + item.text})
        if item.image_data_url:
            parts.append({"type": "text", "text": marker})
            parts.append({"type": "image_url", "image_url": {"url": item.image_data_url}})
    return parts


def _validate_review(request: CompletionReviewRequest, review: CompletionReview) -> None:
    criteria = request.task.completion_criteria
    if len(review.criteria_results) != len(criteria):
        raise AIError("SCHEMA_INVALID", "완료 기준 개수와 판정 결과 개수가 일치해야 합니다.")
    allowed = {item.evidence_id for item in request.evidence} | {SUBMISSION_DESCRIPTION_EVIDENCE_ID}
    statuses = []
    for index, (criterion, result) in enumerate(zip(criteria, review.criteria_results)):
        if result.criterion_index != index or result.criterion != criterion:
            raise AIError("SCHEMA_INVALID", "완료 기준의 순서와 원문을 바꾸지 마세요.")
        if any(ref.evidence_id not in allowed for ref in result.evidence_refs):
            raise AIError("SCHEMA_INVALID", "제공되지 않은 evidence_id를 참조하지 마세요.")
        if result.status == "MET" and not result.evidence_refs:
            raise AIError("SCHEMA_INVALID", "MET 판정에는 실제 근거가 필요합니다.")
        statuses.append(result.status)
    expected = "REVIEW_NEEDED" if "NOT_MET" in statuses else "NEEDS_INPUT" if "UNKNOWN" in statuses else "PASS"
    if review.decision != expected:
        raise AIError("SCHEMA_INVALID", f"기준별 상태에 따른 decision은 {expected}여야 합니다.")


def review_completion(
    request: CompletionReviewRequest,
    prepared_evidence: list[PreparedEvidence],
    runtime: Runtime | None = None,
    fresh: bool = False,
) -> Result[CompletionReview]:
    known = {item.evidence_id for item in request.evidence}
    if any(item.evidence_id not in known for item in prepared_evidence):
        raise ValueError("Prepared evidence must correspond to request evidence metadata")

    def check(review: CompletionReview):
        _validate_review(request, review)

    return (runtime or Runtime()).run(
        "M2.review",
        request,
        CompletionReview,
        REVIEW_POLICY,
        check=check,
        fresh=fresh,
        content_parts=_content_parts(prepared_evidence),
    )
