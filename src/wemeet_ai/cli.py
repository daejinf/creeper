"""Thin JSON command adapter; business behavior stays inside the modules."""
import argparse
import json
from pathlib import Path
from .shared.privacy import sanitize

def registry():
    from .m1.a_coaching import CoachRequest, coach_reply
    from .m1.b_analysis import AnalysisRequest, analyze_conversation
    from .m1.c_goal_draft import DraftRequest, draft_goal_spec
    from .m1.d_group_plan import ConfirmedGoal, generate_group_plan
    from .m1.e_member_plan import MemberCoachRequest, MemberPlanRequest, coach_member_goal, draft_member_goal, generate_member_plan
    from .m1.f_validation import ValidationRequest, RevisionRequest, validate_plan, revise_plan
    return {"A": (CoachRequest, coach_reply), "B": (AnalysisRequest, analyze_conversation), "C": (DraftRequest, draft_goal_spec),
            "D": (ConfirmedGoal, generate_group_plan), "E.coach": (MemberCoachRequest, coach_member_goal),
            "E.draft": (MemberCoachRequest, draft_member_goal), "E.plan": (MemberPlanRequest, generate_member_plan),
            "F": (ValidationRequest, validate_plan), "F.revise": (RevisionRequest, revise_plan)}

def execute(module, payload, *, runtime=None, fresh=False):
    request_type, function = registry()[module]
    request = request_type.model_validate(payload)
    return function(request) if module == "F" else function(request, runtime=runtime, fresh=fresh)

def main():
    parser = argparse.ArgumentParser(description="WE·MEET 독립 모듈 JSON 실행기. 실행은 유료 LIVE일 수 있습니다.")
    parser.add_argument("module", choices=[*registry(), "schemas"])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.module == "schemas":
        from .shared.contracts import Result, Plan, ValidationReport
        from .continuity import RecordView, PreparedInput, Artifact
        data = {"schema_version": "0.14.0", "requests": {k:t.model_json_schema() for k,(t,_) in registry().items()},
                "record_view": RecordView.model_json_schema(), "prepared_input": PreparedInput.model_json_schema(), "artifact": Artifact.model_json_schema(),
                "common_result": Result.model_json_schema(), "plan": Plan.model_json_schema(), "validation": ValidationReport.model_json_schema()}
    else:
        if not args.input: parser.error("--input request.json이 필요합니다.")
        try: data = execute(args.module, json.loads(args.input.read_text(encoding="utf-8"))).model_dump(mode="json")
        except ValueError as exc:
            from pydantic import ValidationError
            data = {"status": "FAILED", "error": {"code": "INPUT_INVALID", "message": "입력 형식을 확인하세요."}, "fields": [{"path": list(e["loc"]), "message": e["msg"]} for e in exc.errors()] if isinstance(exc, ValidationError) else []}
    output = json.dumps(sanitize(data), ensure_ascii=False, indent=2)
    if args.output: args.output.write_text(output, encoding="utf-8")
    else: print(output)

if __name__ == "__main__": main()
