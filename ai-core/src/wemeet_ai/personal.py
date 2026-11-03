"""E: private, group-grounded contribution coaching; no UI or database dependency."""
from uuid import uuid4
import re

from .client import AIError
from .contracts import (
    DraftField, GoalSpec, MemberCoachModel, MemberCoachReply, MemberGoalDraft, PersonalQuestion, MemberGoalRequest, Result,
)
from .interactions import questions_in, wants_plan
from .tracing import emit
from .validation import validate_plan


PROFILE_FIELDS = {"current_level", "weekly_available_minutes", "preferred_times", "constraints", "current_progress", "scope", "tools", "support_needed"}
LIST_FIELDS = {"preferred_times", "constraints", "tools", "support_needed"}
PERSONAL_FIELDS = PROFILE_FIELDS | {"contribution_candidate"}


def normalize_personal_value(field,value):
    if field=='weekly_available_minutes':
        if isinstance(value,dict) and set(value)=={'minutes'}:value=value['minutes']
        elif isinstance(value,dict) and set(value)=={'hours'} and type(value['hours']) in (int,float):value=value['hours']*60
        if isinstance(value,str):
            text=value.strip()
            if text.isdigit():return int(text)
            match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*(시간|분)',text)
            if match:
                minutes=float(match[1])*(60 if match[2]=='시간' else 1)
                return int(minutes) if minutes.is_integer() else value
        if type(value) is float and value.is_integer():return int(value)
    if field in LIST_FIELDS and isinstance(value,str) and value.strip():
        return [] if value.strip() in {'없음','별도 제약 없음','필요 없음'} else [value.strip()]
    return value


def check_value(field, value):
    if field == "weekly_available_minutes":
        return type(value) is int and 0 < value <= 10080
    if field in LIST_FIELDS:
        return isinstance(value, list) and all(isinstance(v, str) and bool(v.strip()) for v in value)
    return isinstance(value, str) and bool(value.strip())


def missing_fields(fields):
    return [key for key in ("current_level", "weekly_available_minutes") if key not in fields or not check_value(key, fields[key].value)]


