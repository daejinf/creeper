"""M1-E: adaptive private intake and independent member planning."""
from datetime import date
import re
from typing import Any, Literal
from uuid import uuid4
from pydantic import Field, model_validator
from ...shared.contracts import Contract, GoalContext, Message, Plan, ReviewField
from ...m0 import Runtime, AIError
from ...m0.runtime import code_result
from ..f_validation import inspect_plan
from ...shared.plans import assign_ids

FIELDS = {"current_level", "weekly_available_minutes", "scope", "current_progress", "tools", "preferred_times", "constraints", "support_needed"}
PersonalField = Literal["current_level", "weekly_available_minutes", "scope", "current_progress", "tools", "preferred_times", "constraints", "support_needed"]

class AnswerOption(Contract):
    option_id: str
    label: str
    value: Any = None
    action: Literal["VALUE", "UNKNOWN", "SKIP"] = "VALUE"

class Question(Contract):
    question_id: str = ""
    field: PersonalField
    text: str
    reason: str
    topic_id: str = ""
    refinement_reason: str | None = None
    options: list[AnswerOption] = Field(default_factory=list, max_length=6)

class Dimension(Contract):
    field: PersonalField
    label: str
    reason: str
    required: bool = False

class MemberState(Contract):
    owner_member_id: str = ""
    context_key: str = ""
    fields: dict[str, ReviewField] = Field(default_factory=dict)
    answer_states: dict[str, Literal["KNOWN", "UNKNOWN", "SKIPPED"]] = Field(default_factory=dict)
    dimensions: list[Dimension] = Field(default_factory=list)
    question: Question | None = None
    answered_question_ids: list[str] = Field(default_factory=list)
    answered_topics: list[str] = Field(default_factory=list)
    answered_since_summary: int = 0
    refinements: dict[str, ReviewField] = Field(default_factory=dict)
    refinement_states: dict[str, Literal["KNOWN", "UNKNOWN", "SKIPPED"]] = Field(default_factory=dict)

class ButtonAnswer(Contract):
    question_id: str
    option_id: str
    message_id: str

class MemberCoachRequest(Contract):
    context: GoalContext
    owner_member_id: str
    conversation: list[Message] = Field(default_factory=list)
    state: MemberState = Field(default_factory=MemberState)
    answer: ButtonAnswer | None = None
    reference_date: date = Field(default_factory=date.today)
    @model_validator(mode="after")
    def private_owner(self):
        if self.state.owner_member_id and self.state.owner_member_id != self.owner_member_id: raise ValueError("Other member state")
        if any(m.role == "user" and m.speaker_id != self.owner_member_id for m in self.conversation): raise ValueError("Other member private message")
        ids = [m.message_id for m in self.conversation]
        if len(ids) != len(set(ids)): raise ValueError("Duplicate message ID")
        if set(self.state.fields) - FIELDS or set(self.state.answer_states) - FIELDS: raise ValueError("Unknown personal field")
        return self

class CoachModel(Contract):
    reply: str
    dimensions: list[Dimension] = Field(min_length=4, max_length=6)
    extracted_fields: dict[PersonalField, ReviewField]
    answer_states: dict[PersonalField, Literal["KNOWN", "UNKNOWN", "SKIPPED"]] = Field(default_factory=dict)
    question: Question | None = None

class MemberCoachReply(Contract):
    reply: str
    state: MemberState
    remaining_core: list[str] = Field(default_factory=list)
    remaining_optional: list[str] = Field(default_factory=list)
    can_draft: bool = True
    can_generate_plan: bool = False
    summary_checkpoint: bool = False

def valid_value(field, value):
    if field == "weekly_available_minutes": return type(value) is int and 0 < value <= 10080
    if field in {"current_level", "scope", "current_progress"}: return isinstance(value, str) and bool(value.strip())
    return isinstance(value, list) and all(isinstance(v, str) for v in value)

