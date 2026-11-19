"""DEPRECATED since D-B3 (2026-11-19): the personal interview turn machinery. NOT wired to any route or to `main.py`.

Stage 3 no longer interviews anyone before the role ranking. This module (with `coach.py`, the `interview_turns` and
`member_states` tables and `member_progress.interview_closed_at`) is kept unchanged for stage 4, where each member is
interviewed by their own AI after the role is assigned. Do not call it from stage 3 paths. `RankingService`
(`ranking.py`) owns the stage 3 screen and the ranking submission.

A message is one turn row: PENDING -> RUNNING -> SUCCEEDED | FAILED. The AI call runs in a background task, never in the
request. A failure is stored with a retry; nothing retries by itself.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..groups.service import ACTIVE, active_cohort_query, is_group_id
from ..models import (
    cohort_members,
    groups,
    interview_turns,
    member_progress,
    member_states,
    preference_submissions,
    project_briefs,
    role_candidate_sets,
)
from ..timeutil import utcnow
from .candidates import SUCCEEDED as CANDIDATES_READY
from .coach import (
    MAX_TURNS,
    MSG_UNCLEAR,
    apply_button,
    meaningful,
    missing_required,
    progress_view,
    question_view,
    run_turn as run_ai_turn,
    state_rows,
)

log = logging.getLogger("moim.interview")

PENDING, RUNNING, SUCCEEDED, FAILED = "PENDING", "RUNNING", "SUCCEEDED", "FAILED"
INTERVIEW, WAITING_ASSIGNMENT = "INTERVIEW", "WAITING_ASSIGNMENT"
ACTION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
OPEN_STATES = (PENDING, RUNNING, FAILED)

MSG_NOT_FOUND = "찾을 수 없어요."
MSG_GENERIC_FAIL = "AI 응답을 받지 못했어요. 다시 시도해주세요."
MSG_SLOW_FAIL = "AI 응답이 늦거나 연결되지 않았어요. 다시 시도해주세요."
MSG_RESTART = "서버가 다시 시작되어 답변 생성이 중단되었어요. 다시 시도해주세요."
SLOW_CODES = {"TIMEOUT", "NETWORK_ERROR", "UPSTREAM_ERROR", "RATE_LIMITED"}


class InterviewError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def not_found() -> InterviewError:
    return InterviewError(404, "NOT_FOUND", MSG_NOT_FOUND)


def _action(action_id: str) -> str:
    if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
        raise InterviewError(422, "INVALID", "요청 형식이 올바르지 않아요.")
    return action_id


def _fail_message(code: str) -> str:
    return MSG_SLOW_FAIL if code in SLOW_CODES else MSG_GENERIC_FAIL


class InterviewTurnService:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], object], clock=utcnow):
        self._sf = session_factory
        self._client_factory = client_factory
        self._clock = clock

    # ---- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _lock_member(s: Session, group_id: str, user_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"interview:{group_id}:{user_id}"})

    @staticmethod
    def _cohort_group(s: Session, group_id: str, user_id: str):
        """The group row for a member of its ACTIVE cohort; NOT_FOUND for everyone else (missing group included)."""
        if not is_group_id(group_id):
            raise not_found()
        g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
        if g is None or g.status != ACTIVE:
            raise not_found()
        member = s.execute(
            active_cohort_query(group_id, g.cohort_revision).where(cohort_members.c.user_id == user_id)
        ).first()
        if member is None:  # never in the cohort, or left the group (D-D1 D6)
            raise not_found()
        return g

    @staticmethod
    def _latest_state(s: Session, group_id: str, user_id: str) -> tuple[int, dict | None]:
        row = s.execute(
            select(member_states.c.revision, member_states.c.state)
            .where(member_states.c.group_id == group_id, member_states.c.user_id == user_id)
            .order_by(member_states.c.revision.desc())
            .limit(1)
        ).one_or_none()
        return (row.revision, row.state) if row else (0, None)

    @staticmethod
    def _turns(s: Session, group_id: str, user_id: str):
        return s.execute(
            select(interview_turns)
            .where(interview_turns.c.group_id == group_id, interview_turns.c.user_id == user_id)
            .order_by(interview_turns.c.seq)
        ).all()

    @staticmethod
    def _progress(s: Session, group_id: str, user_id: str):
        return s.execute(
            select(member_progress).where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
        ).one_or_none()

    @staticmethod
    def _candidate_row(s: Session, group_id: str, revision: int):
        return s.execute(
            select(role_candidate_sets).where(
                role_candidate_sets.c.group_id == group_id,
                role_candidate_sets.c.source_project_brief_revision == revision,
            )
        ).one_or_none()

    @staticmethod
    def _turn_view(t) -> dict:
        failed = t.state == FAILED
        return {
            "seq": t.seq,
            "user_message": t.user_message,
            "ai_reply": t.ai_reply,
            "state": t.state,
            "attempt": t.attempt,
            "error_code": t.error_code if failed else None,
            "error_message": t.error_message if failed else None,
        }

    # ---- sending a message ------------------------------------------------------------------

    def send_message(self, user_id: str, group_id: str, action_id: str, message: str) -> tuple[int, bool]:
        """Store the user's message as a PENDING turn. Returns (seq, created); a repeated action_id creates nothing."""
        text_ = message.strip()
        if not text_ or len(text_) > 300:
            raise InterviewError(422, "INVALID", "메시지는 1자 이상 300자 이하로 보내주세요.", field="message")
        return self._open_turn(user_id, group_id, action_id, text_, None)

    def send_answer(self, user_id: str, group_id: str, action_id: str, question_id: str, option_id: str) -> tuple[int, bool]:
        """A button answer. The browser sends only ids; the text and the value come from the stored question."""
        return self._open_turn(user_id, group_id, action_id, None, (question_id, option_id))

    def _open_turn(self, user_id: str, group_id: str, action_id: str, text_: str | None, answer: tuple[str, str] | None) -> tuple[int, bool]:
        _action(action_id)
        try:
            with self._sf.begin() as s:
                self._lock_member(s, group_id, user_id)
                self._cohort_group(s, group_id, user_id)
                turns = self._turns(s, group_id, user_id)
                same = next((t for t in turns if t.action_id == action_id), None)
                if same is not None:
                    if (answer is None and same.user_message != text_) or (
                        answer is not None and (same.answer_question_id, same.answer_option_id) != answer
                    ):
                        raise InterviewError(409, "ACTION_CONFLICT", "같은 요청 번호에 다른 내용을 보낼 수 없어요.")
                    return same.seq, False
                if answer is None and not meaningful(text_):
                    raise InterviewError(422, "UNCLEAR_ANSWER", MSG_UNCLEAR, field="message")
                progress = self._progress(s, group_id, user_id)
                if progress is not None and (progress.status == WAITING_ASSIGNMENT or progress.interview_closed_at):
                    raise InterviewError(409, "INTERVIEW_CLOSED", "인터뷰가 끝났어요.")
                if any(t.state in OPEN_STATES for t in turns):
                    raise InterviewError(409, "TURN_IN_PROGRESS", "이전 답변을 처리하는 중이에요. 잠시 후 다시 보내주세요.")
                if len(turns) >= MAX_TURNS:
                    raise InterviewError(409, "TURNS_EXHAUSTED", "대화 횟수를 모두 사용했어요.")
                if answer is not None:
                    _, prior = self._latest_state(s, group_id, user_id)
                    g = s.execute(select(groups).where(groups.c.id == group_id)).one()
                    brief = s.execute(
                        select(project_briefs).where(project_briefs.c.group_id == group_id, project_briefs.c.revision == g.brief_revision)
                    ).one()
                    chosen = apply_button(prior, brief, answer[0], answer[1])
                    if chosen is None:  # an old, made-up, already answered or foreign question
                        raise InterviewError(409, "STALE_QUESTION", "지금 질문의 선택지가 아니에요. 화면을 새로고침해 주세요.")
                    text_ = chosen[0]
                seq = len(turns) + 1
                now = self._clock()
                s.execute(
                    insert(interview_turns).values(
                        group_id=group_id, user_id=user_id, seq=seq, action_id=action_id, user_message=text_,
                        answer_question_id=answer[0] if answer else None, answer_option_id=answer[1] if answer else None,
                        state=PENDING, created_at=now, updated_at=now,
                    )
                )
        except IntegrityError:
            # A concurrent request for the same member won the unique index: report the same conflict as above.
            raise InterviewError(409, "TURN_IN_PROGRESS", "이전 답변을 처리하는 중이에요. 잠시 후 다시 보내주세요.") from None
        log.info("interview_event group_id=%s user_id=%s seq=%d state=PENDING button=%s", group_id, user_id, seq, bool(answer))
        return seq, True

    def close_interview(self, user_id: str, group_id: str) -> bool:
        """The member chose the role step. Needs the required items; idempotent (False if it was already closed)."""
        with self._sf.begin() as s:
            self._lock_member(s, group_id, user_id)
            self._cohort_group(s, group_id, user_id)
            progress = self._progress(s, group_id, user_id)
            if progress is not None and progress.interview_closed_at:
                return False
            turns = self._turns(s, group_id, user_id)
            if any(t.state in OPEN_STATES for t in turns):
                raise InterviewError(409, "TURN_IN_PROGRESS", "이전 답변을 처리하는 중이에요. 잠시 후 다시 시도해주세요.")
            _, state = self._latest_state(s, group_id, user_id)
            if missing_required(state) and len(turns) < MAX_TURNS:  # at the turn limit the member may go on with what is known
                raise InterviewError(409, "INTERVIEW_INCOMPLETE", "필요한 정보를 먼저 알려 주세요.")
            now = self._clock()
            if progress is None:
                s.execute(insert(member_progress).values(group_id=group_id, user_id=user_id, status=INTERVIEW, interview_closed_at=now, updated_at=now))
            else:
                s.execute(
                    update(member_progress)
                    .where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
                    .values(interview_closed_at=now, updated_at=now)
                )
        log.info("interview_event group_id=%s user_id=%s state=CLOSED", group_id, user_id)
        return True

    # ---- background run ---------------------------------------------------------------------

    def _cas(self, group_id: str, user_id: str, seq: int, attempt: int, from_state: str, **values) -> bool:
        stmt = (
            update(interview_turns)
            .where(
                interview_turns.c.group_id == group_id,
                interview_turns.c.user_id == user_id,
                interview_turns.c.seq == seq,
                interview_turns.c.attempt == attempt,
                interview_turns.c.state == from_state,
            )
            .values(updated_at=self._clock(), **values)
        )
        with self._sf.begin() as s:
            return s.execute(stmt).rowcount == 1

    def run_turn(self, group_id: str, user_id: str, seq: int) -> None:
        """Background task: one E.coach run (one Timely call) for one turn. Safe to call twice."""
        with self._sf() as s:
            turn = s.execute(
                select(interview_turns).where(
                    interview_turns.c.group_id == group_id, interview_turns.c.user_id == user_id, interview_turns.c.seq == seq
                )
            ).one_or_none()
            if turn is None or turn.state != PENDING:
                return
            _, prior = self._latest_state(s, group_id, user_id)
            g = s.execute(select(groups).where(groups.c.id == group_id)).one()
            brief = s.execute(
                select(project_briefs).where(project_briefs.c.group_id == group_id, project_briefs.c.revision == g.brief_revision)
            ).one()
        attempt = turn.attempt
        if not self._cas(group_id, user_id, seq, attempt, PENDING, state=RUNNING):
            return  # another caller claimed it
        log.info("interview_event group_id=%s user_id=%s seq=%d attempt=%d state=RUNNING", group_id, user_id, seq, attempt)
        try:
            button = None
            if turn.answer_question_id:
                button = apply_button(prior, brief, turn.answer_question_id, turn.answer_option_id)
            if turn.answer_question_id and button is None:
                raise InterviewError(409, "STALE_QUESTION", "stale")
            out = run_ai_turn(self._client_factory(), user_id, prior, brief, turn.user_message, button, seq)
            if out.guarded:
                log.info("interview_event group_id=%s user_id=%s seq=%d note=ai_next_question_replaced_by_server_question", group_id, user_id, seq)
        except Exception as exc:  # the type name only: the message may hold private input
            self._cas(group_id, user_id, seq, attempt, RUNNING, state=FAILED, error_code="HANDLER_ERROR", error_message=MSG_GENERIC_FAIL)
            log.error("interview_event group_id=%s user_id=%s seq=%d state=ERROR error_type=%s", group_id, user_id, seq, type(exc).__name__)
            return
        usage = dict(
            model=(out.model or "")[:64] or None, latency_ms=out.latency_ms, prompt_tokens=out.prompt_tokens,
            completion_tokens=out.completion_tokens, total_tokens=out.total_tokens,
        )
        if not out.ok:
            self._cas(
                group_id, user_id, seq, attempt, RUNNING, state=FAILED, error_code=(out.error_code or "FAILED")[:64],
                error_message=_fail_message(out.error_code or ""), **usage,
            )
            log.info("interview_event group_id=%s user_id=%s seq=%d attempt=%d state=FAILED error=%s", group_id, user_id, seq, attempt, out.error_code)
            return
        with self._sf.begin() as s:
            # out.state is None when the answer was not understood: the turn is finished, the question stays, nothing is saved
            revision = self._latest_state(s, group_id, user_id)[0] + 1
            changed = s.execute(
                update(interview_turns)
                .where(
                    interview_turns.c.group_id == group_id, interview_turns.c.user_id == user_id,
                    interview_turns.c.seq == seq, interview_turns.c.attempt == attempt, interview_turns.c.state == RUNNING,
                )
                .values(state=SUCCEEDED, ai_reply=out.reply, state_revision=revision, error_code=None, error_message=None,
                        updated_at=self._clock(), **usage)
            ).rowcount
            if changed != 1:
                return
            if out.state is not None:
                s.execute(
                    insert(member_states).values(
                        group_id=group_id, user_id=user_id, revision=revision, state=out.state, source_turn_seq=seq,
                        created_at=self._clock(),
                    )
                )
        log.info(
            "interview_event group_id=%s user_id=%s seq=%d attempt=%d state=SUCCEEDED state_revision=%s latency_ms=%d total_tokens=%d",
            group_id, user_id, seq, attempt, revision, out.latency_ms, out.total_tokens,
        )

    # ---- explicit retry ---------------------------------------------------------------------

    def retry_turn(self, user_id: str, group_id: str, seq: int, expected_attempt: int) -> str:
        """FAILED -> PENDING as a new attempt. "started" (schedule `run_turn`), "replay" or "refused"."""
        with self._sf.begin() as s:
            self._lock_member(s, group_id, user_id)
            self._cohort_group(s, group_id, user_id)
            t = s.execute(
                select(interview_turns).where(
                    interview_turns.c.group_id == group_id, interview_turns.c.user_id == user_id, interview_turns.c.seq == seq
                )
            ).one_or_none()
            if t is None:
                raise not_found()
            if t.attempt == expected_attempt + 1:
                return "replay"  # the same click twice: the next attempt already started
            if t.state != FAILED or t.attempt != expected_attempt:
                return "refused"
            s.execute(
                update(interview_turns)
                .where(
                    interview_turns.c.group_id == group_id, interview_turns.c.user_id == user_id, interview_turns.c.seq == seq
                )
                .values(state=PENDING, attempt=t.attempt + 1, error_code=None, error_message=None, updated_at=self._clock())
            )
        return "started"

    def fail_interrupted(self) -> int:
        """Server start: a turn left PENDING/RUNNING lost its background task, so it becomes a retryable failure."""
        stmt = (
            update(interview_turns)
            .where(interview_turns.c.state.in_((PENDING, RUNNING)))
            .values(state=FAILED, error_code="SERVER_RESTARTED", error_message=MSG_RESTART, updated_at=self._clock())
        )
        with self._sf.begin() as s:
            return s.execute(stmt).rowcount
