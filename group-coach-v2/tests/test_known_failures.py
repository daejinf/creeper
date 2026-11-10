"""Known 0.3.0 acceptance blockers, preserved from anonymous LIVE output.

These are expected failures, not a passing quality result. README/report explicitly
hold production acceptance. No live HTTP is used to reproduce them.
"""

import json
from pathlib import Path
import pytest
from wemeet_coach import personal_coach
from wemeet_coach.m0.runtime import code_result


def reproduce():
    case = json.loads(
        (Path(__file__).parents[1] / "evaluations/regressions-030.json").read_text(
            encoding="utf8"
        )
    )["cases"][0]

    class RecordedProvider:
        def run(self, task, request, schema, instructions, check=None, **kwargs):
            out = schema.model_validate(case["provider_output"])
            check(out)
            return code_result(task, out)

    return case, personal_coach(case["request"], runtime=RecordedProvider())


@pytest.mark.xfail(
    strict=True,
    reason="P1: adding a constraint currently replaces an unrelated known constraint",
)
def test_new_constraint_preserves_previous_confirmed_constraint():
    case, out = reproduce()
    old = set(case["request"]["state"]["fields"]["constraints"]["value"])
    assert old <= set(out.data.state.fields["constraints"].value)


@pytest.mark.xfail(
    strict=True,
    reason="P1: a code-blocked plan still carries the model plan-written reply",
)
def test_blocked_plan_does_not_claim_a_completed_draft():
    case, out = reproduce()
    assert out.status == "NEEDS_INPUT" and out.data.plan is None
    assert "계획을 작성했습니다" not in out.data.reply
