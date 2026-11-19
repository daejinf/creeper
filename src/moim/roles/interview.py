"""Post-assignment interview service (stage 4, step 6). Owner-only, one member at a time, never blocking anyone else.

Each member of the CURRENT assignment revision has one interview (`role_interviews`) linked to that revision. A step is
a row in `role_interview_turns`: the AI's first question (OPEN), a member answer (ANSWER) or a confirm-step action
(EDIT). AI steps run as background tasks, PENDING -> RUNNING -> SUCCEEDED | FAILED; a failure keeps the member's answer
and offers an explicit retry, nothing retries by itself. Each successful step writes a new immutable MemberState
revision; confirming writes the input snapshot the todo draft reads. Meaningless input stores nothing, counts nothing
and calls nothing. Nothing here changes the assigned role or is used for a reassignment.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..groups.service import today_kst
from ..interview.candidates import project_input
from ..interview.ranking import RankingService
from ..models import (
    member_progress,
    project_briefs,
    role_assignments,
    role_interview_snapshots,
    role_interview_turns,
    role_interviews,
    role_member_states,
)
from ..timeutil import utcnow
from . import interview_ai as ai
from .assignment import AssignmentService

log = logging.getLogger("moim.role_interview")

PENDING, RUNNING, SUCCEEDED, FAILED = "PENDING", "RUNNING", "SUCCEEDED", "FAILED"
ASKING, CONFIRMING, DONE = "ASKING", "CONFIRMING", "DONE"
OPEN_STATES = (PENDING, RUNNING, FAILED)
ACTION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_SEQ = 40
MSG_FAILED = "AI 응답을 받지 못했어요. 다시 시도해주세요."
MSG_SLOW = "AI 응답이 늦거나 연결되지 않았어요. 다시 시도해주세요."
MSG_RESTART = "서버가 다시 시작되어 답변 생성이 중단되었어요. 다시 시도해주세요."
MSG_MEANINGLESS = "답을 알아보기 어려워요. 버튼을 고르거나 조금 더 자세히 적어 주세요."
SLOW_CODES = {"TIMEOUT", "NETWORK_ERROR", "UPSTREAM_ERROR", "RATE_LIMITED"}


class InterviewError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def _action(action_id: str) -> str:
    if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
        raise InterviewError(422, "INVALID", "요청 형식이 올바르지 않아요.")
    return action_id


class RoleInterviewService:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], object], clock=utcnow, todos=None):
        self._sf = session_factory
        self._client_factory = client_factory
        self._clock = clock
        self.todos = todos  # stage 4 session B: TodoService, whose owner view rides on the same `role` payload

    # ---- shared reads -----------------------------------------------------------------------

    @staticmethod
    def _lock(s: Session, group_id: str, user_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"role_interview:{group_id}:{user_id}"})

    @staticmethod
    def _session_row(s: Session, group_id: str, user_id: str):
        """The member's interview for the CURRENT assignment revision; NOT_FOUND for outsiders, 409 before assignment."""
        g = RankingService._cohort_group(s, group_id, user_id)
        progress = s.execute(
            select(member_progress).where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
        ).one_or_none()
        current = AssignmentService.current(s, group_id)
        if progress is None or progress.status != "ROLE_TODOS" or current is None or progress.assignment_revision != current.revision:
            raise InterviewError(409, "NOT_ASSIGNED", "역할이 정해진 뒤에 대화할 수 있어요.")
        row = s.execute(
            select(role_interviews).where(
                role_interviews.c.group_id == group_id, role_interviews.c.user_id == user_id,
                role_interviews.c.source_assignment_revision == current.revision,
            )
        ).one_or_none()
        if row is None or row.stale_at is not None:
            raise InterviewError(409, "NOT_ASSIGNED", "역할이 정해진 뒤에 대화할 수 있어요.")
        return g, current, row

    @staticmethod
    def _state(s: Session, group_id: str, user_id: str, revision: int, state_revision: int) -> dict | None:
        if state_revision == 0:
            return None
        return s.execute(
            select(role_member_states.c.state).where(
                role_member_states.c.group_id == group_id, role_member_states.c.user_id == user_id,
                role_member_states.c.source_assignment_revision == revision, role_member_states.c.revision == state_revision,
            )
        ).scalar_one()

    @staticmethod
    def _turns(s: Session, group_id: str, user_id: str, revision: int):
        return s.execute(
            select(role_interview_turns).where(
                role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                role_interview_turns.c.source_assignment_revision == revision,
            ).order_by(role_interview_turns.c.seq)
        ).all()

    @staticmethod
    def _base_state(s: Session, group_id: str, revision: int) -> dict:
        brief = s.execute(
            select(project_briefs.c.start_date, project_briefs.c.end_date)
            .join(role_assignments, (role_assignments.c.group_id == project_briefs.c.group_id)
                  & (role_assignments.c.source_candidate_revision == project_briefs.c.revision))
            .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == revision)
        ).one()
        st = ai.empty_state()
        st["period"] = [brief.start_date.isoformat(), brief.end_date.isoformat()]
        return st

    # ---- screen -----------------------------------------------------------------------------

    def view(self, s: Session, group_id: str, user_id: str, revision: int) -> dict:
        row = s.execute(
            select(role_interviews).where(
                role_interviews.c.group_id == group_id, role_interviews.c.user_id == user_id,
                role_interviews.c.source_assignment_revision == revision,
            )
        ).one()
        state = self._state(s, group_id, user_id, revision, row.state_revision)
        turns = self._turns(s, group_id, user_id, revision)
        messages, shown_q, busy, failed = [], None, False, None
        for t in turns:
            if t.kind != "OPEN":
                messages.append({"from": "me", "text": t.user_message})
            if t.state in (PENDING, RUNNING):
                busy = True
                continue
            if t.state == FAILED:
                failed = {"seq": t.seq, "attempt": t.attempt}
                messages.append({"from": "ai", "text": t.error_message or MSG_FAILED, "err": True})
                continue
            if t.reply:
                messages.append({"from": "ai", "text": t.reply})
            q = t.question
            if q and q["question_id"] != shown_q:
                messages.append({"from": "ai", "text": q["text"]})
                shown_q = q["question_id"]
        question = (state or {}).get("question") if not busy and failed is None and row.state != DONE else None
        return {
            "state": row.state,
            "started": bool(turns),
            "questions_asked": row.questions_asked,
            "max_questions": ai.MAX_QUESTIONS,
            "messages": messages,
            "question": ai.question_view(question),
            "busy": busy,
            "failed": failed,
            "input_open": question is not None and question["kind"] in ("ask", "edit"),
            "editing": question is not None and question["kind"] == "edit",
        }

    def role_view(self, user_id: str, group_id: str, display_name: str) -> dict | None:
        """The ROLE_TODOS part of the chat payload: only the caller's own assignment, interview and the coverage state.
        Never another member's name, role, ranking or random draw (D-B1 B6)."""
        from .coverage import CoverageService

        with self._sf() as s:
            progress = s.execute(
                select(member_progress).where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
            ).one_or_none()
            current = AssignmentService.current(s, group_id)
            if progress is None or progress.status != "ROLE_TODOS" or current is None or progress.assignment_revision != current.revision:
                return None
            mine = AssignmentService.mine(s, group_id, user_id, current.revision)
            interview = self.view(s, group_id, user_id, current.revision)
            coverage = CoverageService.state_view(s, group_id, current.revision)
            todos = (self.todos.view(s, group_id, user_id, current.revision, interview["state"] == DONE, coverage["state"])
                     if self.todos is not None else None)
        rank = mine.assigned_rank
        rank_text = f"{rank}순위로 배정" if rank else "남은 역할로 배정"
        coverage["can_retry"] = coverage["state"] == "FAILED"
        return {
            "role_name": mine.role_name,
            "role_description": mine.role_description,
            "rank_text": rank_text,
            "announcement": [
                "팀원 모두 순위를 냈어요. 1순위부터 차례로 역할을 나누고, 같은 역할에 여러 명이 겹칠 때만 서버가 무작위로 정했어요.",
                f"{display_name}님은 이번 프로젝트에서 {mine.role_name} 역할을 맡게 됐어요({rank_text}).",
            ],
            "interview": interview,
            "coverage": coverage,
            "todos": todos,
        }

    # ---- start (the AI's first question) ----------------------------------------------------

    def start(self, user_id: str, group_id: str, action_id: str) -> tuple[str, int | None]:
        """("started", revision) when the first question must be generated now, else ("noop", None)."""
        _action(action_id)
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            _, current, row = self._session_row(s, group_id, user_id)
            if row.state != ASKING:
                return "noop", None
            exists = s.execute(
                select(func.count()).select_from(role_interview_turns).where(
                    role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                    role_interview_turns.c.source_assignment_revision == current.revision,
                )
            ).scalar_one()
            if exists:
                return "noop", None
            # An action id already used for another (now STALE) assignment revision: a page left open across a
            # reassignment or a brief change. Refuse cleanly instead of failing on the unique key.
            reused = s.execute(
                select(role_interview_turns.c.seq).where(
                    role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                    role_interview_turns.c.action_id == action_id,
                )
            ).first()
            if reused is not None:
                raise InterviewError(409, "ACTION_REUSED", "이 화면이 오래됐어요. 새로고침해 주세요.")
            now = self._clock()
            s.execute(
                insert(role_interview_turns).values(
                    group_id=group_id, user_id=user_id, source_assignment_revision=current.revision, seq=0, kind="OPEN",
                    action_id=action_id, state=PENDING, created_at=now, updated_at=now,
                )
            )
        return "started", current.revision

    # ---- answers ----------------------------------------------------------------------------

    def answer(self, user_id: str, group_id: str, action_id: str, message: str | None, question_id: str | None,
               option_id: str | None) -> tuple[str, int | None, int | None]:
        """("started", revision, seq) when an AI step must run now; ("done" | "replay", None, None) otherwise."""
        _action(action_id)
        if (message is None) == (question_id is None or option_id is None) or ((question_id is None) != (option_id is None)):
            raise InterviewError(422, "INVALID", "요청 형식이 올바르지 않아요.")
        if message is not None:
            message = message.strip()
            if not (1 <= len(message) <= 300):
                raise InterviewError(422, "INVALID", "답변은 1~300자로 적어 주세요.", field="message")
            if not ai.meaningful(message):  # nothing stored, nothing counted, no AI call
                raise InterviewError(422, "MEANINGLESS", MSG_MEANINGLESS, field="message")
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            _, current, row = self._session_row(s, group_id, user_id)
            rev = current.revision
            replay = s.execute(
                select(role_interview_turns.c.seq).where(
                    role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                    role_interview_turns.c.action_id == action_id,
                )
            ).first()
            if replay is not None:
                return "replay", None, None
            if row.state == DONE:
                raise InterviewError(409, "INTERVIEW_DONE", "인터뷰를 이미 마쳤어요.")
            turns = self._turns(s, group_id, user_id, rev)
            if any(t.state in OPEN_STATES for t in turns):
                raise InterviewError(409, "TURN_OPEN", "앞의 답변을 처리하고 있어요. 잠시 후 다시 시도해주세요.")
            state = self._state(s, group_id, user_id, rev, row.state_revision)
            q = (state or {}).get("question")
            if q is None:
                raise InterviewError(409, "NO_QUESTION", "질문을 준비하고 있어요. 잠시 후 다시 시도해주세요.")
            button = None
            if question_id is not None:
                if question_id != q["question_id"]:
                    raise InterviewError(409, "STALE_QUESTION", "지난 질문이에요. 새로고침해서 다시 골라 주세요.")
                button = ai.resolve_button(q, option_id) if q["kind"] == "ask" else self._edit_button(q, option_id)
                if button is None:
                    raise InterviewError(422, "INVALID_OPTION", "고를 수 없는 답이에요.")
            elif q["kind"] == "confirm":
                raise InterviewError(422, "USE_BUTTONS", "아래 버튼에서 골라 주세요.", field="message")
            seq = (max(t.seq for t in turns) + 1) if turns else 1
            if seq > MAX_SEQ:
                raise InterviewError(409, "TOO_MANY", "대화가 너무 길어졌어요. '맞아요'로 마무리해 주세요.")
            label = button[0] if button is not None else message
            now = self._clock()
            if q["kind"] in ("confirm", "edit"):
                return self._confirm_step(s, group_id, user_id, rev, row, state, q, seq, action_id, label, message,
                                          question_id, option_id, button, now)
            state = {**state, "today": today_kst(now).isoformat()}  # "다음 주 화요일" is resolved against today
            new_state, interpret, reply = ai.prepare_answer(state, q, message, button, seq)
            base = dict(group_id=group_id, user_id=user_id, source_assignment_revision=rev, seq=seq, kind="ANSWER",
                        action_id=action_id, user_message=label, answer_question_id=question_id, answer_option_id=option_id,
                        created_at=now, updated_at=now)
            if not ai.needs_ai_call(new_state, interpret):
                self._finish_asking(new_state, seq)
                self._commit_step(s, group_id, user_id, rev, row, new_state, seq, now)
                s.execute(insert(role_interview_turns).values(**base, state=SUCCEEDED, reply=reply,
                                                              question=new_state["question"], state_revision=row.state_revision + 1))
                return "done", None, None
            s.execute(insert(role_interview_turns).values(**base, state=PENDING))
        return "started", rev, seq

    @staticmethod
    def _edit_button(q: dict, option_id: str):
        if q["kind"] == "confirm":
            return next(((o["label"], o["option_id"], None) for o in q["options"] if o["option_id"] == option_id), None)
        if option_id in (ai.UNKNOWN_ID, ai.CANCEL_ID):
            return next((o["label"], o["option_id"], None) for o in q["options"] if o["option_id"] == option_id)
        return None

    def _confirm_step(self, s, group_id, user_id, rev, row, state, q, seq, action_id, label, message, question_id, option_id, button, now):
        """Confirm / edit actions: code only, stored at once as an EDIT step (never an AI call)."""
        new = json.loads(json.dumps(state))
        new["today"] = today_kst(now).isoformat()
        reply = None
        done = False
        if q["kind"] == "confirm":
            choice = button[1]
            if choice == ai.ACCEPT_ID:
                new["question"] = None
                reply = ai.MSG_DONE
                done = True
            else:
                field = choice[len("edit_"):]
                new["question"] = ai.edit_question(field, f"e{seq}")
        else:
            field = q["field"]
            if button is not None and button[1] == ai.CANCEL_ID:
                reply = None
            else:
                try:
                    reply = ai.apply_edit(new, field, message, unknown=button is not None, seq=seq)
                except ValueError as exc:  # nothing stored: the member types again
                    raise InterviewError(422, "INVALID_ANSWER", str(exc), field="message") from None
            new["question"] = ai.confirm_question(new, f"c{seq}")
        self._commit_step(s, group_id, user_id, rev, row, new, seq, now, state_name=DONE if done else CONFIRMING)
        s.execute(
            insert(role_interview_turns).values(
                group_id=group_id, user_id=user_id, source_assignment_revision=rev, seq=seq, kind="EDIT", action_id=action_id,
                user_message=label, answer_question_id=question_id, answer_option_id=option_id, state=SUCCEEDED,
                reply=reply, question=new["question"], state_revision=row.state_revision + 1, created_at=now, updated_at=now,
            )
        )
        if done:
            provider, private, limits = ai.snapshot(new)
            s.execute(
                insert(role_interview_snapshots).values(
                    group_id=group_id, user_id=user_id, source_assignment_revision=rev,
                    member_state_revision=row.state_revision + 1, provider_input=provider, private_constraints=private,
                    deadline_limits=limits, created_at=now,
                )
            )
            log.info("role_interview_event group_id=%s user_id=%s revision=%d state=DONE", group_id, user_id, rev)
        return "done", None, None

    @staticmethod
    def _finish_asking(state: dict, seq: int) -> None:
        state["question"] = ai.confirm_question(state, f"c{seq}")

    def _commit_step(self, s, group_id, user_id, rev, row, new_state, seq, now, state_name=None, questions=None):
        s.execute(
            insert(role_member_states).values(
                group_id=group_id, user_id=user_id, source_assignment_revision=rev, revision=row.state_revision + 1,
                state=new_state, source_turn_seq=seq, created_at=now,
            )
        )
        q = new_state.get("question")
        name = state_name or (ASKING if q is not None and q["kind"] == "ask" else CONFIRMING)
        s.execute(
            update(role_interviews)
            .where(role_interviews.c.group_id == group_id, role_interviews.c.user_id == user_id,
                   role_interviews.c.source_assignment_revision == rev)
            .values(state=name, state_revision=row.state_revision + 1, questions_asked=len(new_state["asked"]), updated_at=now)
        )

    # ---- background AI step -----------------------------------------------------------------

    def _cas(self, group_id, user_id, rev, seq, attempt, from_state, **values) -> bool:
        stmt = (
            update(role_interview_turns)
            .where(role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                   role_interview_turns.c.source_assignment_revision == rev, role_interview_turns.c.seq == seq,
                   role_interview_turns.c.attempt == attempt, role_interview_turns.c.state == from_state)
            .values(updated_at=self._clock(), **values)
        )
        with self._sf.begin() as s:
            return s.execute(stmt).rowcount == 1

    def run(self, group_id: str, user_id: str, rev: int, seq: int) -> None:
        """PENDING -> RUNNING -> SUCCEEDED | FAILED for one AI step. Safe to call twice."""
        with self._sf() as s:
            turn = s.execute(
                select(role_interview_turns).where(
                    role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                    role_interview_turns.c.source_assignment_revision == rev, role_interview_turns.c.seq == seq,
                )
            ).one_or_none()
            if turn is None or turn.state != PENDING:
                return
            row = s.execute(
                select(role_interviews).where(role_interviews.c.group_id == group_id, role_interviews.c.user_id == user_id,
                                              role_interviews.c.source_assignment_revision == rev)
            ).one()
            state = self._state(s, group_id, user_id, rev, row.state_revision) or self._base_state(s, group_id, rev)
            mine = AssignmentService.mine(s, group_id, user_id, rev)
            team = AssignmentService.team_table(s, group_id, rev)
            brief = s.execute(
                select(project_briefs)
                .join(role_assignments, (role_assignments.c.group_id == project_briefs.c.group_id)
                      & (role_assignments.c.source_candidate_revision == project_briefs.c.revision))
                .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == rev)
            ).one()
        attempt = turn.attempt
        if not self._cas(group_id, user_id, rev, seq, attempt, PENDING, state=RUNNING):
            return
        q = state.get("question")
        button = None
        if turn.kind == "ANSWER":
            if turn.answer_option_id is not None:
                button = ai.resolve_button(q, turn.answer_option_id)
            state = {**state, "today": today_kst(turn.created_at).isoformat()}  # the day the member answered
            new, interpret_needed, reply = ai.prepare_answer(state, q, turn.user_message, button, seq)
            new["answer_turns"] = new.get("answer_turns", 0) + 1
            kind = "button" if button is not None else ("withheld" if q["field"] == "constraints" else "text")
            answer = turn.user_message if kind in ("text", "button") else None
        else:
            new, interpret_needed, reply, kind, answer = json.loads(json.dumps(state)), False, None, "none", None
            q = None
        payload = ai.ai_payload(project_input(brief), {"role": mine.role_name, "description": mine.role_description}, team,
                                new if turn.kind == "ANSWER" else state, q, answer, kind)
        started = time.perf_counter()
        try:
            done = self._call(payload)
        except _StepFailed as exc:
            self._fail(group_id, user_id, rev, seq, attempt, exc.code)
            return
        out = done.parsed_output
        understood = True
        try:
            if interpret_needed:
                understood, reply = ai.interpret(new, q, turn.user_message, out, seq)
            if not understood:
                r = (out.reply or "").strip()
                reply = r if ai._usable_text(r, 60) else ai.MSG_UNCLEAR
                new["question"] = q  # the same question stays; it is not counted again
            elif len(new["asked"]) >= ai.MAX_QUESTIONS or new.get("answer_turns", 0) >= ai.MAX_ANSWER_TURNS:
                self._finish_asking(new, seq)
            else:
                nq = ai.finish_ai_step(new, out, f"q{seq}")
                if nq is None:
                    self._finish_asking(new, seq)
        except ai.TurnInvalid as exc:
            self._fail(group_id, user_id, rev, seq, attempt, exc.code, done)
            return
        latency = done.latency_ms or int((time.perf_counter() - started) * 1000)
        now = self._clock()
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            row = s.execute(
                select(role_interviews).where(role_interviews.c.group_id == group_id, role_interviews.c.user_id == user_id,
                                              role_interviews.c.source_assignment_revision == rev)
            ).one()
            if row.stale_at is not None:
                return
            claimed = s.execute(
                update(role_interview_turns)
                .where(role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                       role_interview_turns.c.source_assignment_revision == rev, role_interview_turns.c.seq == seq,
                       role_interview_turns.c.attempt == attempt, role_interview_turns.c.state == RUNNING)
                .values(state=SUCCEEDED, reply=reply, question=new["question"], state_revision=row.state_revision + 1,
                        error_code=None, error_message=None, model=(done.model or "")[:64], latency_ms=latency,
                        prompt_tokens=done.usage.prompt_tokens, completion_tokens=done.usage.completion_tokens,
                        total_tokens=done.usage.total_tokens, updated_at=now)
            ).rowcount
            if claimed != 1:
                return
            self._commit_step(s, group_id, user_id, rev, row, new, seq, now)
        log.info(
            "role_interview_event group_id=%s user_id=%s revision=%d seq=%d attempt=%d state=SUCCEEDED latency_ms=%d total_tokens=%d questions=%d",
            group_id, user_id, rev, seq, attempt, latency, done.usage.total_tokens, len(new["asked"]),
        )

    def _call(self, payload: dict):
        from wemeet_ai.m0.client import AIError

        messages = [{"role": "system", "content": ai.AI_POLICY},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            return self._client_factory().complete_json(messages, ai.RoleTurnOutput, {"reasoning_effort": "low"})
        except AIError as exc:
            raise _StepFailed(exc.code) from None
        except Exception as exc:  # the type name only
            log.error("role_interview_event state=ERROR error_type=%s", type(exc).__name__)
            raise _StepFailed("HANDLER_ERROR") from None

    def _fail(self, group_id, user_id, rev, seq, attempt, code, done=None) -> None:
        values = dict(state=FAILED, error_code=code[:64], error_message=MSG_SLOW if code in SLOW_CODES else MSG_FAILED)
        if done is not None:
            values.update(model=(done.model or "")[:64], latency_ms=done.latency_ms, prompt_tokens=done.usage.prompt_tokens,
                          completion_tokens=done.usage.completion_tokens, total_tokens=done.usage.total_tokens)
        self._cas(group_id, user_id, rev, seq, attempt, RUNNING, **values)
        log.info("role_interview_event group_id=%s user_id=%s revision=%d seq=%d attempt=%d state=FAILED error=%s",
                 group_id, user_id, rev, seq, attempt, code)

    # ---- explicit retry ---------------------------------------------------------------------

    def retry(self, user_id: str, group_id: str, seq: int, expected_attempt: int) -> tuple[str, int | None]:
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            _, current, _ = self._session_row(s, group_id, user_id)
            turn = s.execute(
                select(role_interview_turns.c.state, role_interview_turns.c.attempt).where(
                    role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                    role_interview_turns.c.source_assignment_revision == current.revision, role_interview_turns.c.seq == seq,
                )
            ).one_or_none()
            if turn is None:
                return "refused", None
            if turn.attempt == expected_attempt + 1 and turn.state != FAILED:
                return "replay", None  # this retry already started the next attempt
            if turn.state != FAILED or turn.attempt != expected_attempt:
                return "refused", None
            s.execute(
                update(role_interview_turns)
                .where(role_interview_turns.c.group_id == group_id, role_interview_turns.c.user_id == user_id,
                       role_interview_turns.c.source_assignment_revision == current.revision, role_interview_turns.c.seq == seq)
                .values(state=PENDING, attempt=turn.attempt + 1, error_code=None, error_message=None, updated_at=self._clock())
            )
        return "started", current.revision

    def fail_interrupted(self) -> int:
        stmt = (
            update(role_interview_turns)
            .where(role_interview_turns.c.state.in_((PENDING, RUNNING)))
            .values(state=FAILED, error_code="SERVER_RESTARTED", error_message=MSG_RESTART, updated_at=self._clock())
        )
        with self._sf.begin() as s:
            return s.execute(stmt).rowcount


class _StepFailed(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
