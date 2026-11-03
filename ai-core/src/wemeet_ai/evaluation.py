"""Small explicit LIVE smoke suite; fixture conversion does not claim model accuracy."""
from .analysis import local_analysis
from .contracts import PlanBundle
from .examples import example_input, regression_conversation


def inspect_legacy(root):
    import json
    from pathlib import Path
    report = {}
    for name in ("eval_cases.jsonl", "coaching_eval_cases.jsonl"):
        path = Path(root) / name
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        report[name] = {
            "case_count": len(rows),
            "reusable_conversations": sum(bool(row.get("turns") or row.get("request", {}).get("kickoff_messages")) for row in rows),
            "requires_relabeling": len(rows),
            "reason": "Old automatic-generation and next_action labels differ from explicit human confirmation and the new module contracts.",
            "categories": sorted({str(row.get("category", row.get("expected_status", "unlabeled"))) for row in rows}),
        }
    return report


def evaluate_examples(ai, *, live=False, legacy_root=None, resume_path=None):
    regression = regression_conversation()
    local = local_analysis(regression)
    values = [c.value for c in local.fields["timeline"].candidates]
    checks = {
        "durations_preserved": {"duration_days": 14} in values and {"duration_days": 21} in values,
        "calendar_range_preserved": {"start_date": "2026-11-01", "end_date": "2026-11-09"} in values,
        "frequency": any(c.value == {"per_week": 3} for c in local.fields["frequency"].candidates),
        "verification": bool(local.fields["verification"].candidates),
        "missing_privacy": local.fields["privacy"].status == "MISSING",
    }
    cases = []
    previous = {}
    if resume_path:
        import json
        from pathlib import Path
        previous = {c["case_id"]: c for c in json.loads(Path(resume_path).read_text(encoding="utf-8"))["cases"]}
    if live:
        if previous.get("user-regression", {}).get("passed"):
            cases.append(previous["user-regression"])
        else:
            analysis = ai.analyze_conversation(regression)
            cases.append({"case_id": "user-regression", "passed": analysis.status == "OK" and all(checks.values()), "result": analysis.model_dump(mode="json")})
        from .contracts import CoachRequest
        coached = ai.coach_reply(CoachRequest(**regression.model_dump()))
        cases.append({"case_id": "coach-smoke", "passed": coached.status == "OK", "result": coached.model_dump(mode="json")})
        for name in ("project", "study", "exercise"):
            if previous.get(name, {}).get("passed"):
                cases.append(previous[name]); continue
            prepared = ai.prepare_goal_spec(example_input(name))
            saved = previous.get(name, {}).get("result", {})
            if "group_plan" in saved:
                existing = PlanBundle.model_validate(saved)
                bundle = ai.generate_bundle(existing.goal_spec, existing)
            else:
                bundle = ai.generate_bundle(prepared.data) if prepared.status == "OK" else prepared
            cases.append({"case_id": name, "passed": isinstance(bundle, PlanBundle) and bundle.validation.valid and not bundle.retry_member_ids, "result": bundle.model_dump(mode="json")})
    return {"mode": "LIVE" if live else "LOCAL_CHECKS_ONLY", "passed": all(checks.values()) and all(c["passed"] for c in cases), "local_checks": checks, "cases": cases, "legacy_compatibility": inspect_legacy(legacy_root) if legacy_root else None}
