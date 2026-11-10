"""Run from project root. --check: no API/DB; --live: paid Timely request."""

import argparse, json
from pathlib import Path
from wemeet_coach.shared.contracts import GroupRequest, PersonalRequest
from wemeet_coach.group import group_coach
from wemeet_coach.personal import personal_coach


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--module", choices=["group", "personal"], default="group")
    args = parser.parse_args()
    schema, function = (
        (GroupRequest, group_coach)
        if args.module == "group"
        else (PersonalRequest, personal_coach)
    )
    filename = (
        "group-request.json" if args.module == "group" else "personal-request.json"
    )
    request = schema.model_validate_json(
        (Path(__file__).parent / filename).read_text(encoding="utf8")
    )
    if not args.live:
        print(
            "입력 계약 정상 · AI 호출 없음 · 구성원",
            len(request.members) if args.module == "group" else 1,
        )
        return
    result = function(request)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