def normalize_value(field, value):
    """Only lossless, known representations; never guess an unknown answer."""
    import re
    if field == "weekly_available_minutes":
        if isinstance(value, dict) and set(value) == {"minutes"}: value = value["minutes"]
        elif isinstance(value, dict) and set(value) == {"hours"} and type(value["hours"]) in (int, float): value = value["hours"] * 60
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(시간|분)?\s*", value)
            if match:
                count = float(match[1]) * (60 if match[2] == "시간" else 1)
                if count.is_integer(): value = int(count)
        if type(value) is float and value.is_integer(): value = int(value)
    elif field in {"tools", "preferred_times", "constraints", "support_needed"} and isinstance(value, str) and value.strip():
        if value.strip() in {"없음", "별도 제약 없음", "별도 도움 필요 없음"}: return []
        if value.strip() not in {"모름", "건너뛰기", "아직 모르겠어요"}: return [value.strip()]
    return value

def update_member_condition(state: MemberState, field: str, *, value=None, status="KNOWN", actor_id: str):
    """Explicit SW/user edit, with no model call. Returns new state; never mutates input."""
    if field not in FIELDS or status not in {"KNOWN", "UNKNOWN", "SKIPPED"}:
        return code_result("E.review", state, needed=["지원하는 개인 항목과 상태를 선택해주세요."])
    if status == "KNOWN" and not valid_value(field, value):
        return code_result("E.review", state, needed=[field + ": 값의 형식을 확인해주세요."])
    if status == "SKIPPED" and field in {"current_level", "weekly_available_minutes", "scope", "current_progress"}:
        return code_result("E.review", state, needed=[field + ": 모름으로 보존할 수 있습니다."])
    from ...shared.contracts import Evidence
    out = state.model_copy(deep=True)
    out.fields[field] = ReviewField(value=value if status == "KNOWN" else None, origin="EXTRACTED" if status == "KNOWN" else "UNRESOLVED",
        review_status="EDITED", evidence=[Evidence(source="SW_CONFIRMED", source_id=actor_id)], reason="본인이 직접 수정한 조건")
    out.answer_states[field] = status
    if out.question and out.question.field == field:
        out.answered_question_ids.append(out.question.question_id)
        out.question = None
    return code_result("E.review", out)

