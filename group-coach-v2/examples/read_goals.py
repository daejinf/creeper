"""SW-authenticated read adapter; no AI calls. Run from the project directory.

python examples/read_goals.py --user-id <SW-verified-id> [--group-id <id>]
The CLI argument is for trusted local integration testing, not authentication.
"""

import argparse
import json
import os
from pathlib import Path
from wemeet_coach import ActorContext
from wemeet_coach.persistence import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--group-id")
    parser.add_argument("--scope", choices=["GROUP", "PERSONAL"], default="GROUP")
    parser.add_argument("--plan-version-id")
    args = parser.parse_args()
    store = Store(
        os.environ["WEMEET_COACH_DATABASE_URL"],
        Path("runtime/recovery"),
        os.environ["WEMEET_COACH_RECOVERY_KEY"],
    )
    reader = store.reader(ActorContext(external_user_id=args.user_id))
    result = reader.groups()
    if args.group_id:
        result = reader.context(
            args.group_id, scope=args.scope, plan_version_id=args.plan_version_id
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
