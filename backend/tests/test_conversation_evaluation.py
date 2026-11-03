from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.coaching import CoachingService
from app.config import Settings
from app.conversation_evaluation import ConversationEvaluationService, DecisionsNotReady
from app.models import AIRun, ConversationMessage, KickoffSession
from app.providers import MockPlanningProvider, ProviderError
from app.schemas import CreateKickoffSessionRequest, PrivateIntakeRequest, SendMessageRequest, SessionMemberInput, VoteRequest


MEMBERS = [
    SessionMemberInput(member_id="internal-1", nickname="지민", avatar_color="violet"),
    SessionMemberInput(member_id="internal-2", nickname="서준", avatar_color="mint"),
    SessionMemberInput(member_id="internal-3", nickname="하린", avatar_color="coral"),
]
SETTINGS = Settings(ai_provider="mock", database_url="sqlite://")


def create_session(db):
    return CoachingService(db, None, SETTINGS).create_session(CreateKickoffSessionRequest(
        group_id="conversation-eval-test",
        deadline_at=datetime.now(timezone.utc) + timedelta(days=2),
        members=MEMBERS,
        initial_messages=[],
    ))


def send(db, session_id, member_id, text):
    return CoachingService(db, None, SETTINGS).send_message(session_id, SendMessageRequest(member_id=member_id, text=text))


def full_conversation(db, session_id):
    for member_id, text in [
        ("internal-1", "독서 습관을 만들어 각자 책 2권을 읽는 것을 목표로 해요."),
        ("internal-2", "2026-11-09부터 2026-11-30까지 주 2회 진행해요."),
        ("internal-3", "읽은 페이지 사진과 짧은 감상으로 인증해요."),
        ("internal-1", "금요일 저녁 8시에 체크인해요."),
        ("internal-2", "그룹에는 완료 여부만 공개해요."),
    ]:
        send(db, session_id, member_id, text)


def test_new_session_is_neutral_and_hides_internal_ids(db):
    session = create_session(db)
    assert len(session.messages) == 1
    assert "파이썬" not in session.model_dump_json()
    assert "internal-1" not in session.model_dump_json()


def test_messages_do_not_call_ai_and_fifth_only_suggests_evaluation(db):
    session = create_session(db)
    for index in range(4):
        response = send(db, session.session_id, "internal-1", f"자유 의견 {index}")
        assert response.ai_messages == [] and not response.evaluation_suggested
    response = send(db, session.session_id, "internal-2", "다섯 번째 자유 의견")
    assert response.evaluation_suggested and response.messages_since_evaluation == 5
    assert db.scalar(select(AIRun)) is None
    assert len(db.scalars(select(ConversationMessage).where(ConversationMessage.author_type == "HUMAN")).all()) == 5


def test_one_evaluation_creates_multiple_topics(db):
    session = create_session(db); full_conversation(db, session.session_id)
    result = ConversationEvaluationService(db, MockPlanningProvider(), SETTINGS).evaluate(session.session_id, "internal-1")
    dimensions = {topic.dimension for topic in result.topics if topic.options}
    assert {"goal", "metric_target", "timeline", "frequency", "verification", "checkin", "privacy"} <= dimensions
    assert result.messages_since_evaluation == 0
    assert all(option.proposers for topic in result.topics for option in topic.options)


def test_votes_are_hidden_then_majority_reveals_results(db):
    session = create_session(db); full_conversation(db, session.session_id)
    service = ConversationEvaluationService(db, MockPlanningProvider(), SETTINGS)
    evaluated = service.evaluate(session.session_id, "internal-1")
    topic = next(item for item in evaluated.topics if item.dimension == "goal"); option = topic.options[0]
    first = service.vote(session.session_id, topic.topic_id, VoteRequest(member_id="internal-1", option_id=option.option_id, choice="AGREE"))
    current = next(item for item in first.topics if item.topic_id == topic.topic_id)
    assert not current.vote_summary.revealed and current.vote_summary.results == []
    second = service.vote(session.session_id, topic.topic_id, VoteRequest(member_id="internal-2", option_id=option.option_id, choice="AGREE"))
    current = next(item for item in second.topics if item.topic_id == topic.topic_id)
    assert current.status == "APPROVED" and current.vote_summary.revealed
    assert {item.nickname for item in current.vote_summary.results} == {"지민", "서준"}