def coach_member_goal(request: MemberCoachRequest, *, runtime: Runtime | None = None, extra_policy: str = ""):
    """`extra_policy` is appended to the built-in policy by the MOIM adapter (turn budget, required fields)."""
    import hashlib
    state = request.state.model_copy(deep=True)
    state.owner_member_id = request.owner_member_id
    context_key = hashlib.sha256(request.context.model_dump_json().encode()).hexdigest()
    if state.context_key and state.context_key != context_key:
        state.question = None
        state.dimensions = []
        state.fields.pop("scope", None)
        state.answer_states.pop("scope", None)
        state.refinements.clear()
        state.refinement_states.clear()
    state.context_key = context_key
    if not request.context.goal: return code_result("E.coach", needed=["context.goal: 공동 목표 설명이 필요합니다."])
    refinement_field=None
    if request.answer:
        answer = request.answer
        if answer.question_id in state.answered_question_ids:
            return code_result("E.coach", MemberCoachReply(reply="이미 반영한 답변입니다.", state=state))
        question = state.question
        if not question or question.question_id != answer.question_id: return code_result("E.coach", needed=["현재 질문의 선택지를 사용해주세요."])
        option = next((o for o in question.options if o.option_id == answer.option_id), None)
        ids = {m.message_id for m in request.conversation if m.role == "user"}
        if not option or answer.message_id not in ids: return code_result("E.coach", needed=["선택지와 본인 답변 메시지 근거를 확인해주세요."])
        if option.action == "VALUE" and not valid_value(question.field, option.value): return code_result("E.coach", needed=["선택지 값의 형식을 확인해주세요."])
        status = {"VALUE": "KNOWN", "UNKNOWN": "UNKNOWN", "SKIP": "SKIPPED"}[option.action]
        from ...shared.contracts import Evidence
        answer_fact=ReviewField(value=option.value if status == "KNOWN" else None,origin="EXTRACTED" if status=="KNOWN" else "UNRESOLVED",evidence=[Evidence(source="USER_MESSAGE",source_id=answer.message_id)])
        if question.refinement_reason and question.topic_id and state.answer_states.get(question.field)=='KNOWN':
            # Unknown deliverable detail must not erase an already known two-screen scope.
            refinement_field=question.field
            answer_fact.reason=question.field+' · '+question.text
            state.refinements[question.topic_id]=answer_fact
            state.refinement_states[question.topic_id]=status
        else:
            state.fields[question.field]=answer_fact
            state.answer_states[question.field]=status
        state.answered_question_ids.append(question.question_id)
        state.answered_topics.append(question.topic_id or question.field)
        state.question = None
        state.answered_since_summary += 1
    elif request.conversation and state.question:
        state.answered_topics.append(state.question.topic_id or state.question.field)
        state.answered_since_summary += 1
    payload = request.model_copy(update={"state": state, "answer": None})
    unconfirmed_states = []
    def check(out):
        ids = {m.message_id for m in request.conversation if m.role == "user"}
        # MOIM: a fact the model only suggests (AI_SUGGESTED, or backed by the shared context instead of the member's own
        # words) is not a personal fact. It is dropped here, never stored, and the member is simply asked again. The LIVE
        # model did this for `scope`; failing the whole turn for it made the interview unusable. Facts that claim a user
        # message that does not exist still fail below.
        for field in [f for f, fact in out.extracted_fields.items() if fact.origin == "AI_SUGGESTED" or any(e.source != "USER_MESSAGE" for e in fact.evidence)]:
            out.extracted_fields.pop(field)
            out.answer_states.pop(field, None)
        # The public role/context is not a private answer. Before the first
        # personal reply there can be no newly extracted personal facts.
        if not ids:
            out.extracted_fields = {}
            out.answer_states = {}
        required = [Dimension(field="current_level", label="현재 경험", reason="작업 난이도를 맞추기 위해 확인합니다.", required=True),
                    Dimension(field="weekly_available_minutes", label="주간 가능 시간", reason="시간 예산을 초과하지 않게 작업량을 정합니다.", required=True)]
        by_field = {d.field: d for d in out.dimensions}
        for d in required:
            if d.field not in by_field: by_field[d.field] = d
            by_field[d.field].required = True
        order = [d.field for d in required] + ([out.question.field] if out.question else []) + [d.field for d in out.dimensions]
        out.dimensions = [by_field[k] for k in dict.fromkeys(order) if k in by_field][:6]
        dims = [d.field for d in out.dimensions]
        if "마지막 하나" in out.reply or "마지막으로 하나" in out.reply: raise AIError("COACH_POLICY", "확인이 끝난다는 약속을 반복하지 마세요.")
        if len(set(dims)) != len(dims) or set(dims) - FIELDS: raise AIError("INVALID_FIELD", "진단 항목은 허용된 서로 다른 개인 필드여야 합니다.")
        for field, fact in out.extracted_fields.items():
            fact.value = normalize_value(field, fact.value)
            if field not in FIELDS or (fact.value is not None and not valid_value(field, fact.value)): raise AIError("SCHEMA_INVALID", "개인 조건 형식: " + field)
            if fact.value is not None and (not fact.evidence or any(e.source != "USER_MESSAGE" or e.source_id not in ids for e in fact.evidence)): raise AIError("INVALID_EVIDENCE", "본인 발언 근거가 필요합니다.")
        # Explicit minute budgets cannot disappear merely because the prose reply
        # sounds correct. Unknown/ambiguous expressions remain the model's task.
        for message in reversed(request.conversation):
            if message.role!='user':continue
            matches=re.findall(r'(?:주당|주간|일주일에?|주)\s*(\d+(?:\.\d+)?)\s*(시간|분)',message.text)
            if len(matches)!=1:continue
            raw,unit=matches[0];minutes=float(raw)*(60 if unit=='시간' else 1)
            if not minutes.is_integer() or not 0<minutes<=10080:continue
            if 'weekly_available_minutes' not in out.extracted_fields and state.answer_states.get('weekly_available_minutes')!='KNOWN':
                from ...shared.contracts import Evidence
                out.extracted_fields['weekly_available_minutes']=ReviewField(value=int(minutes),origin='EXTRACTED',evidence=[Evidence(source='USER_MESSAGE',source_id=message.message_id)],reason='본인이 명시한 주간 시간 예산')
            break
        level_text=any(m.role=='user' and re.search(r'해봤|해본|경험|협업.*처음|초보|숙련',m.text) for m in request.conversation)
        if level_text and state.answer_states.get('current_level')!='KNOWN' and not out.extracted_fields.get('current_level'):
            raise AIError('MISSING_EXTRACTED_FACT','이미 말한 경험을 current_level과 본인 근거 ID로 추출하세요. reply에서만 이해했다고 하지 마세요.')
        for field, status in list(out.answer_states.items()):
            fact = out.extracted_fields.get(field)
            if status == state.answer_states.get(field) and field in state.fields:
                continue
            if status == "KNOWN" and fact is not None and fact.value is None:
                raise AIError("INVALID_EVIDENCE", "확인된 값은 null일 수 없습니다.")
            if field not in FIELDS or not fact or not fact.evidence or any(e.source != "USER_MESSAGE" or e.source_id not in ids for e in fact.evidence):
                raise AIError("INVALID_EVIDENCE", "모름/건너뜀에도 본인 답변 근거가 필요합니다.")
            if status in {"UNKNOWN", "SKIPPED"}:
                evidence_text=' '.join(m.text for m in request.conversation if m.message_id in {e.source_id for e in fact.evidence})
                pattern=r'모르|모름|미정|정하지|not sure|don.t know' if status=='UNKNOWN' else r'건너|넘길|나중에|비공개|말하고 싶지|제공하지|답하기 어려|skip'
                if not re.search(pattern,evidence_text,re.I):
                    out.answer_states.pop(field)
                    if fact.value is None:out.extracted_fields.pop(field)
                    unconfirmed_states.append(field)
                    continue
            if status == "SKIPPED" and field not in {"constraints", "support_needed", "preferred_times", "tools"}:
                raise AIError("INVALID_FIELD", "이 필드는 미확인 상태로 보존하세요.")
        if out.question:
            q = out.question
            answered = state.answer_states.get(q.field)
            extracted = out.extracted_fields.get(q.field)
            refining = bool(q.refinement_reason and q.topic_id and q.topic_id not in state.answered_topics)
            repeated = answered in {"UNKNOWN", "SKIPPED"} or q.topic_id and q.topic_id in state.answered_topics or (answered == "KNOWN" or extracted and extracted.value is not None or q.field in out.answer_states) and not refining
            if q.field not in dims:
                # MOIM: the open question's field is part of the diagnosis, so add it instead of failing the turn (at most 6 items).
                if len(out.dimensions) >= 6: raise AIError("INVALID_FIELD", "진단 항목 안에서 질문하세요.")
                out.dimensions.append(Dimension(field=q.field, label=q.field, reason=""))
                dims.append(q.field)
            if repeated:
                out.question = None
                out.reply = out.reply.replace(q.text, "").strip() + "\n이미 확인된 내용은 다시 묻지 않고 현재 조건을 활용하겠습니다."
                return  # Keep valid facts; a repeated optional question must not destroy progress.
            usable = []
            for o in q.options:
                o.value = normalize_value(q.field, o.value)
                # MOIM: an unusable button is dropped instead of failing the turn (this free-form coach is no longer called by MOIM; see interpret_member_answer).
                if o.action == "VALUE" and not valid_value(q.field, o.value): continue
                if o.action == "SKIP" and q.field in {"current_level", "weekly_available_minutes", "scope"}: continue
                usable.append(o)
            q.options = usable
        if "마지막 하나" in out.reply or "마지막으로 하나" in out.reply: raise AIError("COACH_POLICY", "확인이 끝난다는 약속을 반복하지 마세요.")
    policy = """본인 역할에 맞는 개인 코치입니다. 공동 목표/역할/일정만 있어도 시작합니다.
수준·주간시간만 묻고 끝내지 말고 scope/current_progress/tools/preferred_times/constraints/support_needed 중 계획을 바꾸는 항목을 포함해 4~6개 dimensions를 선정하세요.
우선 전체 본인 대화에서 이미 말한 수준·주간 시간·범위·진척·도구를 extracted_fields에 근거 ID와 함께 반드시 추출하세요. extracted_fields는 필수입니다. reply에서 정리한 사실을 JSON에서 누락하면 안 됩니다.
처음에는 확인할 항목들을 함께 알리고, 가장 영향이 큰 질문 하나를 question으로 제시합니다. reply에 또 다른 질문을 붙이지 마세요.
현재 수준=current_level(문자열), 주간 시간=weekly_available_minutes(정수 분), scope/current_progress=문자열, 나머지는 문자열 목록입니다.
질문에는 상황별 버튼 VALUE, UNKNOWN(모름)을 제공하고 민감한 제약은 SKIP도 제공합니다. 모름/건너뜀/없음을 구분합니다.
current_level/weekly_available_minutes는 진단 항목에 항상 포함하세요. 필드 ID는 JSON 안에서만 쓰고 reply는 쉬운 한국어로 설명하세요.
이미 알려진 값과 이번 답변에서 추출한 값은 재질문하지 마세요. UNKNOWN/SKIPPED를 다시 묻지 마세요.
단순 역할에서 실제 결과물로 범위를 구체화하는 등 같은 필드라도 작업 계획이 달라질 세부 확인은 topic_id와 refinement_reason으로 이유를 명시하세요. 이미 답한 topic_id를 반복하지 마세요.
두 답변 후에는 요약과 작업량 변화를 먼저 설명하고 필요한 다음 질문은 계속합니다. 2회 제한은 없습니다.
계획 요청에는 실행 초안을 우선 제공합니다. 사소한 문장 선택을 필수 질문으로 만들지 마세요.
주30분이면 작업 범위를 줄이거나 도움 요청을 제안합니다. 확정 배정이라고 말하지 마세요.
개인 조건은 본인 사용자 발언 evidence만 사용합니다. AI 추정은 extracted_fields에 넣지 마세요.
개인 사용자 발언이 아직 없으면 extracted_fields와 answer_states는 반드시 빈 객체 {}입니다. 공개 역할은 개인 수준·시간·진척의 근거가 아닙니다. 첫 질문과 기여 제안만 작성하세요.
answer_states에는 이번 답변에서 새로 확인한 상태만 넣으세요. 기존 state의 상태를 반복 복사하지 마세요.
정보가 없다는 것과 사용자가 모른다고 답한 것은 다릅니다. 답하지 않은 항목을 UNKNOWN/SKIPPED로 저장하지 마세요.
이미 정해진 공동 규칙을 개인 제약 질문으로 다시 묻지 마세요. 개인 제약은 본인의 일정·도구 접근·환경에 관한 선택적 질문입니다.
자유 입력의 모름/건너뜀은 extracted_fields의 null 값과 본인 evidence를 붙이고 answer_states의 UNKNOWN/SKIPPED로 기록합니다. 제약 없음은 VALUE의 빈 목록이며 건너뜀과 다릅니다.
state.refinements/refinement_states는 기존 사실의 세부 답변입니다. 세부사항에 모름을 답했다고 이미 확인한 상위 범위·시간·수준을 미정으로 바꾸지 마세요. 모른다고 답한 세부 주제는 다시 묻지 말고 초안 제안으로 남깁니다.
기여 결과물/완료 기준/수준/시간예산이 실질적으로 확인됐으면 진단을 마무리해도 됩니다. 추가 선택 정보가 없다고 막지 마세요."""
    if extra_policy:
        policy += "\n" + extra_policy
    result =(runtime or Runtime()).run("E.coach", payload, CoachModel, policy, check=check, owner=request.owner_member_id)
    if unconfirmed_states:result.warnings.append("아직 답하지 않은 항목은 모름·건너뜀 선택으로 저장하지 않았습니다.")
    if result.status != "OK":
        result.data = MemberCoachReply(reply="답변은 보존했습니다. AI 응답만 다시 요청할 수 있습니다.", state=state)
        return result
    output = result.data
    public_labels = {d.field: d.label for d in output.dimensions}
    for key, label in public_labels.items(): output.reply = output.reply.replace(key, label)
    for key, fact in output.extracted_fields.items():
        if refinement_field==key:continue
        previous = state.fields.get(key)
        if request.answer and previous and any(e.source_id == request.answer.message_id for e in previous.evidence): continue
        if state.answer_states.get(key) in {"UNKNOWN", "SKIPPED"} and previous and set(e.source_id for e in fact.evidence) <= set(e.source_id for e in previous.evidence): continue
        special = output.answer_states.get(key)
        if special in {"UNKNOWN", "SKIPPED"}:
            fact.value = None
            state.fields[key] = fact
            state.answer_states[key] = special
        elif fact.value is not None:
            fact.review_status = "PENDING"
            state.fields[key] = fact
            state.answer_states[key] = "KNOWN"
    state.dimensions = output.dimensions
    state.question = output.question
    if state.question: state.question.question_id = str(uuid4())
    checkpoint = state.answered_since_summary >= 2
    if checkpoint: state.answered_since_summary = 0
    core = [k for k in ("current_level", "weekly_available_minutes", "scope") if state.answer_states.get(k) != "KNOWN"]
    optional = [d.field for d in state.dimensions if d.field not in core and d.field not in state.answer_states]
    result.data = MemberCoachReply(reply=output.reply, state=state, remaining_core=core, remaining_optional=optional, can_generate_plan=not core, summary_checkpoint=checkpoint)
    return result

