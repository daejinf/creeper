"""Server-side sessions. The cookie carries a random token; the DB keeps only its sha256."""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..models import auth_sessions, users
from ..timeutil import utcnow

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # token_urlsafe(32)

# Without "keep": browser-session cookie, 2 h idle, 12 h absolute. With "keep": 14 days idle, 30 days absolute.
POLICY = {
    False: (timedelta(hours=2), timedelta(hours=12)),
    True: (timedelta(days=14), timedelta(days=30)),
}
TOUCH_AFTER = timedelta(seconds=60)  # slide the idle expiry at most once a minute


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SessionUser:
    user_id: str
    username: str
    display_name: str
    keep: bool


class SessionStore:
    def __init__(self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = utcnow):
        self._sf = session_factory
        self._clock = clock

    def create(self, user_id: str, keep: bool) -> tuple[str, int | None]:
        """New session with a fresh token. Returns (token, cookie max-age seconds or None for a browser-session cookie)."""
        token = secrets.token_urlsafe(32)
        idle, absolute = POLICY[bool(keep)]
        now = self._clock()
        with self._sf.begin() as s:
            s.execute(
                auth_sessions.insert().values(
                    token_hash=hash_token(token),
                    user_id=user_id,
                    keep=bool(keep),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + idle,
                    absolute_expires_at=now + absolute,
                )
            )
            s.execute(delete(auth_sessions).where(auth_sessions.c.absolute_expires_at < now))  # lazy cleanup
        # The cookie lives as long as the absolute cap; the server enforces the shorter sliding idle expiry.
        return token, (int(absolute.total_seconds()) if keep else None)

    def lookup(self, token: str | None) -> SessionUser | None:
        if not token or not TOKEN_RE.fullmatch(token):
            return None
        th = hash_token(token)
        now = self._clock()
        with self._sf.begin() as s:
            row = s.execute(
                select(
                    auth_sessions.c.keep,
                    auth_sessions.c.last_seen_at,
                    auth_sessions.c.expires_at,
                    auth_sessions.c.absolute_expires_at,
                    users.c.id,
                    users.c.username,
                    users.c.display_name,
                )
                .join(users, users.c.id == auth_sessions.c.user_id)
                .where(auth_sessions.c.token_hash == th)
            ).first()
            if row is None:
                return None
            if row.expires_at <= now or row.absolute_expires_at <= now:
                s.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == th))
                return None
            if now - row.last_seen_at >= TOUCH_AFTER:
                idle, _ = POLICY[row.keep]
                s.execute(
                    update(auth_sessions)
                    .where(auth_sessions.c.token_hash == th)
                    .values(last_seen_at=now, expires_at=min(now + idle, row.absolute_expires_at))
                )
            return SessionUser(row.id, row.username, row.display_name, row.keep)

    def revoke(self, token: str | None) -> None:
        if not token or not TOKEN_RE.fullmatch(token):
            return
        with self._sf.begin() as s:
            s.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == hash_token(token)))

    def revoke_all(self, user_id: str) -> None:
        with self._sf.begin() as s:
            s.execute(delete(auth_sessions).where(auth_sessions.c.user_id == user_id))
