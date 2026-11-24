"""Ed25519 signatures over confirmed todo revisions (SECURITY.md 6).

Why: the hidden evaluation criteria are seen by nobody (not even their owner) and an M2 PASS completes a todo without
a human (D-H1). The database already refuses changes to a confirmation (trigger, 0009/0012), but anyone who can turn
that trigger off (the owner role, the database host, a restored backup, or the app role through the
`moim.lifecycle_purge` switch) could rewrite a criterion silently. The private key lives only in the app's environment,
never in the database or a backup, so such a change can no longer carry a valid signature.

Key material:
  * `MOIM_SIGNING_KEY`: base64 of the 32-byte Ed25519 private seed. Required in development and production; a test
    process without it uses a fixed, public test key (never valid outside MOIM_ENV=test).
  * `MOIM_SIGNING_OLD_PUBLIC_KEYS`: optional comma-separated base64 32-byte public keys of retired keys, so records
    signed before a key rotation still verify.
A key id (`kid`) is the first 16 hex characters of SHA-256 over the raw public key. Keys never reach logs, responses
or the database; only the 64-byte signature and the kid are stored.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .config import ConfigError

CONTEXT = b"moim.todo-confirmation.v1\n"  # domain separation: this signature means nothing anywhere else
TEST_SEED = hashlib.sha256(b"moim public test signing key - never use outside MOIM_ENV=test").digest()

VALID, UNSIGNED, INVALID = "VALID", "UNSIGNED", "INVALID"


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(public: Ed25519PublicKey) -> str:
    return hashlib.sha256(_raw_public(public)).hexdigest()[:16]


def _b64_32(raw: str, name: str) -> bytes:
    try:
        data = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError(f"{name} is not valid base64") from None
    if len(data) != 32:
        raise ConfigError(f"{name} must decode to 32 bytes")
    return data


def new_private_key_b64() -> str:
    """A fresh key for `MOIM_SIGNING_KEY` (the launcher and operators use this)."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def public_key_b64(private_b64: str) -> str:
    """The public key of a `MOIM_SIGNING_KEY` value, for `MOIM_SIGNING_OLD_PUBLIC_KEYS` and the verify tool."""
    priv = Ed25519PrivateKey.from_private_bytes(_b64_32(private_b64, "MOIM_SIGNING_KEY"))
    return base64.b64encode(_raw_public(priv.public_key())).decode("ascii")


def canonical_confirmation(group_id: str, user_id: str, assignment_revision: int, confirmation_revision: int,
                           draft_revision: int, member_state_revision: int, items: Iterable[Mapping]) -> bytes:
    """The exact bytes signed for one confirmation: every todo of it, visible fields and hidden criteria together.
    Sorted keys, no whitespace, UTF-8, items in their confirmed position order."""
    rows = []
    for it in sorted(items, key=lambda i: i["position"]):
        due = it["due_date"]
        rows.append({
            "task_id": it["task_id"], "position": it["position"], "title": it["title"],
            "due_date": due.isoformat() if isinstance(due, date) else str(due), "part_id": it["part_id"],
            "deliverable": it["deliverable"], "completion_criteria": list(it["completion_criteria"]),
            "evidence_types": list(it["evidence_types"]), "estimated_minutes": it["estimated_minutes"],
        })
    body = {
        "group_id": group_id, "user_id": user_id, "assignment_revision": assignment_revision,
        "confirmation_revision": confirmation_revision, "draft_revision": draft_revision,
        "member_state_revision": member_state_revision, "items": rows,
    }
    return CONTEXT + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class Verifier:
    """Public keys only: verifying never needs (or can make) a signature. The verify tool uses this alone."""
    public_keys: Mapping[str, Ed25519PublicKey]

    @classmethod
    def from_b64(cls, keys: Iterable[str]) -> "Verifier":
        pubs = [Ed25519PublicKey.from_public_bytes(_b64_32(k, "public key")) for k in keys if k.strip()]
        return cls({key_id(p): p for p in pubs})

    def verify(self, payload: bytes, signature: bytes | None, kid: str | None) -> str:
        if signature is None and kid is None:
            return UNSIGNED  # confirmed before signing existed: reported apart, never re-signed
        key = self.public_keys.get(kid or "")
        if key is None or signature is None:
            return INVALID  # unknown key or half a signature: never trusted
        try:
            key.verify(bytes(signature), payload)
        except InvalidSignature:
            return INVALID
        return VALID


class Signer:
    def __init__(self, private_seed: bytes, old_public_keys: Iterable[Ed25519PublicKey] = ()):
        self._private = Ed25519PrivateKey.from_private_bytes(private_seed)
        public = self._private.public_key()
        self.kid = key_id(public)
        keys = {key_id(p): p for p in old_public_keys}
        keys[self.kid] = public
        self.verifier = Verifier(keys)

    def __repr__(self) -> str:  # never the key
        return f"Signer(kid={self.kid!r})"

    def sign(self, payload: bytes) -> bytes:
        return self._private.sign(payload)

    def verify(self, payload: bytes, signature: bytes | None, kid: str | None) -> str:
        return self.verifier.verify(payload, signature, kid)


def load_signer(env: str, environ: Mapping[str, str] | None = None) -> Signer:
    """Fails fast (ConfigError naming the variable, never its value) outside tests when the key is missing or bad."""
    environ = os.environ if environ is None else environ
    raw = (environ.get("MOIM_SIGNING_KEY") or "").strip()
    if not raw:
        if env != "test":
            raise ConfigError("MOIM_SIGNING_KEY is required (python -m moim.signing new-key)")
        seed = TEST_SEED
    else:
        seed = _b64_32(raw, "MOIM_SIGNING_KEY")
        if seed == TEST_SEED and env != "test":  # the test key is public: anyone could sign with it
            raise ConfigError("MOIM_SIGNING_KEY is the public test key; make a real one (python -m moim.signing new-key)")
    old = [k for k in (environ.get("MOIM_SIGNING_OLD_PUBLIC_KEYS") or "").split(",") if k.strip()]
    return Signer(seed, Verifier.from_b64(old).public_keys.values())


if __name__ == "__main__":  # python -m moim.signing new-key | public-key  (reads MOIM_SIGNING_KEY)
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "new-key":
        print(new_private_key_b64())
    elif cmd == "public-key":
        print(public_key_b64(os.environ.get("MOIM_SIGNING_KEY") or ""))
    else:
        raise SystemExit("usage: python -m moim.signing new-key | public-key")
