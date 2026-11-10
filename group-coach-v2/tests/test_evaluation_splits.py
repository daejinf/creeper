"""Evaluation infrastructure must not leak holdouts or duplicate paid inputs."""

import json
from evaluations.improvement import MANIFEST, select_cases


def test_frozen_cases_have_disjoint_shards_and_protected_inputs():
    data = json.loads(MANIFEST.read_bytes())
    for label, total in [
        ("baseline", 60),
        ("round1", 60),
        ("round4", 60),
        ("round5", 80),
    ]:
        left = {c["id"] for c in select_cases(data, label, 0, 2)}
        right = {c["id"] for c in select_cases(data, label, 1, 2)}
        assert not left & right and len(left | right) == total
        assert label == "round5" or not any(
            c["holdout"] for c in select_cases(data, label)
        )


def test_comparison_pairs_stay_in_the_same_stage_and_domain():
    from collections import defaultdict

    data = json.loads(MANIFEST.read_bytes())
    pairs = defaultdict(list)
    for case in data["cases"]:
        if case["pair_id"]:
            pairs[case["pair_id"]].append(case)
    assert len(pairs) == 30
    assert all(
        len(rows) == 2
        and len({r["stage"] for r in rows}) == 1
        and len({r["domain"] for r in rows}) == 1
        for rows in pairs.values()
    )


def test_group_quality_review_never_receives_hidden_personal_fixture():
    from evaluations.improvement import review_input

    case = {
        "id": "group",
        "stage": "COACH",
        "domain": "test",
        "expected": [],
        "forbidden": [],
        "personal_fixture": {"weekly_minutes": 60, "secret": "PRIVATE_CANARY"},
    }
    result = {
        "status": "OK",
        "steps": {
            "COACH": {
                "request": {"conversation": ["공개 내용"]},
                "result": {
                    "status": "OK",
                    "data": {"reply": "공동 안내"},
                    "meta": {"model": "VERSION_HINT"},
                },
            }
        },
    }
    encoded = json.dumps(review_input(case, result), ensure_ascii=False)
    assert "PRIVATE_CANARY" not in encoded and "weekly_minutes" not in encoded
    assert "VERSION_HINT" not in encoded and "공개 내용" in encoded
