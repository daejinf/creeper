import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from math import floor
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .coaching import CoachingService, _as_utc
from .config import Settings
from .models import (
    AIRun, ConversationMessage, DecisionOption, DecisionTopic, DecisionVote,
    EvaluationSnapshot, IdempotencyRecord, KickoffSession, PlanRecord, PrivateIntake, SessionMember,
)
from .planner import PlanningService
from .providers import PlanningProvider, ProviderError
from .schemas import (
    AIConversationEvaluation, DecisionOptionResponse, DecisionTopicResponse, EvaluationReadiness,
    EvaluationSnapshotResponse, ExtractedFact, GoalPlanningRequest, KickoffMessage, KnownConstraints,
    MemberProfile, PlanProposal, PlanResponse, VoteDetailResponse, VoteRequest, VoteSummaryResponse,
)


PROMPT_VERSION = "m0-evaluation-v3"
REQUIRED_DIMENSIONS = ["goal", "metric_target", "timeline", "frequency", "verification", "checkin", "privacy"]
TITLES = {
    "goal": "공동 목표", "metric_target": "측정 기준과 목표값", "timeline": "진행 기간",
    "frequency": "실행 빈도", "verification": "인증 방식", "checkin": "체크인 방식", "privacy": "공개 범위",
}


class DecisionsNotReady(RuntimeError):
    def __init__(self, blockers: list[str]):
        super().__init__("DECISIONS_NOT_READY")
        self.blockers = blockers