class ExtraFact(Contract):
    field: PersonalField
    value: Any
    quote: str

class NextOption(Contract):
    label: str
    value: Any = None

class NextQuestion(Contract):
    field: PersonalField
    text: str
    options: list[NextOption] = Field(default_factory=list, max_length=4)

class TurnModel(Contract):
    """MOIM: one interview turn. The AI reads the member's answer AND leads the conversation: it writes the next question and
    its answer buttons. The server validates every part (quotes, values, field, options) before anything is stored or shown."""
    understood: bool = True
    value: Any = None
    quote: str = ""
    extras: list[ExtraFact] = Field(default_factory=list, max_length=4)
    reply: str = ""
    next: NextQuestion | None = None

class TurnRequest(Contract):
    owner_member_id: str
    project: dict
    question_field: PersonalField
    question: str
    answer: str = Field(min_length=1, max_length=300)
    answer_kind: Literal["text", "button"]
    known: dict[str, Any] = Field(default_factory=dict)
    asked_unknown: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    turns_left: int = Field(ge=0, le=20)

INTERVIEW_POLICY = """너는 MOIM의 개인 인터뷰어다. 팀 프로젝트(project)에서 이 사람(한 명)이 어떤 역할을 맡으면 좋을지 정하려고, 본인에게만 몇 가지를 묻는다.
입력: project(프로젝트 정보), question_field·question(방금 한 질문), answer·answer_kind(사용자 답, text=직접 쓴 글, button=고른 버튼), known(이미 알게 된 정보), asked_unknown(모른다고 답한 항목), missing_required(아직 꼭 필요한 항목), turns_left(남은 질문 횟수).
항목(field): current_level(경험 수준, 문자열), weekly_available_minutes(주간 가능 시간, 정수 분), scope(맡고 싶은 범위, 문자열), constraints(일정·도구 제약, 문자열 목록, 없으면 []), tools(쓸 수 있는 도구, 목록), current_progress(준비한 자료·진행, 문자열), preferred_times(선호 시간대, 목록), support_needed(필요한 도움, 목록).
JSON {"understood": bool, "value": ..., "quote": "...", "extras": [{"field","value","quote"}], "reply": "...", "next": {"field": "...", "text": "...", "options": [{"label": "...", "value": ...}]} | null} 하나만 반환한다.
1) answer_kind가 text이면 answer가 question_field에 대한 답으로 이해되는지 판단한다. 이해되면 understood=true, value는 question_field 형식의 짧고 정확한 값, quote는 answer 안의 근거 문구를 글자 그대로 복사한 것이다. 이해되지 않거나 엉뚱하면 understood=false. answer_kind가 button이면 이미 처리됐으니 value·quote는 비운다.
2) answer에 다른 항목의 정보를 사용자가 분명히 말했으면 extras에 넣는다(각 quote는 answer에서 그대로 복사, scope는 맡고 싶다/할 수 있다고 분명히 말했을 때만, 이미 known인 항목은 사용자가 고쳐 말한 경우만). 목록 항목은 말한 것을 빠짐없이 담는다.
3) next는 네가 다음에 가장 알고 싶은 것 하나다. 이 프로젝트와 지금까지의 답에 맞춰 구체적으로 묻는다(예: 발표 경험이 있다고 했으면 발표 준비 방식, 개발이면 쓰는 언어). field는 known에 없는 항목이고, 이미 물어 답을 받은 항목을 다시 묻지 않는다. missing_required가 비어 있지 않으면 그 안에서 고른다(모른다고 답한 필수 항목은 다른 방식으로 한 번 더 묻는다). answer가 이해되지 않았으면 같은 항목을 더 쉽게 다시 묻는다. 사용자가 질문을 했으면 reply로 한 문장 답하고 이어간다. 더 물을 것이 없고 missing_required가 비어 있으면 next는 null.
4) next.options는 이 질문에 사용자가 실제로 고를 법한 구체적 답 2~4개다(label 20자 이내). value는 항목 형식(weekly_available_minutes는 정수 분, 목록 항목은 문자열 목록, 문자열 항목은 문자열). 모른다/직접 입력은 넣지 않는다(서버가 붙인다).
5) reply는 한 문장 60자 이내. 사용자가 이해되지 않았거나 질문했을 때만 쓰고, 그 밖에는 빈 문자열이다. 항목명·진단·상태 같은 내부 표현은 쓰지 않고, 역할이나 투두를 제안·배정하지 않는다.
6) 출력은 짧게: 불필요한 설명·이유 필드를 만들지 않는다."""

