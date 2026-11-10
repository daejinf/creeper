"""Explicit authored test transport, not an automatic production fallback."""
import json
from wemeet_ai.m0.client import Completion
from wemeet_ai.shared.contracts import Usage
try:
    from ._data import CONTEXT, plan, sample
except ImportError:
    from _data import CONTEXT, plan, sample

class FixtureClient:
    model = "AUTHORED FIXTURE · AI 미실행"
    base_url = "fixture://local"
    is_fixture = True
    def __init__(self): self.calls = []
    def complete_text(self, messages, options=None, *, stats=None):
        self.calls.append(messages)
        return Completion("참여 방식을 선택할 수 있는 진행안이 목표군요. 먼저 익명 한 문장 제출과 자유 발언 두 방식으로 작은 초안을 만들 수 있어요. 기간과 인증은 이미 대화에 있어 다시 묻지 않을게요.", self.model, None, "stop", Usage(), 0, 0)
    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        self.calls.append(messages)
        req = json.loads(messages[1]["content"])
        name = output_schema.__name__
        if name == "Analysis":
            facts=sample('C')['facts']
            for key,fact in facts.items():fact['evidence']=[{'source':'USER_MESSAGE','source_id':'m2' if key in {'execution_method','verification_method','privacy_scope'} else 'm1'}]
            data = {"summary": "참여 부담을 줄이는 교류회 진행안 논의 · 작성된 fixture", "facts": facts, "missing": ["역할별 작업 범위"]}
        elif name == "GoalDraft": data = {"fields": req["facts"]}
        elif name == "CoachModel":
            state = req["state"]; order = ["current_level", "weekly_available_minutes", "current_progress", "tools"]
            target = next((k for k in order if k not in state["answer_states"]), None)
            labels = {"current_level": "현재 경험", "weekly_available_minutes": "주간 가능 시간", "current_progress": "현재 진척", "tools": "사용 도구"}
            values = {"current_level": "일부 경험", "weekly_available_minutes": 30, "current_progress": "아직 시작 전", "tools": ["공유 문서"]}
            data = {"extracted_fields": {}, "reply": "지금까지 알려주신 조건에 맞춰 작업 범위를 조정하고 있어요. 초기에는 경험·시간·진척·도구를 확인합니다.", "dimensions": [{"field": k, "label": labels[k], "reason": "중복 작업과 시간 초과를 피하기 위해 확인합니다."} for k in order], "question": {"field": target, "text": labels[target] + "은 어떤가요?", "reason": "실행할 작업 범위를 맞추기 위해 필요합니다.", "options": [{"option_id": "yes", "label": " / ".join(values[target]) if isinstance(values[target],list) else str(values[target]), "value": values[target]}, {"option_id": "unknown", "label": "아직 모르겠어요", "action": "UNKNOWN"}]} if target else None}
        elif name == "MemberDraft": data = {"contribution": {"title": "참여 설계", "deliverable": "안내 문서 한 장", "completion_criteria": CONTEXT["completion_criteria"]}, "profile_fields": req["state"]["fields"]}
        else:
            owner = req.get("owner_member_id")
            data = plan(owner, min(20, req.get("profile", {}).get("weekly_available_minutes", 20)))
            context = req.get("context", req)
            data.update(goal=context.get("goal", CONTEXT["goal"]), start_date=context.get("start_date", CONTEXT["start_date"]), end_date=context.get("end_date", CONTEXT["end_date"]))
        out = output_schema.model_validate(data)
        return Completion(out.model_dump_json(), self.model, None, "stop", Usage(), 0, 0, out)