class ConversationEvaluationService:
    def __init__(self, db: Session, provider: PlanningProvider | None, settings: Settings):
        self.db = db
        self.provider = provider
        self.settings = settings

    def evaluate(self, session_id: str, viewer_member_id: str | None = None) -> EvaluationSnapshotResponse:
        if self.provider is None:
            raise ProviderError("AI_PROVIDER_NOT_CONFIGURED", "DeepSeek provider is required for evaluation")
        session = self._session(session_id)
        messages = list(self.db.scalars(select(ConversationMessage).where(
            ConversationMessage.session_id == session_id,
            ConversationMessage.author_type == "HUMAN",
            ConversationMessage.visibility == "GROUP",
        ).order_by(ConversationMessage.created_at)).all())
        if not messages:
            raise ValueError("EVALUATION_REQUIRES_MESSAGES")
        members = self._members(session_id)
        member_ids = {member.member_id for member in members}
        payload = [{"id": m.id, "member_id": m.member_id, "nickname": next((x.nickname for x in members if x.member_id == m.member_id), None), "text": m.text, "created_at": m.created_at.isoformat()} for m in messages]
        prompt = (
            "그룹 전체 대화를 한 번에 평가하세요. 질문을 만들지 말고 goal, metric_target, timeline, frequency, verification, checkin, privacy 안건을 정리하세요. "
            "서로 다른 제안은 같은 topic의 options로 보존하세요. 각 option은 실제 message id만 evidence_refs로 사용하고 실제 제안자의 member_id만 proposer_member_ids에 넣으세요. "
            "충돌이 있으면 recommended=true인 현실적인 절충안 하나를 추가할 수 있지만 자동 확정하지 마세요. 날짜는 가능하면 YYYY-MM-DD, 요일은 MON~SUN, 시간은 HH:MM, "
            "공개 범위는 OBJECTIVE_ONLY, PRIVATE, CUSTOM 중 하나를 사용하세요. 정보가 없으면 해당 topic을 생략하세요.\nMESSAGES:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        started = time.perf_counter()
        try:
            result = self.provider.evaluate_conversation(payload, prompt)
            evaluation = AIConversationEvaluation.model_validate(result.payload)
        except (ProviderError, ValidationError) as exc:
            self._log_run(payload, None, "FAILED", [str(exc)], getattr(exc, "code", "SCHEMA_VALIDATION_FAILED"), int((time.perf_counter() - started) * 1000))
            self.db.commit()
            raise
        public_names = {member.member_id: member.nickname for member in members}
        evaluation.summary = self._public_text(evaluation.summary, public_names)
        evaluation.warnings = [self._public_text(item, public_names) for item in evaluation.warnings]
        for topic in evaluation.topics:
            topic.title = self._public_text(topic.title, public_names)
            if topic.recommendation_reason:
                topic.recommendation_reason = self._public_text(topic.recommendation_reason, public_names)
            if topic.missing_reason:
                topic.missing_reason = self._public_text(topic.missing_reason, public_names)
            for option in topic.options:
                option.label = self._public_text(option.label, public_names)
        invalid_refs = {ref for topic in evaluation.topics for option in topic.options for ref in option.evidence_refs if ref not in {m.id for m in messages}}
        invalid_members = {mid for topic in evaluation.topics for option in topic.options for mid in option.proposer_member_ids if mid not in member_ids}
        if invalid_refs or invalid_members:
            errors = [f"unknown evidence refs: {sorted(invalid_refs)}", f"unknown member refs: {sorted(invalid_members)}"]
            self._log_run(payload, result.raw_response, "FAILED", errors, "EVALUATION_VALIDATION_FAILED", int((time.perf_counter() - started) * 1000), result)
            self.db.commit()
            raise ValueError("EVALUATION_VALIDATION_FAILED")
        run = self._log_run(payload, result.raw_response, "SUCCEEDED", [], None, int((time.perf_counter() - started) * 1000), result)
        version = (self.db.scalar(select(func.max(EvaluationSnapshot.version)).where(EvaluationSnapshot.session_id == session_id)) or 0) + 1
        snapshot = EvaluationSnapshot(session_id=session_id, version=version, summary=evaluation.summary, warnings=evaluation.warnings, evaluated_through_message_id=messages[-1].id, ai_run_id=run.id)
        self.db.add(snapshot)
        self.db.flush()
        previous = self.db.get(EvaluationSnapshot, session.latest_evaluation_id) if session.latest_evaluation_id else None
        preserved = self._copy_approved(previous, snapshot) if previous else set()
        topics_by_dimension = {topic.dimension: topic for topic in evaluation.topics}
        for dimension in REQUIRED_DIMENSIONS:
            if dimension in preserved:
                continue
            ai_topic = topics_by_dimension.get(dimension)
            topic = DecisionTopic(snapshot_id=snapshot.id, session_id=session_id, dimension=dimension, title=ai_topic.title if ai_topic else TITLES[dimension], status="NEEDS_INFORMATION", required=True, recommendation_reason=ai_topic.recommendation_reason if ai_topic else None)
            self.db.add(topic)
            self.db.flush()
            if ai_topic:
                for ai_option in ai_topic.options:
                    value, valid, inferred = self._normalize_option(dimension, ai_option.value, session)
                    topic.inferred = topic.inferred or inferred
                    self.db.add(DecisionOption(topic_id=topic.id, label=self._label(dimension, value), value=value, evidence_refs=ai_option.evidence_refs, proposer_member_ids=ai_option.proposer_member_ids, recommended=ai_option.recommended))
                    if valid:
                        topic.status = "READY_TO_VOTE"
                if not ai_topic.options:
                    topic.status = "NEEDS_INFORMATION"
        session.latest_evaluation_id = snapshot.id
        session.messages_since_evaluation = 0
        self.db.commit()
        return self.get_latest(session_id, viewer_member_id)

    def start_hybrid_evaluation(self, session_id: str, viewer_member_id: str | None = None) -> EvaluationSnapshotResponse:
        """Create a useful rule-based board immediately; AI enrichment happens separately."""
        session = self._session(session_id)
        messages = list(self.db.scalars(select(ConversationMessage).where(
            ConversationMessage.session_id == session_id,
            ConversationMessage.author_type == "HUMAN",
            ConversationMessage.visibility == "GROUP",
        ).order_by(ConversationMessage.created_at)).all())
        if not messages:
            raise ValueError("EVALUATION_REQUIRES_MESSAGES")
        version = (self.db.scalar(select(func.max(EvaluationSnapshot.version)).where(EvaluationSnapshot.session_id == session_id)) or 0) + 1
        snapshot = EvaluationSnapshot(
            session_id=session_id, version=version,
            summary="대화에서 명확한 제안을 먼저 정리했습니다. DeepSeek가 충돌과 절충안을 보강하고 있어요.",
            warnings=[], evaluated_through_message_id=messages[-1].id,
            evaluation_status="AI_RUNNING", evaluated_message_count=len(messages), ai_attempts=1,
        )
        self.db.add(snapshot); self.db.flush()
        local = self._extract_local_topics(messages, session)
        for dimension in REQUIRED_DIMENSIONS:
            found = local.get(dimension, [])
            topic = DecisionTopic(snapshot_id=snapshot.id, session_id=session_id, dimension=dimension, title=TITLES[dimension], status="READY_TO_VOTE" if found else "NEEDS_INFORMATION", required=True)
            self.db.add(topic); self.db.flush()
            for item in found:
                self.db.add(DecisionOption(topic_id=topic.id, label=item["label"], value=item["value"], evidence_refs=item["evidence_refs"], proposer_member_ids=item["proposer_member_ids"], recommended=False, source="RULE"))
        session.latest_evaluation_id = snapshot.id
        session.messages_since_evaluation = 0
        self.db.commit()
        return self.get_latest(session_id, viewer_member_id)

    def enrich_hybrid_evaluation(self, session_id: str, snapshot_id: str) -> None:
        snapshot = self.db.get(EvaluationSnapshot, snapshot_id)
        session = self._session(session_id)
        if not snapshot or session.latest_evaluation_id != snapshot_id or self.provider is None:
            return
        messages = list(self.db.scalars(select(ConversationMessage).where(
            ConversationMessage.session_id == session_id,
            ConversationMessage.author_type == "HUMAN",
            ConversationMessage.visibility == "GROUP",
        ).order_by(ConversationMessage.created_at).limit(snapshot.evaluated_message_count)).all())
        members = self._members(session_id)
        payload = [{"id": m.id, "member_id": m.member_id, "nickname": next((x.nickname for x in members if x.member_id == m.member_id), None), "text": m.text, "created_at": m.created_at.isoformat()} for m in messages]
        prompt = (
            "전체 대화를 평가해 goal, metric_target, timeline, frequency, verification, checkin, privacy를 모두 검토하세요. "
            "14일·3주 같은 기간은 duration_days로, 날짜 범위는 start_date/end_date로, 주 N번은 per_week로 반환하세요. "
            "이미지·사진 첨부는 verification method입니다. 짧은 동의 표현은 직전 제안의 합의 근거로 해석하세요. "
            "규칙 분석 결과와 병합되므로 대화에 근거한 선택지와 충돌 절충안을 JSON으로 반환하세요.\nMESSAGES:\n" + json.dumps(payload, ensure_ascii=False)
        )
        started = time.perf_counter()
        try:
            result = self.provider.evaluate_conversation(payload, prompt)
            evaluation = AIConversationEvaluation.model_validate(result.payload)
            names = {member.member_id: member.nickname for member in members}
            snapshot.summary = self._public_text(evaluation.summary, names)
            snapshot.warnings = [self._public_text(item, names) for item in evaluation.warnings]
            topics = {t.dimension: t for t in self.db.scalars(select(DecisionTopic).where(DecisionTopic.snapshot_id == snapshot.id)).all()}
            for ai_topic in evaluation.topics:
                topic = topics.get(ai_topic.dimension)
                if not topic:
                    continue
                if ai_topic.recommendation_reason:
                    topic.recommendation_reason = self._public_text(ai_topic.recommendation_reason, names)
                existing = {json.dumps(o.value, ensure_ascii=False, sort_keys=True) for o in self._options(topic.id)}
                for ai_option in ai_topic.options:
                    value, valid, inferred = self._normalize_option(ai_topic.dimension, ai_option.value, session)
                    signature = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if valid and signature not in existing:
                        self.db.add(DecisionOption(topic_id=topic.id, label=self._label(ai_topic.dimension, value), value=value, evidence_refs=ai_option.evidence_refs, proposer_member_ids=ai_option.proposer_member_ids, recommended=ai_option.recommended, source="AI"))
                        existing.add(signature)
                    topic.inferred = topic.inferred or inferred
                if existing:
                    topic.status = "READY_TO_VOTE"
            run = self._log_run(payload, result.raw_response, "SUCCEEDED", [], None, int((time.perf_counter() - started) * 1000), result)
            snapshot.ai_run_id = run.id
            snapshot.evaluation_status = "AI_ENRICHED"
            snapshot.error_code = None
            snapshot.enriched_at = datetime.now(timezone.utc)
        except (ProviderError, ValidationError) as exc:
            run = self._log_run(payload, None, "FAILED", [str(exc)], getattr(exc, "code", "SCHEMA_VALIDATION_FAILED"), int((time.perf_counter() - started) * 1000))
            snapshot.ai_run_id = run.id
            snapshot.evaluation_status = "AI_FAILED"
            snapshot.error_code = getattr(exc, "code", "SCHEMA_VALIDATION_FAILED")
            snapshot.ai_attempts = 2
        self.db.commit()

    def add_manual_option(self, session_id: str, topic_id: str, member_id: str, label: str, value: dict) -> EvaluationSnapshotResponse:
        session = self._session(session_id)
        topic = self.db.get(DecisionTopic, topic_id)
        if not self.db.scalar(select(SessionMember).where(SessionMember.session_id == session_id, SessionMember.member_id == member_id)):
            raise LookupError("SESSION_MEMBER_NOT_FOUND")
        if not topic or topic.snapshot_id != session.latest_evaluation_id or topic.status in {"APPROVED", "SUPERSEDED"}:
            raise ValueError("TOPIC_NOT_EDITABLE")
        normalized, valid, inferred = self._normalize_option(topic.dimension, value, session)
        if not valid:
            raw = self._manual_value(topic.dimension, label)
            normalized, valid, inferred = self._normalize_option(topic.dimension, raw, session)
        if not valid:
            raise ValueError("INVALID_OPTION_VALUE")
        for vote in self.db.scalars(select(DecisionVote).where(DecisionVote.topic_id == topic.id)).all():
            self.db.delete(vote)
        self.db.add(DecisionOption(topic_id=topic.id, label=label.strip(), value=normalized, evidence_refs=[], proposer_member_ids=[member_id], recommended=False, source="HUMAN"))
        topic.status = "READY_TO_VOTE"; topic.inferred = topic.inferred or inferred
        self.db.commit()
        return self.get_latest(session_id, member_id)

    @staticmethod
    def _manual_value(dimension: str, label: str) -> dict:
        if dimension == "goal": return {"summary": label}
        if dimension == "verification": return {"method": label}
        if dimension == "privacy": return {"scope": "PRIVATE" if any(x in label for x in ["비공개", "본인만"]) else "OBJECTIVE_ONLY"}
        if dimension == "frequency":
            match = re.search(r"(\d+)\s*(?:번|회)", label); return {"per_week": int(match.group(1))} if match else {}
        if dimension == "timeline":
            duration = re.search(r"(\d+)\s*(일|주)", label)
            if duration: return {"duration_days": int(duration.group(1)) * (7 if duration.group(2) == "주" else 1)}
            dates = re.search(r"(\d{1,2})[/.](\d{1,2})\s*[~\-]\s*(\d{1,2})[/.](\d{1,2})", label)
            return {"start_date": f"{dates.group(1)}-{dates.group(2)}", "end_date": f"{dates.group(3)}-{dates.group(4)}"} if dates else {}
        if dimension == "metric_target":
            match = re.search(r"(\d+(?:\.\d+)?)\s*(개|문제|페이지|권|km)", label)
            return {"target_value": float(match.group(1)), "unit": match.group(2), "metric": f"완료 {match.group(2)}"} if match else {}
        return {}

    def _extract_local_topics(self, messages: list[ConversationMessage], session: KickoffSession) -> dict[str, list[dict]]:
        topics: dict[str, list[dict]] = {key: [] for key in REQUIRED_DIMENSIONS}
        korean = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}
        seen: set[tuple[str, str]] = set()
        def add(dim: str, value: dict, label: str, message: ConversationMessage):
            signature = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if (dim, signature) not in seen:
                topics[dim].append({"value": value, "label": label, "evidence_refs": [message.id], "proposer_member_ids": [message.member_id] if message.member_id else []}); seen.add((dim, signature))
        for message in messages:
            text = message.text.strip()
            if any(word in text.lower() for word in ["목표", "프로젝트", "과제", "공부", "운동", "독서", "만들"]):
                summary = re.sub(r"^(오늘은|일단은)\s*", "", text)[:120]
                add("goal", {"summary": summary}, summary, message)
            for match in re.finditer(r"(?<!\d)(\d{1,3})\s*(일|주)(?!\s*에)", text):
                amount, unit = int(match.group(1)), match.group(2)
                days = amount if unit == "일" else amount * 7
                add("timeline", {"duration_days": days}, f"{amount}{unit}", message)
            ranges = re.search(r"(\d{1,2})[/.](\d{1,2})\s*[~～\-]\s*(\d{1,2})[/.](\d{1,2})", text)
            if ranges:
                raw = {"start_date": f"{ranges.group(1)}-{ranges.group(2)}", "end_date": f"{ranges.group(3)}-{ranges.group(4)}"}
                value, valid, _ = self._normalize_option("timeline", raw, session)
                if valid: add("timeline", value, self._label("timeline", value), message)
            freq = re.search(r"(?:주|일주일에)\s*(\d+|한|두|세|네|다섯)\s*(?:번|회)", text)
            if freq:
                count = int(freq.group(1)) if freq.group(1).isdigit() else korean[freq.group(1)]
                add("frequency", {"per_week": count}, f"주 {count}회", message)
            metric = re.search(r"(\d+(?:\.\d+)?)\s*(개|문제|페이지|권|km|킬로)", text, re.I)
            if metric:
                unit = "km" if metric.group(2) == "킬로" else metric.group(2)
                add("metric_target", {"target_value": float(metric.group(1)), "unit": unit, "metric": f"완료 {unit}"}, f"{metric.group(1)}{unit}", message)
            if any(word in text for word in ["이미지", "사진", "스크린샷", "첨부", "인증"]):
                method = "이미지 첨부" if any(word in text for word in ["이미지", "사진", "스크린샷"]) else "활동 자료 첨부"
                add("verification", {"method": method}, method, message)
            if any(word in text for word in ["공개", "비공개", "본인만"]):
                scope = "PRIVATE" if any(word in text for word in ["비공개", "본인만"]) else "OBJECTIVE_ONLY"
                add("privacy", {"scope": scope}, self._label("privacy", {"scope": scope}), message)
        return topics

    def vote(self, session_id: str, topic_id: str, request: VoteRequest) -> EvaluationSnapshotResponse:
        session = self._session(session_id)
        if session.messages_since_evaluation > 0:
            raise ValueError("EVALUATION_STALE")
        member = self.db.scalar(select(SessionMember).where(SessionMember.session_id == session_id, SessionMember.member_id == request.member_id))
        topic = self.db.get(DecisionTopic, topic_id)
        if not member:
            raise LookupError("SESSION_MEMBER_NOT_FOUND")
        if not topic or topic.session_id != session_id or topic.snapshot_id != session.latest_evaluation_id:
            raise LookupError("DECISION_TOPIC_NOT_FOUND")
        if topic.status not in {"READY_TO_VOTE", "NEEDS_DISCUSSION"}:
            raise ValueError("TOPIC_NOT_OPEN_FOR_VOTING")
        option = self.db.get(DecisionOption, request.option_id) if request.option_id else None
        if request.choice != "ABSTAIN" and (not option or option.topic_id != topic.id):
            raise ValueError("VALID_OPTION_REQUIRED")
        if request.choice == "AGREE":
            agree_votes = list(self.db.scalars(select(DecisionVote).where(DecisionVote.topic_id == topic.id, DecisionVote.member_id == member.member_id, DecisionVote.choice == "AGREE")).all())
            for vote in agree_votes:
                self.db.delete(vote)
        existing = self.db.scalar(select(DecisionVote).where(DecisionVote.topic_id == topic.id, DecisionVote.member_id == member.member_id, DecisionVote.option_id == (option.id if option else None)))
        if existing:
            existing.choice = request.choice
            existing.reason = request.reason
        else:
            self.db.add(DecisionVote(topic_id=topic.id, option_id=option.id if option else None, member_id=member.member_id, choice=request.choice, reason=request.reason))
        self.db.flush()
        self._recompute_topic(topic)
        self.db.commit()
        return self.get_latest(session_id, request.member_id)

    def reopen(self, session_id: str, topic_id: str) -> EvaluationSnapshotResponse:
        session = self._session(session_id)
        topic = self.db.get(DecisionTopic, topic_id)
        if not topic or topic.session_id != session_id or topic.snapshot_id != session.latest_evaluation_id:
            raise LookupError("DECISION_TOPIC_NOT_FOUND")
        if topic.status not in {"APPROVED", "NO_MAJORITY"}:
            raise ValueError("ONLY_TERMINAL_TOPIC_CAN_BE_REOPENED")
        topic.status = "SUPERSEDED"
        clone = DecisionTopic(snapshot_id=topic.snapshot_id, session_id=session_id, dimension=topic.dimension, title=topic.title, status="READY_TO_VOTE", required=topic.required, recommendation_reason=topic.recommendation_reason, inferred=topic.inferred, revision=topic.revision + 1)
        self.db.add(clone)
        self.db.flush()
        for option in self._options(topic.id):
            self.db.add(DecisionOption(topic_id=clone.id, label=option.label, value=option.value, evidence_refs=option.evidence_refs, proposer_member_ids=option.proposer_member_ids, recommended=option.recommended))
        self.db.commit()
        return self.get_latest(session_id)

    def get_latest(self, session_id: str, viewer_member_id: str | None = None) -> EvaluationSnapshotResponse:
        session = self._session(session_id)
        if not session.latest_evaluation_id:
            raise LookupError("EVALUATION_NOT_FOUND")
        snapshot = self.db.get(EvaluationSnapshot, session.latest_evaluation_id)
        topics = list(self.db.scalars(select(DecisionTopic).where(DecisionTopic.snapshot_id == snapshot.id, DecisionTopic.status != "SUPERSEDED").order_by(DecisionTopic.created_at)).all())
        readiness = self._readiness(session, topics)
        run = self.db.get(AIRun, snapshot.ai_run_id) if snapshot.ai_run_id else None
        status = "STALE" if session.messages_since_evaluation > 0 else snapshot.evaluation_status
        return EvaluationSnapshotResponse(evaluation_id=snapshot.id, version=snapshot.version, summary=snapshot.summary, warnings=snapshot.warnings, topics=[self._topic_response(topic, viewer_member_id) for topic in topics], readiness=readiness, messages_since_evaluation=session.messages_since_evaluation, evaluation_suggested=session.messages_since_evaluation >= 5, created_at=snapshot.created_at, evaluation_status=status, evaluated_message_count=snapshot.evaluated_message_count, error_code=snapshot.error_code, ai_run_id=snapshot.ai_run_id, ai_latency_ms=run.latency_ms if run else None, ai_total_tokens=run.total_tokens if run else None)

    def generate_plan(self, session_id: str, idempotency_key: str) -> PlanResponse:
        if self.provider is None:
            raise ProviderError("AI_PROVIDER_NOT_CONFIGURED", "DeepSeek provider is required for plan generation")
        session = self._session(session_id)
        operation = f"generate_session_plan:{session_id}"
        cached = self.db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.operation == operation, IdempotencyRecord.idempotency_key == idempotency_key))
        if cached:
            return PlanResponse.model_validate(cached.response_payload)
        evaluation = self.get_latest(session_id)
        if not evaluation.readiness.plan_ready:
            raise DecisionsNotReady(evaluation.readiness.blockers)
        approved = {}
        evidence_refs: list[str] = []
        for topic_response in evaluation.topics:
            if topic_response.status == "APPROVED" and topic_response.approved_option_id:
                option = self.db.get(DecisionOption, topic_response.approved_option_id)
                approved[topic_response.dimension] = option.value
                evidence_refs.extend(option.evidence_refs)
        members = []
        deadline_passed = _as_utc(session.deadline_at) <= datetime.now(timezone.utc)
        for member in self._members(session_id):
            intake = self.db.scalar(select(PrivateIntake).where(PrivateIntake.session_id == session_id, PrivateIntake.member_id == member.member_id))
            members.append(MemberProfile(member_id=member.member_id, nickname=member.nickname, avatar_color=member.avatar_color, skill_level=intake.skill_level if intake else "BEGINNER", weekly_available_minutes=intake.weekly_available_minutes if intake else 120, constraints=intake.constraints if intake else [], preferred_times=intake.preferred_times if intake else [], provisional=intake is None))
            if not intake and deadline_passed:
                member.intake_status = "PROVISIONAL"
        messages = list(self.db.scalars(select(ConversationMessage).where(ConversationMessage.session_id == session_id, ConversationMessage.author_type == "HUMAN").order_by(ConversationMessage.created_at)).all())
        timeline, metric, checkin = approved["timeline"], approved["metric_target"], approved["checkin"]
        request = GoalPlanningRequest(group_id=session.group_id, kickoff_messages=[KickoffMessage(message_id=m.id, member_id=m.member_id, text=m.text, created_at=m.created_at) for m in messages], members=members, known_constraints=KnownConstraints(start_date=timeline["start_date"], end_date=timeline["end_date"], timezone=session.timezone, verification_method=approved["verification"]["method"], privacy_scope=approved["privacy"]["scope"], checkin_days=checkin["days"], checkin_time=checkin["time"], goal_title=approved["goal"]["summary"], metric=metric["metric"], target_value=metric["target_value"], unit=metric["unit"], frequency_per_week=approved["frequency"]["per_week"]))
        response = PlanningService(self.db, self.provider, self.settings).generate(request, session_id=session_id)
        if response.proposal.status.value != "DRAFT":
            raise ValueError("PLAN_GENERATION_FAILED")
        session.current_plan_id = response.plan_id
        self.db.add(IdempotencyRecord(operation=operation, idempotency_key=idempotency_key, response_payload=response.model_dump(mode="json")))
        self.db.commit()
        return response

    def _normalize_option(self, dimension: str, raw: dict, session: KickoffSession) -> tuple[dict, bool, bool]:
        normalized = CoachingService._normalize_fact(ExtractedFact(dimension=dimension, value=raw, evidence_refs=["normalization"])).value
        inferred = False
        # Provider payloads are semantically correct more often than they use our exact
        # field names. Canonicalize common aliases before deterministic validation.
        if dimension == "goal" and (not normalized.get("summary") or normalized.get("summary") == "목표 확인"):
            normalized = {"summary": raw.get("goal") or raw.get("title") or raw.get("objective")}
        elif dimension == "metric_target" and not normalized.get("target_value"):
            alias = next(((key, value) for key, value in raw.items() if isinstance(value, (int, float)) and any(token in key for token in ["target", "per_person", "count", "amount", "books", "pages", "problems"])), None)
            if alias:
                key, value = alias
                unit = "권" if "book" in key else "페이지" if "page" in key else "문제" if "problem" in key else raw.get("unit") or "회"
                normalized = {"target_value": value, "unit": unit, "metric": raw.get("metric") or "개인별 목표 달성량"}
        elif dimension == "frequency" and not normalized.get("per_week"):
            normalized = {"per_week": raw.get("times_per_week") or raw.get("weekly_frequency")}
        elif dimension == "verification" and (not normalized.get("method") or normalized.get("method") == "활동 기록"):
            method = raw.get("verification_method") or raw.get("evidence")
            if method and raw.get("additional_content"):
                method = f"{method} + {raw['additional_content']}"
            normalized = {"method": method}
        elif dimension == "checkin":
            days = normalized.get("days") or raw.get("days") or ([raw.get("day_of_week")] if raw.get("day_of_week") else [])
            normalized = {"days": days, "time": normalized.get("time") or raw.get("time")}
        if dimension == "timeline":
            duration = raw.get("duration_days") or normalized.get("duration_days")
            if duration:
                try:
                    days = int(duration)
                    if days > 0:
                        return {"duration_days": days}, True, False
                except (TypeError, ValueError):
                    pass
            start, start_inferred = self._parse_date(normalized.get("start_date"), session)
            end, end_inferred = self._parse_date(normalized.get("end_date"), session, start)
            inferred = start_inferred or end_inferred
            if not start or not end or start >= end:
                return normalized, False, inferred
            normalized = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        if dimension == "privacy":
            raw_scope = str(raw.get("scope") or raw.get("visibility_scope") or normalized.get("scope") or "").upper()
            source = json.dumps(raw, ensure_ascii=False)
            if raw_scope not in {"OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"}:
                raw_scope = "PRIVATE" if any(x in source for x in ["본인", "비공개", "개인만"]) else "OBJECTIVE_ONLY" if any(x in source for x in ["완료", "달성률", "객관"]) else "CUSTOM"
            normalized = {"scope": raw_scope}
        validators = {
            "goal": bool(normalized.get("summary")), "metric_target": bool(normalized.get("target_value") and normalized.get("unit")),
            "timeline": bool((normalized.get("start_date") and normalized.get("end_date")) or normalized.get("duration_days")), "frequency": bool(normalized.get("per_week")),
            "verification": bool(normalized.get("method")), "checkin": bool(normalized.get("days") and normalized.get("time")),
            "privacy": normalized.get("scope") in {"OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"},
        }
        return normalized, validators.get(dimension, False), inferred

    @staticmethod
    def _parse_date(value, session: KickoffSession, start: date | None = None) -> tuple[date | None, bool]:
        if not value:
            return None, False
        text = str(value).replace("/", "-").replace(".", "-").strip(" -")
        try:
            return date.fromisoformat(text), False
        except ValueError:
            pass
        parts = text.split("-")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None, False
        month, day = map(int, parts)
        today = datetime.now(ZoneInfo(session.timezone)).date()
        year = start.year if start else today.year
        try:
            candidate = date(year, month, day)
            if start and candidate <= start:
                candidate = date(year + 1, month, day)
            elif not start and candidate < today:
                candidate = date(year + 1, month, day)
            return candidate, True
        except ValueError:
            return None, True

    @staticmethod
    def _label(dimension: str, value: dict) -> str:
        if dimension == "goal": return value.get("title") or value.get("summary", "공동 목표")
        if dimension == "metric_target": return f"{value.get('target_value')}{value.get('unit')} · {value.get('metric', '')}"
        if dimension == "timeline":
            if value.get("start_date") and value.get("end_date"): return f"{value['start_date']} ~ {value['end_date']}"
            if value.get("duration_days"): return f"{value['duration_days']}일"
            return "기간 확인 필요"
        if dimension == "frequency": return f"주 {value.get('per_week')}회"
        if dimension == "verification": return value.get("method", "인증 방식")
        if dimension == "checkin": return f"{', '.join(value.get('days', []))} {value.get('time') or ''}".strip()
        if dimension == "privacy": return {"OBJECTIVE_ONLY": "완료 여부·달성률만 공개", "PRIVATE": "본인만 확인", "CUSTOM": "사용자 지정 공개"}.get(value.get("scope"), "공개 범위")
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _public_text(text: str, nicknames: dict[str, str]) -> str:
        for member_id, nickname in sorted(nicknames.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(f"({member_id})", "").replace(member_id, nickname)
        return " ".join(text.split())

    def _recompute_topic(self, topic: DecisionTopic) -> None:
        members = self._members(topic.session_id)
        threshold = floor(len(members) / 2) + 1
        votes = list(self.db.scalars(select(DecisionVote).where(DecisionVote.topic_id == topic.id)).all())
        for option in self._options(topic.id):
            if sum(v.choice == "AGREE" and v.option_id == option.id for v in votes) >= threshold:
                topic.status = "APPROVED"
                topic.approved_option_id = option.id
                return
        if len({vote.member_id for vote in votes}) == len(members):
            topic.status = "NO_MAJORITY"

    def _topic_response(self, topic: DecisionTopic, viewer_member_id: str | None) -> DecisionTopicResponse:
        options = self._options(topic.id)
        members = self._members(topic.session_id)
        nicknames = {member.member_id: member.nickname for member in members}
        votes = list(self.db.scalars(select(DecisionVote).where(DecisionVote.topic_id == topic.id)).all())
        revealed = topic.status in {"APPROVED", "NO_MAJORITY"}
        my_votes = {vote.option_id or "ABSTAIN": vote.choice for vote in votes if vote.member_id == viewer_member_id}
        results = [VoteDetailResponse(nickname=nicknames.get(vote.member_id, "구성원"), option_id=vote.option_id, choice=vote.choice, reason=vote.reason) for vote in votes] if revealed else []
        return DecisionTopicResponse(topic_id=topic.id, dimension=topic.dimension, title=topic.title, status=topic.status, required=topic.required, recommendation_reason=topic.recommendation_reason, inferred=topic.inferred, revision=topic.revision, approved_option_id=topic.approved_option_id, options=[DecisionOptionResponse(option_id=o.id, label=o.label, value=o.value, evidence_refs=o.evidence_refs, proposers=[nicknames[mid] for mid in o.proposer_member_ids if mid in nicknames], recommended=o.recommended, source=o.source) for o in options], vote_summary=VoteSummaryResponse(participation_count=len({v.member_id for v in votes}), member_count=len(members), threshold=floor(len(members) / 2) + 1, revealed=revealed, results=results, my_votes=my_votes))

    def _readiness(self, session: KickoffSession, topics: list[DecisionTopic]) -> EvaluationReadiness:
        active = [topic for topic in topics if topic.required and topic.status != "SUPERSEDED"]
        approved = sum(topic.status == "APPROVED" for topic in active)
        vote_needed = sum(topic.status in {"READY_TO_VOTE", "NEEDS_DISCUSSION", "NO_MAJORITY"} for topic in active)
        info_needed = sum(topic.status == "NEEDS_INFORMATION" for topic in active)
        members = self._members(session.id)
        completed = sum(member.intake_status == "COMPLETED" for member in members)
        deadline_passed = _as_utc(session.deadline_at) <= datetime.now(timezone.utc)
        intake_ready = completed == len(members) or deadline_passed
        blockers = []
        if approved != len(active): blockers.append(f"필수 안건 {len(active) - approved}개가 아직 확정되지 않았습니다.")
        if not intake_ready: blockers.append(f"개인 문답 {len(members) - completed}명이 남았습니다.")
        return EvaluationReadiness(required_total=len(active), approved_count=approved, vote_needed_count=vote_needed, information_needed_count=info_needed, intake_completed_count=completed, member_count=len(members), plan_ready=approved == len(active) and intake_ready, blockers=blockers)

    def _copy_approved(self, previous: EvaluationSnapshot, snapshot: EvaluationSnapshot) -> set[str]:
        copied = set()
        topics = list(self.db.scalars(select(DecisionTopic).where(DecisionTopic.snapshot_id == previous.id, DecisionTopic.status == "APPROVED")).all())
        for old in topics:
            new = DecisionTopic(snapshot_id=snapshot.id, session_id=old.session_id, dimension=old.dimension, title=old.title, status="APPROVED", required=old.required, recommendation_reason=old.recommendation_reason, inferred=old.inferred, revision=old.revision, approved_option_id=None)
            self.db.add(new); self.db.flush()
            option_map = {}
            for option in self._options(old.id):
                clone = DecisionOption(topic_id=new.id, label=option.label, value=option.value, evidence_refs=option.evidence_refs, proposer_member_ids=option.proposer_member_ids, recommended=option.recommended)
                self.db.add(clone); self.db.flush(); option_map[option.id] = clone.id
            new.approved_option_id = option_map.get(old.approved_option_id)
            for vote in self.db.scalars(select(DecisionVote).where(DecisionVote.topic_id == old.id)).all():
                self.db.add(DecisionVote(topic_id=new.id, option_id=option_map.get(vote.option_id), member_id=vote.member_id, choice=vote.choice, reason=vote.reason))
            copied.add(old.dimension)
        return copied

    def _log_run(self, payload, raw, status, errors, error_code=None, latency_ms=0, result=None) -> AIRun:
        if self.provider is None:
            raise ProviderError("AI_PROVIDER_NOT_CONFIGURED", "DeepSeek provider is required")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        run = AIRun(task="conversation_evaluation", provider=self.provider.name, model=self.provider.model, prompt_version=PROMPT_VERSION, input_hash=hashlib.sha256(serialized.encode()).hexdigest(), status=status, raw_response=raw, validation_errors=errors, error_code=error_code, latency_ms=latency_ms, prompt_tokens=getattr(result, "prompt_tokens", 0), completion_tokens=getattr(result, "completion_tokens", 0), total_tokens=getattr(result, "total_tokens", 0))
        self.db.add(run); self.db.flush(); return run

    def _session(self, session_id: str) -> KickoffSession:
        session = self.db.get(KickoffSession, session_id)
        if not session: raise LookupError("KICKOFF_SESSION_NOT_FOUND")
        return session

    def _members(self, session_id: str) -> list[SessionMember]:
        return list(self.db.scalars(select(SessionMember).where(SessionMember.session_id == session_id).order_by(SessionMember.id)).all())

    def _options(self, topic_id: str) -> list[DecisionOption]:
        return list(self.db.scalars(select(DecisionOption).where(DecisionOption.topic_id == topic_id).order_by(DecisionOption.created_at)).all())
