"""DB-backed fixed-window counters with a lock. Shared by every process, so a restart does not reset them.

Keys are `<scope>:<sha256 prefix>`; raw usernames and addresses are never stored.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..timeutil import utcnow


@dataclass(frozen=True)
class Rule:
    scope: str
    limit: int  # events allowed in one window; the event that reaches the limit locks the key
    window_s: int
    lock_s: int


# Login failures are counted per (username, address), per address and per username. Check/signup are plain rate limits.
LOGIN_PAIR = Rule("login-pair", 5, 15 * 60, 15 * 60)
LOGIN_IP = Rule("login-ip", 20, 15 * 60, 15 * 60)
LOGIN_USER = Rule("login-user", 15, 15 * 60, 15 * 60)
CHECK_IP = Rule("check-ip", 30, 10 * 60, 10 * 60)
SIGNUP_IP = Rule("signup-ip", 10, 60 * 60, 60 * 60)
# Invite codes are six digits: wrong guesses (not known-but-closed codes) are counted per user and per address.
JOIN_USER = Rule("join-user", 10, 15 * 60, 15 * 60)
JOIN_IP = Rule("join-ip", 30, 15 * 60, 15 * 60)
# Stage 6: a wrong current password (password change, account deletion) is counted per signed-in user.
REAUTH_USER = Rule("reauth-user", 10, 15 * 60, 15 * 60)

_HIT = text(
    """
    insert into auth_throttle (key, window_start, count, locked_until)
    values (:key, :now, 1, case when cast(:limit as integer) <= 1 then cast(:lock_until as timestamptz) end)
    on conflict (key) do update set
      window_start = case when auth_throttle.window_start <= :cutoff then :now else auth_throttle.window_start end,
      count = case when auth_throttle.window_start <= :cutoff then 1 else auth_throttle.count + 1 end,
      locked_until = case
        when (case when auth_throttle.window_start <= :cutoff then 1 else auth_throttle.count + 1 end)
             >= cast(:limit as integer) then cast(:lock_until as timestamptz)
        else auth_throttle.locked_until end
    returning count, locked_until
    """
)


def key_for(scope: str, ident: str) -> str:
    return f"{scope}:{hashlib.sha256(f'{scope}|{ident}'.encode()).hexdigest()[:40]}"


class Throttle:
    def __init__(self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = utcnow):
        self._sf = session_factory
        self._clock = clock

    def hit(self, rule: Rule, ident: str) -> int:
        """Count one event. Returns seconds the key is locked for (0 = not locked)."""
        now = self._clock()
        with self._sf.begin() as s:
            row = s.execute(
                _HIT,
                {
                    "key": key_for(rule.scope, ident),
                    "now": now,
                    "cutoff": now - timedelta(seconds=rule.window_s),
                    "limit": rule.limit,
                    "lock_until": now + timedelta(seconds=rule.lock_s),
                },
            ).one()
        return _remaining(row.locked_until, now)

    def locked_for(self, keys: Sequence[tuple[Rule, str]]) -> int:
        """Longest remaining lock among the given keys, without counting an event."""
        now = self._clock()
        names = [key_for(rule.scope, ident) for rule, ident in keys]
        with self._sf() as s:
            rows = s.execute(
                text("select locked_until from auth_throttle where key = any(:names) and locked_until > :now"),
                {"names": names, "now": now},
            ).all()
        return max((_remaining(r.locked_until, now) for r in rows), default=0)

    def clear(self, rule: Rule, ident: str) -> None:
        with self._sf.begin() as s:
            s.execute(text("delete from auth_throttle where key = :key"), {"key": key_for(rule.scope, ident)})

    def purge(self) -> None:
        now = self._clock()
        with self._sf.begin() as s:
            s.execute(
                text(
                    "delete from auth_throttle where window_start < :old "
                    "and (locked_until is null or locked_until < :now)"
                ),
                {"old": now - timedelta(days=1), "now": now},
            )


def _remaining(locked_until: datetime | None, now: datetime) -> int:
    if locked_until is None or locked_until <= now:
        return 0
    return max(1, int((locked_until - now).total_seconds()) + 1)