class PersonalModules:
    def _personal_input(self, request):
        report = validate_plan(request.goal_spec, request.group_plan, [], require_all_members=False)
        if not report.valid: raise AIError("PLAN_INVALID", report.model_dump_json())
        state = request.state.model_copy(deep=True)
        if state.owner_member_id and state.owner_member_id != request.owner_member_id:
            raise AIError("PRIVATE_SCOPE", "다른 구성원의 개인 코칭 상태를 전달할 수 없습니다.")
        if request.previous_draft and request.previous_draft.owner_member_id != request.owner_member_id:
            raise AIError("PRIVATE_SCOPE", "다른 구성원의 개인 초안을 전달할 수 없습니다.")
        state.owner_member_id = request.owner_member_id
        known = {m.message_id for m in request.conversation if m.role == "user"}
        if any(not set(field.evidence_refs) <= known for field in state.fields.values()):
            raise AIError("PRIVATE_SCOPE", "개인 상태의 근거가 현재 구성원의 대화에 없습니다.")
        if set(state.fields) - PERSONAL_FIELDS or set(state.answer_states) - PERSONAL_FIELDS:
            raise AIError("SCHEMA_INVALID", "개인 상태에 허용되지 않은 항목이 있습니다.")
        if state.group_plan_id != request.group_plan.plan_id:
            state.current_question = None
            state.consecutive_questions = 0
            state.awaiting_continue = False
            state.diagnostic_dimensions = []
            state.fields.pop("contribution_candidate", None)
            state.answer_states.pop("contribution_candidate", None)
        state.group_plan_id = request.group_plan.plan_id
        if request.continue_diagnosis:
            state.consecutive_questions = 0
            state.awaiting_continue = False
        if request.answer:
            answer, question = request.answer, state.current_question
            if answer.question_id in state.answered_question_ids:
                emit("personal.answer.duplicate", {"question_id": answer.question_id})
                return state, None, True
            if question is None or question.question_id != answer.question_id:
                raise AIError("STALE_QUESTION", "현재 질문과 일치하지 않는 선택입니다.")
            if answer.message_id not in {m.message_id for m in request.conversation if m.role == "user"}:
                raise AIError("SCHEMA_INVALID", "버튼 답변의 개인 메시지 근거가 없습니다.")
            if len(answer.option_ids) != len(set(answer.option_ids)):
                raise AIError("SCHEMA_INVALID", "같은 선택지를 중복 선택할 수 없습니다.")
            chosen = [o for o in question.options if o.option_id in answer.option_ids]
            if len(chosen) != len(answer.option_ids) or (not question.multiple and len(chosen) != 1):
                raise AIError("SCHEMA_INVALID", "질문에 없는 선택지이거나 단일 선택 규칙을 위반했습니다.")
            special = [o for o in chosen if o.action != "VALUE"]
            if special and (len(chosen) != 1 or (special[0].action == "SKIP" and question.target_field not in {"constraints", "support_needed"})):
                raise AIError("SCHEMA_INVALID", "건너뛰기·미확인 선택을 다른 답변과 함께 사용할 수 없습니다.")
            if not special and any(not check_value(question.target_field, o.value) for o in chosen):
                raise AIError("SCHEMA_INVALID", "선택지의 정규화된 값이 잘못됐습니다.")
            value = None if special else chosen[0].value
            if not special and question.multiple:
                value = list(dict.fromkeys(v for option in chosen for v in option.value))
            status = "UNKNOWN" if special and special[0].action == "UNKNOWN" else "SKIPPED" if special else "KNOWN"
            state.fields[question.target_field] = DraftField(value=value, origin="UNRESOLVED" if special else "EXTRACTED", evidence_refs=[answer.message_id], reason="사용자가 선택한 답변", review_status="PENDING")
            state.answer_states[question.target_field] = status
            state.answered_question_ids.append(question.question_id)
            state.current_question = None
            emit("personal.answer.normalized", {"question_id": question.question_id, "field": question.target_field, "answer_state": status, "value": value, "message_id": answer.message_id})
        spec = request.goal_spec.model_dump(mode="json", exclude={"member_profiles"})
        spec["roles"] = {k: v for k, v in spec["roles"].items() if k == request.owner_member_id}
        payload = {"goal_spec": spec, "group_plan": request.group_plan.model_dump(mode="json"),
                   "owner_member_id": request.owner_member_id, "conversation": [m.model_dump(mode="json") for m in request.conversation],
                   "state": state.model_dump(mode="json"), "reference_date": request.reference_date.isoformat(), "timezone": request.timezone}
        return state, payload, False

    @staticmethod
    def _merge_personal(fields, request, state):
        known = {m.message_id for m in request.conversation if m.role == "user"}
        if set(fields) - PROFILE_FIELDS - {"contribution_candidate"}:
            raise AIError("PLAN_INVALID", "허용되지 않은 개인 조건입니다.")
        for key, field in fields.items():
            if not set(field.evidence_refs) <= known: raise AIError("PLAN_INVALID", "개인 조건의 근거 메시지가 잘못됐습니다.")
            if field.value is None: continue
            field.value=normalize_personal_value(key,field.value)
            if field.origin=='AI_SUGGESTED':
                continue  # Estimates belong in answer options until the person selects or confirms them.
            if key in LIST_FIELDS and field.value == [] and not field.evidence_refs:
                continue  # Optional omission is not a claim that the person has no constraints.
            if not check_value(key, field.value) or not field.evidence_refs:
                raise AIError("PLAN_INVALID", "개인 조건은 유효한 값과 실제 사용자 근거가 필요합니다.")
            previous = state.fields.get(key)
            if request.answer and previous and request.answer.message_id in previous.evidence_refs:
                continue  # Explicit button values take precedence over model interpretation.
            if previous and state.answer_states.get(key) in {"UNKNOWN", "SKIPPED"} and not set(field.evidence_refs) - set(previous.evidence_refs):
                continue
            field.origin, field.review_status = "EXTRACTED", "PENDING"
            state.fields[key] = field
            state.answer_states[key] = "KNOWN"

    def coach_member_goal(self, request: MemberGoalRequest) -> Result[MemberCoachReply]:
        def operation(stats):
            state, payload, duplicate = self._personal_input(request)
            contract_issues=[]
            if duplicate:
                return MemberCoachReply(reply="이미 반영한 답변입니다. 현재 개인 목표를 이어서 확인해주세요.", kind="SUMMARY", state=state, missing_fields=missing_fields(state.fields))
            latest = next((m.text for m in reversed(request.conversation) if m.role == "user"), "")
            adaptive = request.diagnostic_mode == "ADAPTIVE"
            allow_question = state.consecutive_questions < 2 and (not wants_plan(latest) or request.continue_diagnosis) and not (adaptive and state.awaiting_continue)
            payload["allow_question"] = allow_question
            payload["diagnostic_mode"] = request.diagnostic_mode

            def check(output):
                probe = state.model_copy(deep=True)
                self._merge_personal(output.extracted_fields, request, probe)
                question = output.question
                if adaptive:
                    dimensions = output.diagnostic_dimensions or state.diagnostic_dimensions
                    ids = [d.field_id for d in dimensions]
                    if not 4 <= len(ids) <= 6 or len(ids) != len(set(ids)) or not {"current_level", "weekly_available_minutes"} <= set(ids):
                        raise AIError("PLAN_INVALID", "목표와 역할에 맞는 진단 항목 4~6개를 선택하고 current_level/weekly_available_minutes를 포함하세요.")
                    if question and question.target_field not in ids:raise AIError("PLAN_INVALID", "질문은 선택한 진단 항목 중에서 제시하세요.")
                askable = [key for key in missing_fields(probe.fields) if probe.answer_states.get(key) not in {"UNKNOWN", "SKIPPED"}]
                if allow_question and askable and (question is None or question.target_field not in askable):
                    raise AIError("PLAN_INVALID", "아직 확인할 필수 개인 조건 " + ", ".join(askable) + " 중 하나를 question과 답변 선택지로 먼저 제시하세요.")
                if questions_in(output.reply): raise AIError("PLAN_INVALID", "질문은 question에만 넣고 reply에 추가 질문을 붙이지 마세요.")
                if not allow_question and re.search(r"알려주세요|알려줄래|하나만.*확인|한 가지만.*확인", output.reply): raise AIError("PLAN_INVALID", "추가 정보 요청 없이 기여 초안을 제시하세요.")
                if question:
                    if not allow_question: raise AIError("PLAN_INVALID", "질문 예산이 없으므로 질문 없이 기여 초안을 제시하세요.")
                    field = question.target_field
                    if field in probe.fields and check_value(field, probe.fields[field].value): raise AIError("PLAN_INVALID", "이미 답한 조건을 재질문하지 마세요.")
                    if probe.answer_states.get(field) in {"UNKNOWN", "SKIPPED"}: raise AIError("PLAN_INVALID", "미확인·건너뛴 항목을 즉시 재질문하지 마세요.")
                    if question.multiple and field not in LIST_FIELDS: raise AIError("PLAN_INVALID", "이 필드는 단일 선택입니다.")
                    for option in question.options:
                        option.value=normalize_personal_value(field,option.value)
                        if option.action=='SKIP' and field not in {'constraints','support_needed'}:
                            option.action='UNKNOWN';option.value=None;option.label='아직 모르겠어요'
                        if field=='weekly_available_minutes' and option.action=='VALUE':
                            label_minutes=normalize_personal_value(field,option.label)
                            if type(label_minutes) is int and label_minutes!=option.value:raise AIError('PLAN_INVALID','선택지 표시 시간과 분 단위 값이 다릅니다.')
                        if option.action == "VALUE" and not check_value(field, option.value): raise AIError("PLAN_INVALID", "선택지 값을 필드에 맞게 정규화하세요.")
                        if option.action == "SKIP" and field not in {"constraints", "support_needed"}: raise AIError("PLAN_INVALID", "건너뛰기는 개인 제약에서만 사용합니다.")
                if any(word in output.reply for word in ("고민 금지", "이렇게 확정", "완벽하게")): raise AIError("PLAN_INVALID", "압박·임의 확정 표현을 제거하세요.")

            def recover(output,reason):
                contract_issues.append(reason)
                fields={}
                for key,field in output.extracted_fields.items():
                    try:
                        probe=state.model_copy(deep=True);self._merge_personal({key:field},request,probe)
                        if key in probe.fields:fields[key]=probe.fields[key]
                    except AIError:continue
                return MemberCoachModel(reply='확인 가능한 개인 조건은 보존했습니다. 선택지 일부를 검증하지 못했어요. 아래 조건 요약을 확인하거나 자유 입력으로 이어갈 수 있습니다.',kind='SUMMARY',question=None,extracted_fields=fields,diagnostic_dimensions=state.diagnostic_dimensions)

            output = self._checked_json("member-goal-coach", payload, MemberCoachModel,
                "diagnostic_mode=ADAPTIVE이면 공동 목표·개인 역할에 맞는 진단 항목 4~6개를 diagnostic_dimensions(field_id,label,reason)로 선정하세요. current_level/weekly_available_minutes 외에 현재 진척·범위·도구·시간대·도움 중 영향이 큰 항목을 고릅니다. 2회 질문 후에는 요약을 제시하고 사용자가 계속 구체화를 선택하면 나머지 진단을 이어갑니다. 추가 진단 전체를 필수 조건으로 강제하지 마세요. "
                "공동 계획 안에서 본인의 기여 목표를 만드는 비공개 코치입니다. 알려진 본인 역할을 활용해 먼저 담당 결과물 초안을 제안하세요. 역할이 없으면 후보를 제안하되 배정을 확정하지 마세요. "
                "reply는 보통 3~6문장으로 짧게 쓰고 다음 행동 1~2개만 제시하세요. 상세한 전체 일정은 실행안 정리 단계에서 작성합니다. "
                "개인 수준·가능 시간은 추측하지 말고 사용자 발언에서만 EXTRACTED로 추출하세요. 추정 후보는 질문의 선택지로 제시하고 사용자 선택 전에는 사실로 저장하지 마세요. DraftField에는 value, origin, evidence_refs(실제 사용자 message_id), reason을 넣습니다. "
                "추출 필드는 current_level/current_progress/scope/contribution_candidate(문자열), weekly_available_minutes(양의 정수 분), preferred_times/constraints/tools/support_needed(문자열 배열)만 허용합니다. "
                "부족한 current_level/weekly_available_minutes가 있으면 question은 그중 하나를 먼저 묻습니다. 기여 후보 선택·선호·제약 질문보다 이 두 필수 조건을 우선합니다. 질문은 reply에 반복하지 마세요. allow_question=false이면 question=null로 두고 기여 초안·요약만 작성하세요. "
                "options는 label,value,action(VALUE/UNKNOWN/SKIP)를 갖습니다. 시간은 분으로 정규화하고 모름은 UNKNOWN/null입니다. 제약은 건너뛰기(SKIP/null), 없음(VALUE/[])을 구별하세요. "
                "multiple은 시간대·제약에만 사용합니다. 직접 입력은 UI가 제공하므로 옵션으로 만들지 마세요. 이미 파악했거나 UNKNOWN/SKIPPED로 답한 정보는 다시 묻지 마세요. "
                "다른 구성원 조건이나 공동 목표 변경을 요청하지 마세요. reply는 자연스러운 한국어 실행안 우선이고, 계획 요청이면 질문 없이 초안을 제시합니다.", stats, check, fallback=recover)
            self._merge_personal(output.extracted_fields, request, state)
            if adaptive and output.diagnostic_dimensions:state.diagnostic_dimensions=output.diagnostic_dimensions
            state.current_question = output.question
            if output.question:
                output.question.question_id = str(uuid4())
                for option in output.question.options: option.option_id = str(uuid4())
                if not any(o.action == "UNKNOWN" for o in output.question.options) and output.question.target_field not in {"constraints", "support_needed"}:
                    from .contracts import AnswerOption
                    output.question.options = output.question.options[:5] + [AnswerOption(option_id=str(uuid4()), label="아직 모르겠어요", action="UNKNOWN")]
                if output.question.target_field in {"constraints","support_needed"}:
                    from .contracts import AnswerOption
                    values = [o for o in output.question.options if o.action == "VALUE" and o.value != []][:4]
                    output.question.options = values + [AnswerOption(option_id=str(uuid4()), label="별도 제약 없음" if output.question.target_field=="constraints" else "별도 도움 필요 없음", value=[]), AnswerOption(option_id=str(uuid4()), label="건너뛰기", action="SKIP")]
                state.consecutive_questions += 1
            else:
                state.consecutive_questions = 0
                if adaptive:state.awaiting_continue=any(d.field_id not in state.fields or state.fields[d.field_id].value is None for d in state.diagnostic_dimensions if state.answer_states.get(d.field_id) not in {"UNKNOWN","SKIPPED"})
            emit("personal.question.ready", {"question": state.current_question.model_dump(mode="json") if state.current_question else None, "missing_fields": missing_fields(state.fields)})
            return MemberCoachReply(reply=output.reply, kind=output.kind, state=state, missing_fields=missing_fields(state.fields),contract_issues=contract_issues)
        return self._run("member-goal-coach", request, operation)

    def draft_member_goal(self, request: MemberGoalRequest) -> Result[MemberGoalDraft]:
        def operation(stats):
            state, payload, duplicate = self._personal_input(request)
            if duplicate: raise AIError("STALE_QUESTION", "목표 정리에는 중복 답변을 다시 전달하지 마세요.")
            if request.previous_draft and request.previous_draft.group_plan_id == request.group_plan.plan_id:
                payload["previous_draft"] = request.previous_draft.model_dump(mode="json")
            def check(draft):
                self._merge_personal(draft.profile_fields, request, state.model_copy(deep=True))
                if not set(draft.contribution.evidence_refs) <= set(request.goal_spec.evidence_ids): raise AIError("PLAN_INVALID", "공개 기여 근거는 공동 명세의 evidence_ids만 사용하세요.")
                if not draft.contribution.title.strip() or not draft.contribution.deliverable.strip(): raise AIError("PLAN_INVALID", "기여 목표와 결과물이 필요합니다.")
                public = draft.contribution.model_dump_json()
                constraints = list(state.fields.get("constraints", DraftField()).value or [])
                constraints += draft.profile_fields.get("constraints", DraftField()).value or []
                if any(len(secret.strip()) >= 4 and secret in public for secret in constraints):
                    raise AIError("PLAN_INVALID", "비공개 제약을 공개 기여 목표에 포함하지 마세요.")
            draft = self._checked_json("member-goal-draft", payload, MemberGoalDraft,
                "공동 계획 안에서 선택한 한 사람의 기여 목표와 개인 조건 검토 초안을 작성하세요. contribution에는 title,deliverable,completion_criteria,public_rationale,evidence_refs를 씁니다. "
                "다른 사람의 역할을 변경하거나 독립적인 개인 목표를 만들지 마세요. 기여 목표는 AI 제안이며 승인으로 표현하지 않습니다. 공개 기여에는 개인 수준·시간·민감한 제약을 설명하지 마세요. "
                "profile_fields에는 current_level,weekly_available_minutes,preferred_times,constraints,current_progress,scope,tools,support_needed를 포함할 수 있습니다. 추가 정보가 없으면 assumptions에 한계를 남기고 필수 누락으로 만들지 마세요. value,origin,evidence_refs,reason을 쓰며 실제 개인 사용자 메시지로 확인되지 않은 값은 null로 남기세요. "
                "현재 state에서 UNKNOWN/SKIPPED인 값은 사용자가 새 정보로 바꾸기 전에는 채우지 마세요. 개인 시간은 분으로 정규화합니다. "
                "contribution.evidence_refs는 공동 명세 evidence_ids만, profile_fields의 근거는 본인 사용자 메시지 ID만 사용합니다. "
                "owner_member_id/group_plan_id는 입력과 일치해야 합니다. 이전 검토된 기여 목표가 현재 공동 계획 기준이면 그대로 보존하세요.", stats, check)
            self._merge_personal(draft.profile_fields, request, state)
            draft.profile_fields = {k: state.fields.get(k, DraftField(reason="아직 대화에서 확인하지 않았습니다.")) for k in PROFILE_FIELDS}
            draft.owner_member_id = draft.contribution.owner_member_id = request.owner_member_id
            draft.group_plan_id = draft.contribution.group_plan_id = request.group_plan.plan_id
            draft.draft_id = str(uuid4())
            draft.contribution.review_status = "PENDING"
            if request.previous_draft and request.previous_draft.group_plan_id == request.group_plan.plan_id and request.previous_draft.contribution.review_status in {"ACCEPTED", "EDITED"}:
                draft.contribution = request.previous_draft.contribution.model_copy(deep=True)
            draft.missing_fields = missing_fields(draft.profile_fields)
            for dimension in state.diagnostic_dimensions:
                if state.fields.get(dimension.field_id, DraftField()).value is None:
                    note=dimension.label+" 정보를 확인하지 않아 관련 세부 조건은 후속 확인이 필요합니다."
                    if note not in draft.assumptions:draft.assumptions.append(note)
            emit("personal.draft.review_required", draft.model_dump(mode="json"))
            return draft
        return self._run("member-goal-draft", request, operation)
