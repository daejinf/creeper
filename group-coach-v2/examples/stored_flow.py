"""Explicit LIVE/DB integration. Anonymous simulated votes, not real consent.
Run from root after configuration: python examples/stored_flow.py --live
Rerun with --group-id <saved id> to inspect/continue without recreating the room.
"""

import argparse, json, os
from pathlib import Path
from uuid import uuid4
from wemeet_coach.persistence import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--group-id")
    parser.add_argument(
        "--resume-action-id", help="저장된 동작의 저장만 재시도합니다. AI 호출 없음"
    )
    args = parser.parse_args()
    if not args.live and not args.group_id:
        print(
            "안내만 표시 · 실행은 --live · 실제 PostgreSQL 저장과 유료 AI 호출을 사용합니다."
        )
        return
    store = Store(
        os.environ["WEMEET_COACH_DATABASE_URL"],
        Path("runtime/example-recovery"),
        os.environ["WEMEET_COACH_RECOVERY_KEY"],
    )
    if args.resume_action_id:
        if not args.group_id:
            parser.error("--resume-action-id에는 --group-id가 필요합니다")
        print(
            json.dumps(
                store.retry_save(args.group_id, args.resume_action_id),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.group_id:
        print(json.dumps(store.view(args.group_id), ensure_ascii=False, indent=2))
        return
    members = [
        {"member_id": "example-a", "nickname": "민재"},
        {"member_id": "example-b", "nickname": "서연"},
        {"member_id": "example-c", "nickname": "도윤"},
    ]
    for member in members:
        member["external_user_id"] = "anonymous-sw-example:" + member["member_id"]
    group = store.create("SW 연결 예제 · 모의 확인", members, purpose="EXAMPLE")
    gid = group["group_id"]
    print("저장된 group_id:", gid, " / 중단 후 --group-id로 복원 가능")
    store.message(
        gid,
        "example-a",
        "2026-11-09~2026-11-22에 행사 안내 화면 2개를 시연하는 준비 계획을 만들자. 민재는 메인, 서연은 상세 화면, 도윤은 테스트. 결과 화면으로 인증하고 완료 여부만 공개해. 실제 코드를 대신 만드는 것이 아닌 실행 계획이 필요해.",
        str(uuid4()),
    )
    # Persist this action ID in SW's request/job record BEFORE sending.
    action_id = str(uuid4())
    checkpoint = Path("runtime/example-checkpoint.json")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(
        json.dumps({"group_id": gid, "action_id": action_id, "owner_member_id": None}),
        encoding="utf8",
    )
    result = store.execute(
        gid, action_id, "group.PROPOSE", {}, store.view(gid)["revision"]
    )
    if result["status"] != "OK" or result["persistence"]["state"] != "COMPLETE":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("동일 action_id로 저장/조회 재시도:", action_id)
        return
    proposal = store.view(gid)["proposal"]
    print("제안:", proposal["body"]["goal"]["title"])
    # DEMO ONLY: replace this loop with real authenticated members' explicit votes.
    for member in members:
        store.vote(gid, proposal["id"], member["member_id"], True, "", "", str(uuid4()))
    print("모의 전원 찬성:", store.view(gid)["approved_proposal_id"])
    owner = "example-a"
    # In a real UI use personal.TALK and return the question_id/option_id from its reply.
    # Here the anonymous scenario explicitly supplies owner-confirmed conditions.
    store.review_conditions(
        gid,
        owner,
        {
            "level": {"status": "KNOWN", "value": "조금 해봤어요"},
            "weekly_minutes": {"status": "KNOWN", "value": 120},
            "scope": {"status": "KNOWN", "value": "메인 화면 구성과 시연 준비"},
        },
        str(uuid4()),
    )
    result = store.execute(
        gid, str(uuid4()), "personal.PLAN", {}, store.view(gid)["revision"], owner=owner
    )
    if result["status"] != "OK" or result["persistence"]["state"] != "COMPLETE":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    view = store.personal_view(gid, owner)
    plan = next(p for p in view["plans"] if p["id"] == view["current_plan_id"])
    print("본인 전용 초안:", json.dumps(plan["body"], ensure_ascii=False, indent=2))
    # OWNER CONFIRMATION POINT: never call publish automatically in a real service.
    if input("익명 예제의 공개 요약을 모의 확인하려면 yes: ").strip() != "yes":
        return
    baseline = store.publish_personal(gid, owner, plan["id"], str(uuid4()))
    task = plan["body"]["tasks"][0]
    print(
        "M2 연결:",
        store.reader({"external_user_id": "anonymous-sw-example:" + owner}).baseline(
            gid, baseline["baseline_id"], plan["id"], task["task_id"]
        ),
    )
    print(
        "SW는 public view와 personal_view를 각각 올바른 권한으로 표시합니다. 비공개 파일을 그룹에 내보내지 마세요."
    )


if __name__ == "__main__":
    main()
