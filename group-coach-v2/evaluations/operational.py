"""Anonymous LIVE + real PostgreSQL deferred-commit fault, disposable review DB only."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from tools.review_database import configure
from tools.config import load
from wemeet_coach.persistence import Store
from wemeet_coach.persistence import schema as d
from wemeet_coach.m0.runtime import Runtime
from wemeet_coach.m0.tracing import JsonlTraceSink


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(
            "No AI or DB mutation. --live explicitly runs the anonymous isolated workflow."
        )
        return
    configure()
    load()
    url = os.environ["WEMEET_COACH_DATABASE_URL"]
    if not make_url(url).database.startswith("wemeet_coach_review5_"):
        raise ValueError("Disposable review database required")
    root = Path("runtime/improve5/operational")
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = root / "result.json"
    if checkpoint.exists():
        raise RuntimeError(
            "Existing operation evidence must not be overwritten or regenerated"
        )
    store = Store(url, root / "recovery", os.environ["WEMEET_COACH_RECOVERY_KEY"])
    runtime = Runtime(trace_sink=JsonlTraceSink(root / "traces.jsonl"))
    external = "anonymous-operational:" + str(uuid4())
    view = store.create(
        "0.3 LIVE · 커밋 실패·저장만 복구",
        [{"member_id": "one", "nickname": "익명 검토자", "external_user_id": external}],
        purpose="VALIDATION",
        reference_date="2026-11-07",
    )
    gid = view["group_id"]
    report = {
        "group_id": gid,
        "external_user_id": external,
        "anonymous_simulated_confirmation": True,
        "steps": [],
    }

    def save():
        checkpoint.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf8",
        )

    save()
    store.message(
        gid,
        "one",
        "11월 10일부터 24일까지 행사 안내 메인 화면 하나의 설계와 이동 흐름 검토 계획을 만들자. 내 역할은 메인 화면 초안과 자체 검토야. 완료 기준은 화면 초안의 메뉴와 이동 흐름을 확인하고 수정 기록을 남기는 것. 인증은 결과 화면과 짧은 설명이고 완료 여부만 공개해. 대신 제작하지 말고 내가 할 작업을 계획해줘.",
        str(uuid4()),
    )
    action = str(uuid4())
    report["pending_action_id"] = action
    save()
    result = store.execute(
        gid, action, "group.PROPOSE", {}, store.view(gid)["revision"], runtime=runtime
    )
    report["steps"].append(result)
    save()
    if result["status"] != "OK":
        return
    proposal = store.view(gid)["proposal"]
    store.vote(gid, proposal["id"], "one", True, "", "", str(uuid4()))
    role = proposal["body"]["goal"]["roles"]["one"]
    store.review_conditions(
        gid,
        "one",
        {
            "level": {"value": "일부 경험", "status": "KNOWN"},
            "weekly_minutes": {"value": 120, "status": "KNOWN"},
            "scope": {"value": role, "status": "KNOWN"},
            "tools": {"value": ["Figma"], "status": "KNOWN"},
            "progress": {"value": "자료는 있고 초안 작성 전", "status": "KNOWN"},
        },
        str(uuid4()),
    )
    real_event = store.event

    def fault(c, group_id, kind, body, **kwargs):
        value = real_event(c, group_id, kind, body, **kwargs)
        if group_id == gid and kind == "AI_REPLY":
            # Fails at the real PostgreSQL COMMIT, after plan/task inserts.
            c.execute(
                d.rooms.update()
                .where(d.rooms.c.id == gid)
                .values(approved_proposal_id="nonexistent-review-proposal")
            )
        return value

    action = str(uuid4())
    report["pending_action_id"] = action
    save()
    store.event = fault
    result = store.execute(
        gid,
        action,
        "personal.PLAN",
        {},
        store.view(gid)["revision"],
        owner="one",
        runtime=runtime,
    )
    store.event = real_event
    report["steps"].append(result)
    report["commit_failed_without_success_claim"] = (
        result.get("persistence", {}).get("state") == "SAVE_PENDING"
    )
    save()
    if not report["commit_failed_without_success_claim"]:
        return
    with store.read(gid) as (c, row):
        report["no_half_saved_plans"] = (
            c.scalar(
                sa.select(sa.func.count())
                .select_from(d.plans)
                .where(d.plans.c.group_id == gid, d.plans.c.owner == "one")
            )
            == 0
        )
    recovered = store.retry_save(gid, action, owner="one")
    report["steps"].append(recovered)
    save()
    if (
        recovered["status"] != "OK"
        or recovered.get("persistence", {}).get("state") != "COMPLETE"
    ):
        return
    plan = recovered["data"]["plan"]
    report["plan_version_id"] = plan["plan_version_id"]
    repeated = store.retry_save(gid, action, owner="one")
    report["repeat_no_http"] = (
        repeated["new_http"] == 0
        and repeated["meta"]["run_id"] == recovered["meta"]["run_id"]
    )
    store.publish_personal(gid, "one", plan["plan_version_id"], str(uuid4()))
    context = store.reader({"external_user_id": external}).context(
        gid, scope="PERSONAL"
    )
    report["context"] = context
    report["ready"] = context["readiness"] == "READY"
    report["restored_plan_id_matches"] = (
        context["plan_version_id"] == plan["plan_version_id"]
    )
    task = context["tasks"][0]
    report["baseline_lookup"] = store.reader({"external_user_id": external}).baseline(
        gid, context["baseline_id"], context["plan_version_id"], task["task_id"]
    )
    unique = {
        step.get("meta", {}).get("run_id"): step.get("meta", {})
        for step in report["steps"]
        if step.get("meta")
    }
    report["actual_http"] = sum(
        meta.get("transport_attempts", 0) for meta in unique.values()
    )
    report["actual_tokens"] = sum(
        meta.get("usage", {}).get("total_tokens", 0) for meta in unique.values()
    )
    save()
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "ready",
                    "no_half_saved_plans",
                    "repeat_no_http",
                    "restored_plan_id_matches",
                    "actual_http",
                    "actual_tokens",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