def interview_turn(request: TurnRequest, *, runtime: Runtime | None = None):
    """E.coach in MOIM (D-B2 follow-up): the AI leads the interview; see TurnModel and docs/ARCHITECTURE.md."""
    return (runtime or Runtime()).run("E.coach", request, TurnModel, INTERVIEW_POLICY, owner=request.owner_member_id)

class Contribution(Contract):
    title: str
    deliverable: str
    completion_criteria: list[str] = Field(default_factory=list)
    confirmed: bool = False

class MemberDraft(Contract):
    contribution: Contribution
    profile_fields: dict[str, ReviewField] = Field(default_factory=dict)
    unanswered: list[str] = Field(default_factory=list)

def draft_member_goal(request: MemberCoachRequest, *, runtime: Runtime | None = None):
    def check(out):
        out.contribution.confirmed = False
        ids = {m.message_id for m in request.conversation if m.role == "user"}
        for key, f in out.profile_fields.items():
            f.value = normalize_value(key, f.value)
            known = request.state.fields.get(key)
            if known and f.value == known.value:
                out.profile_fields[key] = known.model_copy(deep=True)
                continue
            if key not in FIELDS or f.value is not None and not valid_value(key, f.value): raise AIError("SCHEMA_INVALID", "개인 조건 형식을 확인하세요.")
            if f.value is not None and (not f.evidence or any(e.source != "USER_MESSAGE" or e.source_id not in ids for e in f.evidence)): raise AIError("INVALID_EVIDENCE", "본인 근거가 필요합니다.")
    return (runtime or Runtime()).run("E.draft", request, MemberDraft,
        "공개 목표 안에서 본인이 맡을 결과물·완료 기준을 추천하고 본인 발언으로 확인한 조건만 profile_fields로 정리하세요. 없는 개인 시간/수준을 만들지 마세요. 미정은 unanswered에 기록하세요. 비공개 사유는 contribution에 쓰지 마세요. confirmed=false입니다.", check=check, owner=request.owner_member_id)

