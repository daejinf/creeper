import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    AIRun,
    CoachingFact,
    CoachingStateRecord,
    ConversationMessage,
    KickoffSession,
    PlanRecord,
    PrivateIntake,
    SessionMember,
)
from .planner import PlanningService
from .privacy import redact_text
from .providers import PlanningProvider, ProviderError
from .schemas import (
    CoachAction,
    CoachMessageType,
    CoachingStateResponse,
    ConversationMessageResponse,
    CreateKickoffSessionRequest,
    DimensionStatus,
    ExtractedFact,
    FactExtractionResult,
    GoalPlanningRequest,
    KickoffMessage,
    KickoffSessionResponse,
    KnownConstraints,
    MemberProfile,
    MessageResponse,
    PlanProposal,
    PlanResponse,
    PrivateIntakeRequest,
    ReadinessDimension,
    SendMessageRequest,
    SessionMemberResponse,
)


COACH_PROMPT_VERSION = "m0-coach-v2"
DIMENSIONS = [
    ("goal", "목표와 기대 결과", "그룹이 무엇을 이루려는지 구체적으로 이야기해주세요."),
    ("metric_target", "측정 기준과 목표값", "성공을 어떤 숫자나 결과로 판단할까요?"),
    ("timeline", "시작일과 마감일", "정확한 시작일과 마감일을 알려주세요."),
    ("frequency", "실행 빈도", "일주일에 몇 번 실행하는 것이 현실적일까요?"),
    ("verification", "인증 방식", "활동 완료를 어떤 자료로 인증할까요?"),
    ("checkin", "체크인 방식", "어느 요일과 시간에 진행 상황을 확인할까요?"),
    ("privacy", "공개 범위", "그룹에는 어떤 정보까지만 공개할까요?"),
    ("consensus", "구성원 합의", "지금 정리된 운영 방식에 모두 동의하나요?"),
    ("current_level", "개인별 현재 수준", "개인 문답에서 현재 수준을 입력해주세요."),
    ("availability", "가용 시간과 제약", "개인 문답에서 가능한 시간과 제약을 입력해주세요."),
]
LABELS = {key: label for key, label, _ in DIMENSIONS}
QUESTIONS = {key: question for key, _, question in DIMENSIONS}