def test_member_can_agree_to_only_one_option(db):
    session = create_session(db)
    send(db, session.session_id, "internal-1", "주 2회 진행해요."); send(db, session.session_id, "internal-2", "주 5회가 좋아요.")
    service = ConversationEvaluationService(db, MockPlanningProvider(), SETTINGS)
    evaluated = service.evaluate(session.session_id, "internal-1")
    topic = next(item for item in evaluated.topics if item.dimension == "frequency")
    service.vote(session.session_id, topic.topic_id, VoteRequest(member_id="internal-1", option_id=topic.options[0].option_id, choice="AGREE"))
    result = service.vote(session.session_id, topic.topic_id, VoteRequest(member_id="internal-1", option_id=topic.options[1].option_id, choice="AGREE"))
    current = next(item for item in result.topics if item.topic_id == topic.topic_id)
    assert current.vote_summary.my_votes == {topic.options[1].option_id: "AGREE"}


def test_date_and_privacy_values_are_normalized(db):
    session = create_session(db); model = db.get(KickoffSession, session.session_id)
    service = ConversationEvaluationService(db, MockPlanningProvider(), SETTINGS)
    timeline, valid, inferred = service._normalize_option("timeline", {"start_date": "11/09", "end_date": "11/20"}, model)
    privacy, privacy_valid, _ = service._normalize_option("privacy", {"scope": "완료 여부만 공개"}, model)
    assert valid and inferred and timeline == {"start_date": "2026-11-09", "end_date": "2026-11-20"}
    assert privacy_valid and privacy == {"scope": "OBJECTIVE_ONLY"}
    frequency, frequency_valid, _ = service._normalize_option("frequency", {"times_per_week": 2}, model)
    checkin, checkin_valid, _ = service._normalize_option("checkin", {"day_of_week": "FRI", "time": "20:00"}, model)
    metric, metric_valid, _ = service._normalize_option("metric_target", {"books_per_person": 2}, model)
    assert frequency_valid and frequency == {"per_week": 2}
    assert checkin_valid and checkin == {"days": ["FRI"], "time": "20:00"}
    assert metric_valid and metric == {"target_value": 2, "unit": "권", "metric": "개인별 목표 달성량"}
    assert service._public_text("지민(internal-1)과 internal-2가 제안", {"internal-1": "지민", "internal-2": "서준"}) == "지민과 서준가 제안"


def test_plan_generation_requires_decisions_and_intakes(db):
    session = create_session(db); full_conversation(db, session.session_id)
    service = ConversationEvaluationService(db, MockPlanningProvider(), SETTINGS); service.evaluate(session.session_id, "internal-1")
    with pytest.raises(DecisionsNotReady): service.generate_plan(session.session_id, "not-ready-key")


class FailingProvider(MockPlanningProvider):
    def evaluate_conversation(self, messages, prompt):
        raise ProviderError("TIMEOUT", "upstream timed out")


def test_failed_reevaluation_preserves_previous_snapshot(db):
    session = create_session(db); full_conversation(db, session.session_id)
    service = ConversationEvaluationService(db, MockPlanningProvider(), SETTINGS)
    previous = service.evaluate(session.session_id, "internal-1")
    send(db, session.session_id, "internal-3", "추가 의견이 있어요.")
    with pytest.raises(ProviderError): ConversationEvaluationService(db, FailingProvider(), SETTINGS).evaluate(session.session_id, "internal-1")
    assert service.get_latest(session.session_id, "internal-1").evaluation_id == previous.evaluation_id