class MemberProfile(Contract):
    current_level: str = ""
    weekly_available_minutes: int | None = Field(default=None, gt=0, le=10080)
    confirmed: bool = False
    constraints: list[str] = Field(default_factory=list)
    private_notes: list[str] = Field(default_factory=list)

class MemberPlanRequest(Contract):
    context: GoalContext
    owner_member_id: str
    contribution: Contribution
    profile: MemberProfile

def generate_member_plan(request: MemberPlanRequest, *, runtime: Runtime | None = None):
    missing = []
    for k in ("goal", "start_date", "end_date"):
        if not getattr(request.context, k): missing.append("context." + k)
    if not request.profile.current_level: missing.append("profile.current_level")
    if not request.profile.weekly_available_minutes: missing.append("profile.weekly_available_minutes")
    if not request.profile.confirmed: missing.append("profile.confirmed")
    if not request.contribution.confirmed: missing.append("contribution.confirmed")
    if not request.contribution.completion_criteria: missing.append("contribution.completion_criteria")
    if missing: return code_result("E.plan", needed=missing)
    # Data minimization is the primary privacy boundary. Private reasons never reach public generation.
    public_input = request.model_copy(update={"profile": request.profile.model_copy(update={"constraints": [], "private_notes": []})})
    def check(plan):
        if plan.owner_member_id != request.owner_member_id: raise AIError("OWNER_MISMATCH", "본인의 계획만 생성하세요.")
        issues = inspect_plan(plan, request.context, {request.owner_member_id: request.profile.weekly_available_minutes})
        if issues: raise AIError("PLAN_INVALID", str([i.model_dump() for i in issues]))
        assign_ids(plan)
    from datetime import timedelta
    windows=[]
    begin=request.context.start_date
    while begin<=request.context.end_date:
        end=min(begin+timedelta(days=6),request.context.end_date)
        windows.append(f'{begin.isoformat()}~{end.isoformat()}: 모든 과제 예상 시간 합계 {request.profile.weekly_available_minutes}분 이하')
        begin+=timedelta(days=7)
    budget_policy=' 코드 검증의 주차는 월요일 기준이 아니라 시작일로부터 7일씩입니다. '+ '; '.join(windows)
    result = (runtime or Runtime()).run("E.plan", public_input, Plan,
        """본인 기여 결과물의 주차별 성과와 첫7일 과제를 구체적으로 작성하세요. goal은 context.goal과 같아야 합니다.
확인된 날짜와 주간 시간 예산을 지키고 결과물·난이도·분량·작업 순서를 수준에 맞추세요.
30분과300분 계획은 작업 범위가 달라야 합니다. 작은 예산이면 최소 결과물과 도움 요청을 제안하세요.
owner_member_id는 본인, 과제도 본인입니다. 공개 과제·완료 기준·일정만 작성하며 개인 사유나 비공개 조언은 쓰지 마세요.
새 plan_version_id/task_id는 빈 문자열, dependencies는 빈 목록입니다. 인증 방식은 context를 따릅니다. 같은 주차에 여러 과제가 있으면 합계로 예산을 지키세요."""+budget_policy, check=check, owner=request.owner_member_id)
    if result.status == "OK":
        if not result.data.plan_version_id: result.data.plan_version_id = str(uuid4())
        for t in result.data.tasks:
            if not t.task_id: t.task_id = str(uuid4())
        if request.profile.constraints: result.warnings.append("비공개 제약 사유는 공개 생성 입력에서 제외했습니다. 날짜별 가용성은 별도 확인이 필요합니다.")
    return result

__all__ = ["MemberCoachRequest", "MemberState", "MemberCoachReply", "Question", "AnswerOption", "ButtonAnswer", "Contribution", "MemberDraft", "MemberProfile", "MemberPlanRequest", "coach_member_goal", "draft_member_goal", "generate_member_plan", "update_member_condition"]
