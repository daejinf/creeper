from types import SimpleNamespace

import pytest

from wemeet_ai.cli import registry
from wemeet_ai.m0 import Runtime
from wemeet_ai.m2 import CompletionReviewRequest, PreparedEvidence, review_completion


class FixtureClient:
    model = "fixture-model"
    base_url = "fixture://timely"
    is_fixture = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, messages, output_type, options=None, *, stats=None):
        self.calls.append(messages)
        stats.transport_attempts += 1
        stats.actual_model = self.model
        return SimpleNamespace(parsed_output=output_type.model_validate(self.responses.pop(0)))


def request_payload(criteria=None, *, evidence=True):
    criteria = criteria or ["최종 화면에 결과가 표시된다", "요약 문장이 포함된다"]
    return CompletionReviewRequest.model_validate({
        "submission_id": "submission-1",
        "baseline_id": "baseline-1",
        "group_id": "group-1",
        "plan_version_id": "plan-1",
        "task_id": "task-1",
        "owner_member_id": "member-1",
        "task": {
            "title": "결과 정리",
            "deliverable": "결과 이미지와 요약",
            "completion_criteria": criteria,
            "due_date": "2026-11-20",
            "estimated_minutes": 30,
            "difficulty": "MEDIUM",
        },
        "submission": {"description": "결과 화면과 요약을 제출했습니다.", "submitted_at": "2026-11-16T10:00:00+09:00"},
        "evidence": [{
            "evidence_id": "ev-1", "original_name": "result.png", "mime_type": "image/png",
            "size_bytes": 100, "sha256": "a" * 64, "content_kind": "IMAGE",
            "extracted_character_count": 0, "image_count": 1,
        }] if evidence else [],
    })


def review_payload(statuses, decision, *, evidence_id="ev-1"):
    criteria = ["최종 화면에 결과가 표시된다", "요약 문장이 포함된다"]
    return {
        "decision": decision,
        "summary": "기준별 검토 결과입니다.",
        "criteria_results": [{
            "criterion_index": index,
            "criterion": criterion,
            "status": status,
            "reason": "제출 자료에서 확인했습니다." if status == "MET" else "확인 자료가 충분하지 않습니다.",
            "evidence_refs": [{"evidence_id": evidence_id, "location": "image", "description": "제출 자료"}] if status == "MET" else [],
        } for index, (criterion, status) in enumerate(zip(criteria, statuses))],
        "missing_items": [] if decision == "PASS" else ["추가 확인 자료"],
        "user_message": "검토가 완료되었습니다.",
    }


@pytest.mark.parametrize("statuses,decision", [
    (["MET", "MET"], "PASS"),
    (["MET", "NOT_MET"], "REVIEW_NEEDED"),
    (["MET", "UNKNOWN"], "NEEDS_INPUT"),
])
def test_review_completion_decisions(statuses, decision):
    client = FixtureClient([review_payload(statuses, decision)])
    result = review_completion(request_payload(), [], runtime=Runtime(client))
    assert result.status == "OK" and result.data.decision == decision


def test_review_repairs_inconsistent_decision_once():
    wrong = review_payload(["MET", "UNKNOWN"], "PASS")
    corrected = review_payload(["MET", "UNKNOWN"], "NEEDS_INPUT")
    client = FixtureClient([wrong, corrected])
    result = review_completion(request_payload(), [], runtime=Runtime(client))
    assert result.status == "OK" and result.data.decision == "NEEDS_INPUT"
    assert len(client.calls) == 2 and result.meta.repair_attempts == 1


def test_review_rejects_unknown_evidence_id_after_repair():
    invalid = review_payload(["MET", "MET"], "PASS", evidence_id="invented")
    client = FixtureClient([invalid, invalid])
    result = review_completion(request_payload(), [], runtime=Runtime(client))
    assert result.status == "FAILED" and result.error.code == "SCHEMA_INVALID"


def test_review_sends_image_content_part():
    client = FixtureClient([review_payload(["MET", "MET"], "PASS")])
    prepared = [PreparedEvidence(
        evidence_id="ev-1", location="image", image_data_url="data:image/png;base64,AA=="
    )]
    result = review_completion(request_payload(), prepared, runtime=Runtime(client))
    content = client.calls[0][-1]["content"]
    assert result.status == "OK"
    assert any(part.get("type") == "image_url" for part in content)


def test_cli_registers_m2_review():
    assert "M2.review" in registry()