def _as_utc(value: datetime) -> datetime:
    """SQLite may return timezone-aware columns without tzinfo."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CoachingService:
    def __init__(self, db: Session, provider: PlanningProvider | None, settings: Settings):
        self.db = db
        self.provider = provider
        self.settings = settings

    def create_session(self, request: CreateKickoffSessionRequest) -> KickoffSessionResponse:
        member_ids = {member.member_id for member in request.members}
        if len(member_ids) != len(request.members):
            raise ValueError("duplicate member_id")
        if any(message.member_id not in member_ids for message in request.initial_messages):
            raise ValueError("initial message contains unknown member_id")
        session = KickoffSession(
            group_id=request.group_id,
            deadline_at=request.deadline_at,
            timezone=request.timezone,
            status="ACTIVE",
        )
        self.db.add(session)
        self.db.flush()
        for member in request.members:
            self.db.add(SessionMember(session_id=session.id, **member.model_dump()))
        state = CoachingStateRecord(session_id=session.id, dimensions=[], human_messages_since_ai=0)
        self.db.add(state)
        welcome = ConversationMessage(
            session_id=session.id,
            author_type="AI",
            visibility="GROUP",
            message_type=CoachMessageType.question.value,
            text="자유롭게 의견을 나눠주세요. 필요할 때 AI 대화 평가를 실행하면 합의된 내용과 결정할 안건을 한꺼번에 정리해드릴게요.",
        )
        self.db.add(welcome)
        self.db.flush()
        for item in request.initial_messages:
            self.db.add(
                ConversationMessage(
                    session_id=session.id,
                    member_id=item.member_id,
                    author_type="HUMAN",
                    visibility="GROUP",
                    text=redact_text(item.text),
                )
            )
        self.db.commit()
        self._refresh_state(session.id)
        self.db.commit()
        return self.get_session(session.id)

    def send_message(self, session_id: str, request: SendMessageRequest) -> MessageResponse:
        session = self._session(session_id)
        member = self._member(session_id, request.member_id)
        message = ConversationMessage(
            session_id=session.id,
            member_id=member.member_id,
            author_type="HUMAN",
            visibility="GROUP",
            text=redact_text(request.text),
        )
        self.db.add(message)
        state = self._state(session_id)
        state.human_messages_since_ai += 1
        session.messages_since_evaluation += 1
        self.db.commit()
        self.db.refresh(message)
        return MessageResponse(
            user_message=self._message_response(message, {member.member_id: member.nickname}),
            ai_messages=[],
            coaching_state=self._state_response(session_id),
            plan_draft=None,
            messages_since_evaluation=session.messages_since_evaluation,
            evaluation_suggested=session.messages_since_evaluation >= 5,
        )

    def save_intake(self, session_id: str, member_id: str, request: PrivateIntakeRequest) -> MessageResponse:
        self._session(session_id)
        member = self._member(session_id, member_id)
        intake = self.db.scalar(
            select(PrivateIntake).where(PrivateIntake.session_id == session_id, PrivateIntake.member_id == member_id)
        )
        if not intake:
            intake = PrivateIntake(session_id=session_id, member_id=member_id, **request.model_dump(), completed=True)
            self.db.add(intake)
        else:
            for key, value in request.model_dump().items():
                setattr(intake, key, value)
            intake.completed = True
        member.intake_status = "COMPLETED"
        self.db.commit()
        state = self._state_response(session_id)
        return MessageResponse(
            ai_messages=[],
            coaching_state=self._state_response(session_id),
            plan_draft=None,
            messages_since_evaluation=session.messages_since_evaluation if (session := self._session(session_id)) else 0,
            evaluation_suggested=session.messages_since_evaluation >= 5,
        )

    def get_session(self, session_id: str) -> KickoffSessionResponse:
        session = self._session(session_id)
        members = self._members(session_id)
        nicknames = {member.member_id: member.nickname for member in members}
        messages = self.db.scalars(
            select(ConversationMessage).where(
                ConversationMessage.session_id == session_id,
                ConversationMessage.visibility == "GROUP",
            ).order_by(ConversationMessage.created_at)
        ).all()
        plan = None
        if session.current_plan_id:
            record = self.db.get(PlanRecord, session.current_plan_id)
            if record:
                plan = self._plan_response(record)
        return KickoffSessionResponse(
            session_id=session.id,
            group_id=session.group_id,
            deadline_at=session.deadline_at,
            timezone=session.timezone,
            provider_mode="TEST MOCK" if self.provider and self.provider.name == "mock" else "LIVE · DeepSeek",
            members=[SessionMemberResponse(nickname=m.nickname, avatar_color=m.avatar_color, intake_status=m.intake_status) for m in members],
            messages=[self._message_response(message, nicknames) for message in messages],
            coaching_state=self._state_response(session_id),
            plan_draft=plan,
            messages_since_evaluation=session.messages_since_evaluation,
            evaluation_suggested=session.messages_since_evaluation >= 5,
        )

    def _analyze(
        self,
        session_id: str,
        force_intervention: bool = False,
        explicit_ai: bool = False,
        message_ids: list[str] | None = None,
    ) -> tuple[list[ConversationMessage], PlanResponse | None]:
        statement = select(ConversationMessage).where(
            ConversationMessage.session_id == session_id,
            ConversationMessage.author_type == "HUMAN",
            ConversationMessage.visibility == "GROUP",
        )
        if message_ids:
            statement = statement.where(ConversationMessage.id.in_(message_ids))
        messages = self.db.scalars(
            statement.order_by(ConversationMessage.created_at)
        ).all()
        input_messages = [{"id": item.id, "text": item.text} for item in messages]
        prompt = (
            "아래 NEW_MESSAGES에 명시적으로 새로 제안되거나 확정된 계획 사실만 추출하세요. 이전 대화를 다시 요약하지 마세요. "
            "난이도에 대한 감상(예: '너무 어려워'), 의견을 더 나누자는 말, 짧은 맞장구, 숫자만 있는 답은 계획 사실이 아닙니다. "
            "해당하는 새 사실이 없으면 facts=[]를 반환하세요. dimension은 goal, metric_target, timeline, frequency, "
            "verification, checkin, privacy, consensus 중 하나입니다. value는 반드시 다음 키를 사용하세요: "
            "goal={summary,title?}, metric_target={target_value,unit,metric}, timeline={start_date,end_date,duration_weeks?}, "
            "frequency={per_week}, verification={method}, checkin={days,time}, privacy={scope}, consensus={agreed}. "
            "정확한 시작일과 종료일이 둘 다 없으면 timeline status는 PARTIAL입니다. 날짜는 YYYY-MM-DD, "
            "체크인 요일은 MON~SUN, 시간은 HH:MM을 사용하세요. "
            "목표 변경은 목표·달성·완료·바꾸기처럼 변경 의도가 명시된 경우에만 추출하세요. "
            "합의는 동의·합의·결정이 명시된 경우에만 추출하세요. 모든 사실에 실제 메시지 id를 evidence_refs로 넣으세요.\nNEW_MESSAGES:\n"
            + json.dumps(input_messages, ensure_ascii=False)
        )
        prior_conflicts = {
            item.dimension
            for item in self.db.scalars(
                select(CoachingFact).where(
                    CoachingFact.session_id == session_id,
                    CoachingFact.status == DimensionStatus.conflicted.value,
                )
            ).all()
        }
        try:
            result = self.provider.extract_facts(input_messages, prompt)
            extraction = FactExtractionResult.model_validate(result.payload)
            self._log_run("kickoff_fact_extraction", input_messages, result.raw_response, "SUCCEEDED", result)
        except ProviderError as exc:
            self._log_run("kickoff_fact_extraction", input_messages, None, "FAILED", None, exc.code)
            raise
        latest_text = messages[-1].text if messages else ""
        for fact in extraction.facts:
            self._merge_fact(session_id, fact, latest_text)
        # SessionLocal intentionally disables autoflush. Readiness must see facts
        # extracted in this same turn before it decides whether to ask or plan.
        self.db.flush()
        old_state = self._state(session_id)
        previous_question = old_state.last_question_dimension
        state_response = self._refresh_state(session_id)
        answered_previous = previous_question and any(f.dimension == previous_question for f in extraction.facts)
        new_conflict = any(
            item.status == DimensionStatus.conflicted and item.dimension not in prior_conflicts
            for item in state_response.dimensions
        )
        relevant_conflict_reply = any(f.dimension in prior_conflicts for f in extraction.facts)
        conflict_already_asked = (
            state_response.next_action == CoachAction.offer_options
            and previous_question == state_response.next_needed
            and not new_conflict
            and not relevant_conflict_reply
        )
        intervene = (
            force_intervention
            or explicit_ai
            or answered_previous
            or new_conflict
            or (old_state.human_messages_since_ai >= 2 and not conflict_already_asked)
        )
        self.db.commit()
        if state_response.plan_ready:
            return self._auto_plan(session_id)
        if not intervene:
            return [], None
        text, msg_type = self._coach_text(state_response)
        self._log_run(
            "kickoff_coaching",
            state_response.model_dump(mode="json"),
            text,
            "SUCCEEDED",
        )
        ai = self._add_ai_message(session_id, text, msg_type)
        state = self._state(session_id)
        state.human_messages_since_ai = 0
        state.last_question_dimension = state_response.next_needed
        self.db.commit()
        return [ai], None

    def _respond_to_state(self, session_id: str) -> tuple[list[ConversationMessage], PlanResponse | None]:
        state_response = self._refresh_state(session_id)
        if state_response.plan_ready:
            return self._auto_plan(session_id)
        text, msg_type = self._coach_text(state_response)
        self._log_run("kickoff_coaching", state_response.model_dump(mode="json"), text, "SUCCEEDED")
        ai = self._add_ai_message(session_id, text, msg_type)
        state = self._state(session_id)
        state.human_messages_since_ai = 0
        state.last_question_dimension = state_response.next_needed
        self.db.commit()
        return [ai], None

    def _current_conflict_dimension(self, session_id: str) -> str | None:
        fact = self.db.scalar(
            select(CoachingFact).where(
                CoachingFact.session_id == session_id,
                CoachingFact.status == DimensionStatus.conflicted.value,
            )
        )
        return fact.dimension if fact else None

    @staticmethod
    def _choice_index(text: str) -> int | None:
        compact = text.replace(" ", "").lower()
        if any(token in compact for token in ["1번", "첫번째", "첫째", "기존안"]):
            return 0
        if any(token in compact for token in ["2번", "두번째", "둘째", "최근안"]):
            return 1
        return None

    def _resolve_conflict(self, session_id: str, dimension: str, option_index: int, evidence_ref: str) -> None:
        fact = self.db.scalar(
            select(CoachingFact).where(
                CoachingFact.session_id == session_id,
                CoachingFact.dimension == dimension,
                CoachingFact.status == DimensionStatus.conflicted.value,
            )
        )
        options = fact.value.get("options", []) if fact and isinstance(fact.value, dict) else []
        if not fact or option_index >= len(options):
            raise ValueError("INVALID_COACH_CHOICE")
        fact.value = options[option_index]
        fact.status = DimensionStatus.confirmed.value
        fact.evidence_refs = list(dict.fromkeys(fact.evidence_refs + [evidence_ref]))
        if dimension == "metric_target":
            chosen_target = fact.value.get("target_value")
            rejected_targets = [option.get("target_value") for index, option in enumerate(options) if index != option_index]
            goal = self.db.scalar(
                select(CoachingFact).where(
                    CoachingFact.session_id == session_id,
                    CoachingFact.dimension == "goal",
                )
            )
            if goal and chosen_target is not None:
                chosen_text = str(int(chosen_target)) if isinstance(chosen_target, (int, float)) and float(chosen_target).is_integer() else str(chosen_target)
                adjusted = dict(goal.value)
                for key in ["summary", "title"]:
                    if adjusted.get(key):
                        for rejected in rejected_targets:
                            if rejected is not None:
                                rejected_text = str(int(rejected)) if isinstance(rejected, (int, float)) and float(rejected).is_integer() else str(rejected)
                                adjusted[key] = adjusted[key].replace(rejected_text, chosen_text)
                goal.value = adjusted
        self.db.flush()

    def _merge_fact(self, session_id: str, fact: ExtractedFact, latest_text: str) -> None:
        fact = self._normalize_fact(fact)
        explicit_change = any(token in latest_text for token in ["바꾸", "변경", "대신", "수정", "최종"])
        existing = self.db.scalar(
            select(CoachingFact).where(CoachingFact.session_id == session_id, CoachingFact.dimension == fact.dimension)
        )
        resolving = any(word in latest_text for word in ["합의", "결정", "최종", "첫 번째", "두 번째"])
        if not existing:
            self.db.add(CoachingFact(session_id=session_id, visibility="GROUP", **fact.model_dump(mode="json")))
            return
        if existing.value == fact.value:
            existing.evidence_refs = list(dict.fromkeys(existing.evidence_refs + fact.evidence_refs))
            existing.confidence = max(existing.confidence, fact.confidence)
            return
        if self._facts_equivalent(fact.dimension, existing.value, fact.value):
            existing.evidence_refs = list(dict.fromkeys(existing.evidence_refs + fact.evidence_refs))
            existing.confidence = max(existing.confidence, fact.confidence)
            # Prefer a domain-specific count unit over a generic classifier.
            if fact.dimension == "metric_target" and existing.value.get("unit") in {"개", "회"}:
                existing.value = fact.value
            return
        if fact.dimension == "goal" and not any(
            token in latest_text for token in ["목표", "달성", "완료", "하고 싶", "배우", "공부하", "바꾸", "전환"]
        ):
            return
        # A partial statement such as "4주 동안" is refined—not contradicted—
        # by a later exact range. Conversely, a vague repeat must not degrade an
        # already confirmed fact.
        if existing.status == DimensionStatus.partial.value and fact.status == DimensionStatus.confirmed:
            existing.value = fact.value
            existing.status = fact.status.value
            existing.confidence = fact.confidence
            existing.evidence_refs = list(dict.fromkeys(existing.evidence_refs + fact.evidence_refs))
            return
        if existing.status == DimensionStatus.confirmed.value and fact.status == DimensionStatus.partial:
            if explicit_change and fact.dimension in {"metric_target", "timeline", "frequency", "verification", "privacy", "checkin"}:
                fact = fact.model_copy(update={"status": DimensionStatus.confirmed})
            else:
                existing.evidence_refs = list(dict.fromkeys(existing.evidence_refs + fact.evidence_refs))
                return
        strict_conflict = fact.dimension in {"metric_target", "timeline", "frequency", "verification", "privacy", "checkin"}
        if strict_conflict and not resolving:
            options = existing.value.get("options", [existing.value]) if isinstance(existing.value, dict) else [existing.value]
            options.append(fact.value)
            existing.value = {"options": options[-2:]}
            existing.status = DimensionStatus.conflicted.value
            existing.evidence_refs = list(dict.fromkeys(existing.evidence_refs + fact.evidence_refs))
        else:
            existing.value = fact.value
            existing.status = fact.status.value
            existing.confidence = fact.confidence
            existing.evidence_refs = fact.evidence_refs

    @staticmethod
    def _normalize_fact(fact: ExtractedFact) -> ExtractedFact:
        """Map semantically valid provider variants onto the public contract."""
        value = dict(fact.value)
        status = fact.status
        if fact.dimension == "goal":
            summary = value.get("summary") or value.get("title") or value.get("description") or value.get("activity")
            value = {"summary": summary or "목표 확인", **({"title": value["title"]} if value.get("title") else {})}
        elif fact.dimension == "metric_target":
            target = value.get("target_value", value.get("count", value.get("amount")))
            unit = value.get("unit") or value.get("measure") or "회"
            value = {"target_value": target, "unit": unit, "metric": value.get("metric") or f"완료 {unit}"}
            if target is None:
                status = DimensionStatus.partial
        elif fact.dimension == "timeline":
            start = value.get("start_date")
            end = value.get("end_date")
            value = {key: item for key, item in {"start_date": start, "end_date": end, "duration_weeks": value.get("duration_weeks")}.items() if item is not None}
            if not start or not end:
                status = DimensionStatus.partial
        elif fact.dimension == "frequency":
            per_week = value.get("per_week", value.get("frequency_per_week", value.get("count")))
            value = {"per_week": per_week}
            if per_week is None:
                status = DimensionStatus.partial
        elif fact.dimension == "verification":
            value = {"method": value.get("method") or value.get("type") or value.get("description") or "활동 기록"}
        elif fact.dimension == "checkin":
            value = {"days": value.get("days") or value.get("checkin_days") or [], "time": value.get("time") or value.get("local_time")}
            if not value["days"] or not value["time"]:
                status = DimensionStatus.partial
        elif fact.dimension == "privacy":
            value = {"scope": value.get("scope") or value.get("privacy_scope") or "OBJECTIVE_ONLY"}
        elif fact.dimension == "consensus":
            value = {"agreed": bool(value.get("agreed", value.get("confirmed", True)))}
        return fact.model_copy(update={"value": value, "status": status})

    @staticmethod
    def _facts_equivalent(dimension: str, left: dict, right: dict) -> bool:
        if "options" in left or "options" in right:
            return False
        if dimension == "metric_target":
            count_units = {"개", "회", "문제", "문항", "건"}
            same_target = left.get("target_value") == right.get("target_value")
            same_unit = left.get("unit") == right.get("unit") or {left.get("unit"), right.get("unit")} <= count_units
            return same_target and same_unit
        if dimension == "timeline":
            return left.get("start_date") == right.get("start_date") and left.get("end_date") == right.get("end_date") and left.get("duration_weeks") == right.get("duration_weeks")
        if dimension == "frequency":
            return left.get("per_week") == right.get("per_week")
        if dimension == "verification":
            return left.get("method") == right.get("method")
        if dimension == "checkin":
            return set(left.get("days", [])) == set(right.get("days", [])) and left.get("time") == right.get("time")
        if dimension == "privacy":
            return left.get("scope") == right.get("scope")
        if dimension == "consensus":
            return left.get("agreed") == right.get("agreed")
        return left == right

    def _refresh_state(self, session_id: str) -> CoachingStateResponse:
        session = self._session(session_id)
        facts = {fact.dimension: fact for fact in self.db.scalars(select(CoachingFact).where(CoachingFact.session_id == session_id)).all()}
        members = self._members(session_id)
        deadline_passed = _as_utc(session.deadline_at) <= datetime.now(timezone.utc)
        completed = sum(member.intake_status == "COMPLETED" for member in members)
        dimensions: list[ReadinessDimension] = []
        for key, label, _ in DIMENSIONS:
            if key in {"current_level", "availability"}:
                if completed == len(members):
                    status = DimensionStatus.confirmed
                    summary = f"{completed}명 모두 비공개 문답 완료"
                    reason = None
                elif deadline_passed:
                    status = DimensionStatus.partial
                    summary = f"{completed}/{len(members)}명 완료 · 미응답자는 임시 계획"
                    reason = "미응답 구성원의 과제는 PROVISIONAL로 생성됩니다."
                else:
                    status = DimensionStatus.partial if completed else DimensionStatus.missing
                    summary = f"{completed}/{len(members)}명 완료"
                    reason = "모든 구성원의 비공개 문답이 필요합니다."
                dimensions.append(ReadinessDimension(dimension=key, label=label, status=status, summary=summary, reason=reason))
                continue
            fact = facts.get(key)
            if not fact:
                dimensions.append(ReadinessDimension(dimension=key, label=label, status=DimensionStatus.missing, reason=QUESTIONS[key]))
            else:
                summary = self._fact_summary(key, fact.value)
                choices = [self._fact_summary(key, option) for option in fact.value.get("options", [])] if fact.status == "CONFLICTED" else []
                dimensions.append(ReadinessDimension(dimension=key, label=label, status=DimensionStatus(fact.status), summary=summary, reason="아래 선택지 중 하나를 고르거나 새 의견을 남겨주세요." if fact.status == "CONFLICTED" else None, evidence_refs=fact.evidence_refs, choices=choices))
        counts = {status: sum(item.status == status for item in dimensions) for status in DimensionStatus}
        shared_ready = all(item.status == DimensionStatus.confirmed for item in dimensions[:8])
        intake_ready = completed == len(members) or deadline_passed
        plan_ready = shared_ready and intake_ready
        if counts[DimensionStatus.conflicted]:
            action = CoachAction.offer_options
            next_needed = next(item.dimension for item in dimensions if item.status == DimensionStatus.conflicted)
        elif any(item.status != DimensionStatus.confirmed for item in dimensions[:8]):
            action = CoachAction.ask_clarification
            next_needed = next(item.dimension for item in dimensions[:8] if item.status != DimensionStatus.confirmed)
        elif not intake_ready:
            action = CoachAction.request_private_intake
            next_needed = "current_level"
        else:
            action = CoachAction.plan_ready
            next_needed = None
        state = self._state(session_id)
        state.dimensions = [item.model_dump(mode="json") for item in dimensions]
        state.next_action = action.value
        state.plan_ready = plan_ready
        self.db.flush()
        return CoachingStateResponse(
            dimensions=dimensions,
            confirmed_count=counts[DimensionStatus.confirmed],
            partial_count=counts[DimensionStatus.partial],
            missing_count=counts[DimensionStatus.missing],
            conflicted_count=counts[DimensionStatus.conflicted],
            readiness_percent=round(counts[DimensionStatus.confirmed] / len(dimensions) * 100),
            next_action=action,
            next_needed=next_needed,
            plan_ready=plan_ready,
            deadline_passed=deadline_passed,
        )

    def _auto_plan(self, session_id: str) -> tuple[list[ConversationMessage], PlanResponse | None]:
        session = self._session(session_id)
        if session.current_plan_id:
            record = self.db.get(PlanRecord, session.current_plan_id)
            return [], self._plan_response(record) if record else None
        status = self._add_ai_message(session_id, "필요한 정보가 모두 모였어요. 계획 초안을 만들고 있습니다.", CoachMessageType.status)
        facts = {fact.dimension: fact for fact in self.db.scalars(select(CoachingFact).where(CoachingFact.session_id == session_id)).all()}
        timeline = facts["timeline"].value
        start_date = timeline.get("start_date")
        end_date = timeline.get("end_date")
        if not start_date or not end_date:
            return [status], None
        members = []
        for member in self._members(session_id):
            intake = self.db.scalar(select(PrivateIntake).where(PrivateIntake.session_id == session_id, PrivateIntake.member_id == member.member_id))
            members.append(MemberProfile(
                member_id=member.member_id,
                nickname=member.nickname,
                avatar_color=member.avatar_color,
                skill_level=intake.skill_level if intake else "BEGINNER",
                weekly_available_minutes=intake.weekly_available_minutes if intake else 120,
                constraints=intake.constraints if intake else [],
                preferred_times=intake.preferred_times if intake else [],
                provisional=intake is None,
            ))
            if intake is None:
                member.intake_status = "PROVISIONAL"
        messages = self.db.scalars(select(ConversationMessage).where(ConversationMessage.session_id == session_id, ConversationMessage.author_type == "HUMAN", ConversationMessage.visibility == "GROUP").order_by(ConversationMessage.created_at)).all()
        goal = facts["goal"].value
        metric = facts["metric_target"].value
        checkin = facts["checkin"].value
        frequency = facts["frequency"].value
        verification = facts["verification"].value
        privacy = facts["privacy"].value
        request = GoalPlanningRequest(
            group_id=session.group_id,
            kickoff_messages=[KickoffMessage(message_id=m.id, member_id=m.member_id or "ai", text=m.text, created_at=m.created_at) for m in messages],
            members=members,
            known_constraints=KnownConstraints(
                start_date=start_date,
                end_date=end_date,
                timezone=session.timezone,
                verification_method=verification.get("method"),
                privacy_scope=privacy.get("scope", "OBJECTIVE_ONLY"),
                checkin_days=checkin.get("days", []),
                goal_title=goal.get("title") or goal.get("summary", "공동 목표")[:60],
                metric=metric.get("metric"),
                target_value=metric.get("target_value"),
                unit=metric.get("unit"),
                frequency_per_week=frequency.get("per_week"),
                checkin_time=checkin.get("time"),
            ),
        )
        plan = PlanningService(self.db, self.provider, self.settings).generate(request, session_id=session_id)
        session.current_plan_id = plan.plan_id
        plan_message = self._add_ai_message(session_id, "상세 계획 초안을 만들었어요. 승인 전까지 실제 운영에는 반영되지 않습니다.", CoachMessageType.plan_draft)
        self.db.commit()
        return [status, plan_message], plan

    def _coach_text(self, state: CoachingStateResponse) -> tuple[str, CoachMessageType]:
        if state.next_action == CoachAction.offer_options:
            dimension = next(item for item in state.dimensions if item.dimension == state.next_needed)
            labels = dimension.choices or ["먼저 제안된 안", "최근 제안"]
            return f"{dimension.label}에 두 가지 안이 있어요. ① {labels[0]} ② {labels[1]} 중에서 선택하거나 대화를 더 이어가도 좋아요.", CoachMessageType.conflict_options
        if state.next_action == CoachAction.request_private_intake:
            return "공동 운영 방식은 정리됐어요. 이제 각자 비공개 문답에서 수준과 가능한 시간을 알려주세요.", CoachMessageType.private_intake_request
        return QUESTIONS.get(state.next_needed or "goal", "다음으로 필요한 정보를 알려주세요."), CoachMessageType.question

    def _fact_summary(self, key: str, value: dict) -> str:
        if "options" in value:
            return " / ".join(self._fact_summary(key, option) for option in value["options"])
        if key == "goal": return value.get("title") or value.get("summary", "목표 확인")
        if key == "metric_target": return f"{value.get('target_value')}{value.get('unit')} · {value.get('metric', '')}"
        if key == "timeline":
            if value.get("start_date") and value.get("end_date"):
                return f"{value['start_date']} ~ {value['end_date']}"
            if value.get("duration_weeks"):
                return f"{value['duration_weeks']}주 · 정확한 날짜 확인 필요"
            return "정확한 날짜 확인 필요"
        if key == "frequency": return f"주 {value.get('per_week')}회" if value.get("per_week") else "빈도 확인 필요"
        if key == "verification": return value.get("method", "인증 방식 확인")
        if key == "checkin": return f"{', '.join(value.get('days', []))} {value.get('time') or ''}".strip()
        if key == "privacy": return "객관 지표만 공개" if value.get("scope") == "OBJECTIVE_ONLY" else "개인만 확인"
        if key == "consensus": return "구성원 합의 완료"
        return json.dumps(value, ensure_ascii=False)

    def _log_run(self, task: str, payload, raw: str | None, status: str, result=None, error_code: str | None = None) -> None:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.db.add(AIRun(
            task=task,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=COACH_PROMPT_VERSION,
            input_hash=hashlib.sha256(serialized.encode()).hexdigest(),
            status=status,
            raw_response=raw,
            validation_errors=[],
            error_code=error_code,
            prompt_tokens=getattr(result, "prompt_tokens", 0),
            completion_tokens=getattr(result, "completion_tokens", 0),
            total_tokens=getattr(result, "total_tokens", 0),
        ))

    def _add_ai_message(self, session_id: str, text: str, message_type: CoachMessageType) -> ConversationMessage:
        message = ConversationMessage(session_id=session_id, author_type="AI", visibility="GROUP", message_type=message_type.value, text=text)
        self.db.add(message)
        self.db.flush()
        return message

    def _message_response(self, message: ConversationMessage, nicknames: dict[str, str]) -> ConversationMessageResponse:
        return ConversationMessageResponse(id=message.id, nickname=nicknames.get(message.member_id) if message.member_id else None, author_type=message.author_type, message_type=message.message_type, text=message.text, created_at=message.created_at)

    def _state_response(self, session_id: str) -> CoachingStateResponse:
        state = self._state(session_id)
        if not state.dimensions:
            return self._refresh_state(session_id)
        dimensions = [ReadinessDimension.model_validate(item) for item in state.dimensions]
        counts = {status: sum(item.status == status for item in dimensions) for status in DimensionStatus}
        return CoachingStateResponse(dimensions=dimensions, confirmed_count=counts[DimensionStatus.confirmed], partial_count=counts[DimensionStatus.partial], missing_count=counts[DimensionStatus.missing], conflicted_count=counts[DimensionStatus.conflicted], readiness_percent=round(counts[DimensionStatus.confirmed] / len(dimensions) * 100), next_action=CoachAction(state.next_action), next_needed=state.last_question_dimension if state.next_action == CoachAction.ask_clarification.value else next((item.dimension for item in dimensions if item.status in {DimensionStatus.conflicted, DimensionStatus.missing, DimensionStatus.partial}), None), plan_ready=state.plan_ready, deadline_passed=_as_utc(self._session(session_id).deadline_at) <= datetime.now(timezone.utc))

    def _plan_response(self, record: PlanRecord) -> PlanResponse:
        return PlanResponse(plan_id=record.id, version=record.version, approved=record.approved, proposal=PlanProposal.model_validate(record.proposal_payload))

    def _session(self, session_id: str) -> KickoffSession:
        session = self.db.get(KickoffSession, session_id)
        if not session: raise LookupError("KICKOFF_SESSION_NOT_FOUND")
        return session

    def _state(self, session_id: str) -> CoachingStateRecord:
        state = self.db.get(CoachingStateRecord, session_id)
        if not state: raise LookupError("COACHING_STATE_NOT_FOUND")
        return state

    def _member(self, session_id: str, member_id: str) -> SessionMember:
        member = self.db.scalar(select(SessionMember).where(SessionMember.session_id == session_id, SessionMember.member_id == member_id))
        if not member: raise LookupError("SESSION_MEMBER_NOT_FOUND")
        return member

    def _members(self, session_id: str) -> list[SessionMember]:
        return list(self.db.scalars(select(SessionMember).where(SessionMember.session_id == session_id).order_by(SessionMember.id)).all())

