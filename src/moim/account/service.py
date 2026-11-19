"""Stage 6 mypage data: my info, my name, my password, and the groups I am in.

The user id always comes from the session; there is no route that takes another user's id. The username is shown
only to its owner. Group rows are my own progress only (my confirmed todos), never another member's.
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from typing import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth import passwords
from ..auth.errors import AuthError
from ..auth.validation import FieldError, validate_name, validate_password
from ..groups.service import ACTIVE, active_cohort_query, today_kst
from ..models import execution_records, group_members, groups, lifecycle_events, project_briefs, todo_confirmed_items, users
from ..timeutil import utcnow

log = logging.getLogger("moim.account")

NAME_MIN = 2  # the original mypage rule ("이름은 2자 이상으로 정해 주세요.")
MSG_NAME_SHORT = "이름은 2자 이상으로 정해 주세요."
MSG_PW_WRONG = "현재 비밀번호가 맞지 않아요."
MSG_PW_MISMATCH = "비밀번호가 서로 달라요."
MSG_CHANGED = "다른 곳에서 비밀번호가 먼저 바뀌었어요. 다시 시도해 주세요."
GROUP_SHAPES = ("a", "c", "q")  # the original list's dot shapes, in order


class AccountError(AuthError):
    """Same JSON shape as AuthError."""


class AccountService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = utcnow):
        self._sf = session_factory
        self._clock = clock

    def overview(self, user_id: str) -> dict:
        today = today_kst(self._clock())
        from ..roles.todos import TodoService

        with self._sf() as s:
            me = s.execute(select(users.c.username, users.c.display_name).where(users.c.id == user_id)).one()
            rows = s.execute(
                select(groups)
                .join(group_members, group_members.c.group_id == groups.c.id)
                .where(group_members.c.user_id == user_id, groups.c.status == ACTIVE)
                .order_by(groups.c.activated_at.desc(), groups.c.id)
            ).all()
            out = []
            for i, g in enumerate(rows):
                brief = s.execute(select(project_briefs.c.name, project_briefs.c.end_date).where(
                    project_briefs.c.group_id == g.id, project_briefs.c.revision == g.brief_revision)).one()
                members = s.execute(select(func.count()).select_from(active_cohort_query(g.id, g.cohort_revision).subquery())).scalar_one()
                done = total = due_today = 0
                shown = TodoService.shown_confirmations(s, g.id).get(user_id)
                if shown is not None:
                    items = s.execute(select(todo_confirmed_items.c.task_id, todo_confirmed_items.c.due_date).where(
                        todo_confirmed_items.c.group_id == g.id, todo_confirmed_items.c.user_id == user_id,
                        todo_confirmed_items.c.source_assignment_revision == shown[0],
                        todo_confirmed_items.c.confirmation_revision == shown[1])).all()
                    finished = set(s.execute(select(execution_records.c.task_id).where(
                        execution_records.c.group_id == g.id, execution_records.c.user_id == user_id,
                        execution_records.c.marks_done)).scalars())
                    total = len(items)
                    done = sum(1 for it in items if it.task_id in finished)
                    due_today = sum(1 for it in items if it.task_id not in finished and it.due_date == today)
                out.append({"name": brief.name, "shape": GROUP_SHAPES[i % len(GROUP_SHAPES)], "members": members,
                            "days_left": (brief.end_date - today).days, "done": done, "total": total, "today": due_today})
        return {"username": me.username, "name": me.display_name, "groups": out}

    def update_name(self, user_id: str, raw: str) -> str:
        name = validate_name(raw)  # same rule as signup (length, control characters)
        if len(unicodedata.normalize("NFC", name)) < NAME_MIN:
            raise FieldError("name", MSG_NAME_SHORT)
        with self._sf.begin() as s:
            s.execute(update(users).where(users.c.id == user_id, users.c.deleted_at.is_(None)).values(display_name=name))
            s.execute(lifecycle_events.insert().values(kind="PROFILE_UPDATED", user_id=user_id, created_at=self._clock()))
        log.info("account_event kind=PROFILE_UPDATED user_id=%s", user_id)
        return name

    def password_hash(self, user_id: str) -> str | None:
        with self._sf() as s:
            return s.execute(select(users.c.password_hash).where(
                users.c.id == user_id, users.c.deleted_at.is_(None))).scalar_one_or_none()

    def check_password(self, user_id: str, password: str) -> tuple[bool, str | None]:
        """(valid, the hash that was checked). Over-long input is rejected at the same Argon2 cost."""
        stored = self.password_hash(user_id)
        if stored is None or len(password) > 128:
            passwords.burn_verify(password)
            return False, stored
        return passwords.verify_password(stored, password), stored

    def set_password(self, user_id: str, checked_hash: str, new_password: str) -> None:
        """Store a fresh Argon2id hash (current parameters, new salt), only if the password did not change meanwhile."""
        try:
            validate_password(new_password)
        except FieldError as exc:
            raise AccountError(422, "INVALID", exc.message, field="new_password") from None
        new_hash = passwords.hash_password(new_password)
        with self._sf.begin() as s:
            n = s.execute(update(users).where(users.c.id == user_id, users.c.password_hash == checked_hash)
                          .values(password_hash=new_hash)).rowcount
            if n != 1:
                raise AccountError(409, "PASSWORD_CHANGED", MSG_CHANGED)
            s.execute(lifecycle_events.insert().values(kind="PASSWORD_CHANGED", user_id=user_id, created_at=self._clock()))
        log.info("account_event kind=PASSWORD_CHANGED user_id=%s", user_id)
