"""Check every confirmed todo revision against its Ed25519 signature (SECURITY.md 6). Read-only.

  python tools/verify_signatures.py --url <database url> [--public-keys <b64>,<b64>...]

Public keys come from `--public-keys` or, when omitted, are derived from MOIM_SIGNING_KEY (plus
MOIM_SIGNING_OLD_PUBLIC_KEYS). Only public keys are needed: whoever runs this can check signatures but cannot make one.
Run it after a backup restore (OPERATIONS.md 4) or whenever a confirmation is suspected of having changed.

Output (JSON, ids only, never a title, criterion or key): counts of VALID / UNSIGNED / INVALID and the ids of every
INVALID confirmation. Exit code 0 = no INVALID, 1 = at least one INVALID, 2 = usage or connection error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from moim import signing  # noqa: E402
from moim.config import ConfigError, parse_database_url  # noqa: E402
from moim.models import todo_confirmations  # noqa: E402
from moim.roles.todos import confirmation_integrity  # noqa: E402


def verifier_from(args) -> signing.Verifier:
    if args.public_keys:
        return signing.Verifier.from_b64(args.public_keys.split(","))
    key = os.environ.get("MOIM_SIGNING_KEY") or ""
    if not key.strip():
        raise ConfigError("give --public-keys or set MOIM_SIGNING_KEY")
    old = (os.environ.get("MOIM_SIGNING_OLD_PUBLIC_KEYS") or "").split(",")
    return signing.Verifier.from_b64([signing.public_key_b64(key), *old])


def run(url: str, verifier: signing.Verifier) -> dict:
    counts = {signing.VALID: 0, signing.UNSIGNED: 0, signing.INVALID: 0}
    invalid = []
    engine = create_engine(url)
    try:
        with Session(engine) as s:
            heads = s.execute(select(todo_confirmations.c.group_id, todo_confirmations.c.user_id,
                                     todo_confirmations.c.source_assignment_revision, todo_confirmations.c.revision)
                              .order_by(todo_confirmations.c.group_id, todo_confirmations.c.user_id,
                                        todo_confirmations.c.source_assignment_revision,
                                        todo_confirmations.c.revision)).all()
            for h in heads:
                result = confirmation_integrity(s, verifier, h.group_id, h.user_id, h.source_assignment_revision, h.revision)
                counts[result] += 1
                if result == signing.INVALID:
                    invalid.append({"group_id": h.group_id, "user_id": h.user_id,
                                    "assignment_revision": h.source_assignment_revision, "confirmation_revision": h.revision})
    finally:
        engine.dispose()
    return {"counts": counts, "invalid": invalid, "key_ids": sorted(verifier.public_keys)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True)
    p.add_argument("--public-keys", default="")
    args = p.parse_args(argv)
    try:
        url = parse_database_url(args.url, "--url")
        result = run(url, verifier_from(args))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never echo the URL (it may hold a password)
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["counts"][signing.INVALID] else 0


if __name__ == "__main__":
    raise SystemExit(main())
