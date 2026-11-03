"""Anonymous, explicitly loaded examples; never default conversation content."""
from datetime import date, datetime

from .contracts import AnalysisField, ConversationAnalysis, GoalSpec, Member, MemberProfile, PrepareRequest


def example_input(name="project") -> PrepareRequest:
    if name not in {"project", "study", "exercise"}: raise ValueError("Unknown example")
    goals = {
        "project": ("WE·MEET 목표 설정 데모 완성", "실행 가능한 목표 입력·계획 조회 데모", ["목표 입력부터 계획 조회까지 시연", "핵심 흐름 테스트 통과"], "공동 결과물 구현", "시연 영상과 테스트 결과"),
        "study": ("파이썬 문제 풀이 습관 만들기", "기초 문법을 이용해 각자 문제 12개 해결", ["각자 정답 코드와 풀이 설명 12개 제출"], "기초 문법 학습 후 문제 풀이", "정답 코드와 풀이 설명"),
        "exercise": ("꾸준한 걷기 습관 만들기", "각자 12km 걷기", ["각자 누적 12km 기록 제출"], "무리하지 않는 속도로 걷기", "거리 기록 화면"),
    }
    goal, outcome, criteria, execution, verification = goals[name]
    decisions = {"confirmed": True, "goal": goal, "expected_outcome": outcome, "completion_criteria": criteria, "start_date": "2026-11-01", "end_date": "2026-11-14", "frequency_per_week": 2, "execution_method": execution, "verification_method": verification, "privacy_scope": "OBJECTIVE_ONLY"}
    if name != "project": decisions["metric"] = {"name": "문제 풀이" if name == "study" else "걷기 거리", "target": 12, "unit": "문제" if name == "study" else "km", "allocation": "PER_MEMBER"}
    members = [Member(member_id="demo-j", nickname="지민"), Member(member_id="demo-s", nickname="서준")]
    analysis = ConversationAnalysis(summary="테스트 도구에서 명시적으로 선택한 익명 예제", fields={"goal": AnalysisField(status="PARTIAL")}, members=members, evidence_ids=["example-public-1"])
    return PrepareRequest(analysis=analysis, confirmed_decisions=decisions, member_profiles=[
        MemberProfile(member_id="demo-j", current_level="BEGINNER", weekly_available_minutes=60, preferred_times=["화·토 저녁"], constraints=["화요일에는 30분까지만 가능"]),
        MemberProfile(member_id="demo-s", current_level="ADVANCED", weekly_available_minutes=300, preferred_times=["수·일 오전"], constraints=[]),
    ])


def example_spec(name="project") -> GoalSpec:
    from .service import GoalAI
    return GoalAI().prepare_goal_spec(example_input(name)).data


def regression_conversation():
    from .contracts import AnalysisRequest, Message
    lines = [
        ("j", "오늘은 위밋 프로젝트 과제를 어떻게 할지 정할 것이다"),
        ("j", "일단은 일정을 먼저 정하자"), ("j", "난 14일 정도 잡고있어"),
        ("h", "3주가 좋아 난"), ("s", "나도 3주가 좋아"),
        ("s", "그러면 3주 ㄱ?"), ("s", "그럼 빈도는 어떻게 하지"),
        ("s", "1주일에 3번 인증?"), ("j", "좋아"),
        ("j", "그럼 체크인 방식은"), ("j", "그냥 이미지 첨부하는 걸로?"),
        ("s", "ㅇㅇㅇ"), ("h", "ㅇㅇㅇㅇ"), ("h", "11/1~11/9일 ㄱ"),
        ("s", "ㄲㄱ"), ("j", "ㄲㄲ"),
    ]
    return AnalysisRequest(reference_date=date(2026, 10, 27), members=[Member(member_id=mid, nickname=nick) for mid, nick in [("j", "지민"), ("s", "서준"), ("h", "하린")]], conversation=[Message(message_id=f"m{i:02}", speaker_id=mid, role="user", text=text, created_at=datetime.fromisoformat("2026-10-27T12:00:00+09:00")) for i, (mid, text) in enumerate(lines)])


def design_conversation():
    from .contracts import AnalysisRequest, Message
    members = [Member(member_id=mid, nickname=nickname) for mid, nickname in [("j", "지민"), ("s", "서준"), ("h", "하린"), ("y", "서윤")]]
    turns = [
        ("j", "오늘 10/28부터 위밋 프로젝트 데모를 함께 개발하자. 11/10 개발 완료, 11/15 최종 제출이야."),
        ("j", "지민은 디자인, 서준은 DB, 하린은 프론트, 서윤은 백엔드 담당이야."),
        ("s", "11/3까지 각자 중간 목표를 진행하고 매주 화요일 사진으로 인증하자."),
        ("h", "완료 여부와 달성률만 그룹에 공개하자."),
        ("j", "내 디자인은 화면 UI와 PPT 둘 다야. 백지에서 시작하고 핵심 화면은 메인이야."),
        ("j", "이번 프로젝트는 목표 입력부터 개인 계획 확인까지 시연되면 완료로 하자."),
        ("j", "그래도 쓸 만할 정도로 너가 직접 만들어줘. 어떻게 해야 하는지 계획 말이야."),
    ]
    return AnalysisRequest(reference_date=date(2026, 10, 28), members=members,
        conversation=[Message(message_id=f"design-{i}", speaker_id=mid, role="user", text=text,
                      created_at=datetime.fromisoformat("2026-10-28T10:00:00+09:00")) for i, (mid, text) in enumerate(turns)])


def design_coaching_regression():
    """The reported long interview loop, retained as an old-history regression."""
    from .contracts import CoachRequest, Message, SharedContext
    group = design_conversation()
    history = [
        ("user", "그래도 쓸 만할 정도로 너가 직접 만들어줘. 어떻게 해야 하는지 계획 말이야."),
        ("assistant", "먼저 하나만 확인할게요. 화면 UI 시안인가요, PPT 디자인인가요?"),
        ("user", "싹 다야"),
        ("assistant", "11/3까지 화면 쪽에 집중하는 계획을 잡아볼게요. 지금 화면이 만들어져 있나요, 백지인가요?"),
        ("user", "백지 디자인이야"),
        ("assistant", "메인 스타일로 서브 화면을 확장해보세요. 핵심 화면은 로그인/메인/목록 중 무엇인가요?"),
        ("user", "메인이지"),
        ("assistant", "메인을 기준으로 잡겠습니다. 마지막으로 메인 화면의 필수 요소가 정해져 있나요?"),
        ("user", "없어"),
        ("assistant", "구성부터 초안을 잡겠습니다. 팀 스케치를 참고할 시간이 따로 있나요?"),
        ("user", "ㄴㄴㄴㄴ"),
        ("assistant", "먼저 구성을 잡아보세요. 한 가지만 확인할게요. 기능 목록이 있나요?"),
        ("user", "더 묻지 말고 지금까지 정한 내용으로 내가 할 계획을 직접 만들어줘."),
    ]
    return CoachRequest(members=group.members, reference_date=group.reference_date, scope="PRIVATE", owner_member_id="j",
        shared_context=SharedContext(goal="위밋 목표 설정 데모 완성", dates={"development":"2026-11-10","submission":"2026-11-15"}, roles={"j":"UI와 PPT 디자인"}, milestones=[{"title":"개인 중간 목표","due_date":"2026-11-03"}]),
        conversation=[Message(message_id=f"loop-{i}", speaker_id="j" if role=="user" else None, role=role, text=text, created_at=datetime.fromisoformat("2026-10-27T12:00:00+09:00")) for i,(role,text) in enumerate(history)])
