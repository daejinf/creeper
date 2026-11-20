"""Stage 7 deletion ledger (D-G1): what to delete again after a backup restore.

Backups are kept for 7 days (D-G1). A restore brings back the database as it was when the backup was taken, so every
group leave (D-D1 D6) and account deletion (D7) done after that moment would come back. The ledger is an append-only
record of those deletions (and of D5 `되돌리기`, which deletes a completion record and its files for good) kept OUTSIDE the database and outside the upload directory, so a restore never rolls it
back; after a restore, `tools/ops_db.py replay-deletions` runs them again (`LifecycleService.replay`).

What a line holds: an entry id, the time, the kind (`LEAVE` / `ACCOUNT_DELETED` / `RECORD_UNDONE` / `GROUP_DELETED`), the internal user
id and, where it applies, the group id and the record id. No name, username, text, file name or anything a person wrote. One file per UTC day
(`deletions-YYYYMMDD.jsonl`); files older than the retention are removed, so the ledger never holds more than the
backups it exists for.

Fail closed: the line is written (and fsynced) inside the deletion's transaction, just before the commit. If it
cannot be written the deletion is rolled back and the user sees an error, so no deletion is ever committed without
its ledger line. If the commit itself then fails, a cancel line for that entry id is appended (best effort) and the
replay skips it. Only a process that dies in the middle of the commit can leave an entry for a deletion that did not
commit; replaying it carries out a deletion the user had confirmed with their password.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..timeutil import utcnow

log = logging.getLogger("moim.lifecycle")

RETENTION_DAYS = 7
KINDS = ("LEAVE", "ACCOUNT_DELETED", "RECORD_UNDONE", "GROUP_DELETED")
NEEDS_GROUP = ("LEAVE", "RECORD_UNDONE", "GROUP_DELETED")  # GROUP_DELETED: a leader deleted the group (D-H1)
_FILE = re.compile(r"^deletions-(\d{8})\.jsonl$")
_ID = re.compile(r"^[0-9a-f-]{36}$")
_ENTRY = re.compile(r"^[0-9a-f]{32}$")


class LedgerUnavailable(RuntimeError):
    """The ledger line could not be written; the caller must not commit the deletion."""


class DeletionLedger:
    """`directory=None` disables the ledger (development and tests that do not exercise it). Production requires
    one (preflight `DELETION_LEDGER_*`)."""

    def __init__(self, directory: str | Path | None, retention_days: int = RETENTION_DAYS,
                 clock: Callable[[], datetime] = utcnow):
        self.directory = Path(directory) if directory else None
        self.retention_days = retention_days
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def _append(self, obj: dict) -> None:
        assert self.directory is not None
        now = datetime.fromisoformat(obj["ts"])
        with open(self.directory / f"deletions-{now:%Y%m%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def record(self, kind: str, user_id: str, group_id: str | None = None, record_id: str | None = None) -> str | None:
        """Append one line, flushed to disk; returns its entry id (None when disabled). Raises `LedgerUnavailable`
        (after an ERROR log with a fixed code and no ids) when it cannot be written."""
        if self.directory is None:
            return None
        if (kind not in KINDS or not _ID.match(user_id) or (group_id is not None and not _ID.match(group_id))
                or (kind in NEEDS_GROUP) != (group_id is not None)
                or (kind == "RECORD_UNDONE") != (record_id is not None)
                or (record_id is not None and not _ID.match(record_id))):
            raise ValueError("bad deletion ledger entry")
        entry = uuid.uuid4().hex
        now = self._clock().astimezone(timezone.utc)
        try:
            line = {"id": entry, "ts": now.isoformat(), "kind": kind, "user_id": user_id, "group_id": group_id}
            if record_id is not None:
                line["record_id"] = record_id
            self._append(line)
        except OSError:
            log.error("lifecycle_event kind=LEDGER_WRITE_FAILED entry_kind=%s", kind)
            raise LedgerUnavailable("deletion ledger unavailable") from None
        try:
            self.prune()
        except OSError:  # pruning is retried on the next write and by `ops_db.py prune`
            log.warning("lifecycle_event kind=LEDGER_PRUNE_FAILED")
        return entry

    def cancel(self, entry: str | None) -> None:
        """The deletion of `entry` did not commit. Best effort: an ERROR log when even this cannot be written."""
        if self.directory is None or not entry:
            return
        try:
            self._append({"ts": self._clock().astimezone(timezone.utc).isoformat(), "cancel": entry})
        except OSError:
            log.error("lifecycle_event kind=LEDGER_CANCEL_FAILED")

    def _files(self) -> list[tuple[datetime, Path]]:
        out = []
        if self.directory is None or not self.directory.is_dir():
            return out
        for p in self.directory.iterdir():
            m = _FILE.match(p.name)
            if m and p.is_file():
                out.append((datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc), p))
        return sorted(out)

    def prune(self) -> int:
        """Remove day files entirely older than the retention. Returns how many were removed."""
        cutoff = self._clock().astimezone(timezone.utc) - timedelta(days=self.retention_days)
        removed = 0
        for day, p in self._files():
            if day + timedelta(days=1) <= cutoff:
                p.unlink()
                removed += 1
        return removed

    def entries(self, since: datetime | None = None) -> list[dict]:
        """Every well-formed, not cancelled entry in time order (optionally at or after `since`). A torn last line
        (crash during a write) is skipped, never guessed."""
        found, cancelled = [], set()
        for _, p in self._files():
            for raw in p.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(raw)
                    ts = datetime.fromisoformat(e["ts"])
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(e.get("cancel"), str):
                    cancelled.add(e["cancel"])
                    continue
                if (not _ENTRY.match(str(e.get("id", ""))) or e.get("kind") not in KINDS
                        or not _ID.match(str(e.get("user_id", "")))):
                    continue
                if e["kind"] in NEEDS_GROUP and not _ID.match(str(e.get("group_id") or "")):
                    continue
                if e["kind"] == "RECORD_UNDONE" and not _ID.match(str(e.get("record_id") or "")):
                    continue
                if since is None or ts >= since:
                    found.append({"id": e["id"], "ts": ts, "kind": e["kind"], "user_id": e["user_id"],
                                  "group_id": e.get("group_id"), "record_id": e.get("record_id")})
        return sorted((e for e in found if e["id"] not in cancelled), key=lambda e: e["ts"])
