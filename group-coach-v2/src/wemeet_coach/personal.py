"""Adaptive private dialogue with typed buttons, never a two-question gate."""

from .shared.contracts import (
    Contract,
    PersonalRequest,
    PersonalReply,
    PersonalField,
    PersonalPlan,
    Question,
    Option,
    CoachingSummary,
)
from pydantic import Field, ValidationError
from .m0.runtime import Runtime, code_result, input_error
from .validation import validate_plan, assign_ids, confirmed_weekdays, time_allocation
from uuid import uuid4
import re
from .m0.tracing import emit

FIELDS = {
    "level",
    "weekly_minutes",
    "scope",
    "progress",
    "tools",
    "preferred_times",
    "available_weekdays",
    "constraints",
    "support",
}
REQUIRED = ("level", "weekly_minutes", "scope")


class PersonalTalkReply(PersonalReply):
    """Dialogue never needs the large plan schema; plan generation is explicit."""

    plan: None = None
    time_allocation: None = None


class PersonalPlanOutput(Contract):
    """AI writes only a plan. Confirmed conditions and their provenance are code-owned."""

    reply: str
    condition_updates: dict[str, PersonalField] = Field(
        default_factory=dict,
        description="새 본인 발언으로 변경·추가된 조건만. 기존 조건 전체를 재출력하지 않습니다.",
    )
    plan: PersonalPlan | None = None
    missing: list[str] = Field(default_factory=list)
    summary: CoachingSummary | None = None
    details: str = ""
    review_required: list[str] = Field(
        default_factory=list,
        description="실제로 확인된 조건끼리 충돌해 계획을 작성할 수 없는 경우만. 일반 협업 의존성·선택 정보 부족·실행 전 확인 사항은 여기에 넣지 말고 계획의 조정 규칙에 적습니다.",
    )


def opening_question(role):
    """Immediate onboarding, labelled as a form question, not a model response."""
    return Question(
        question_id=str(uuid4()),
        field="level",
        text=f"맡으신 「{role}」 작업은 어느 정도 해보셨나요?",
        reason="경험에 맞춰 작업 난이도와 필요한 도움을 조절해요.",
        options=[
            Option(option_id="new", label="처음이에요", value="처음 해봄"),
            Option(option_id="some", label="조금 해봤어요", value="일부 경험"),
            Option(
                option_id="independent",
                label="혼자 할 수 있어요",
                value="독립 수행 가능",
            ),
            Option(option_id="unknown", label="아직 모르겠어요", action="UNKNOWN"),
        ],
    )


def normalize(field, value):
    if field == "available_weekdays":
        if not isinstance(value, list) or any(
            type(v) != int or v not in range(7) for v in value
        ):
            raise ValueError("가능 요일은 월0~일6의 정수 목록입니다.")
        value = sorted(set(value))
    elif field == "weekly_minutes":
        if isinstance(value, dict) and set(value) == {"minutes"}:
            value = value["minutes"]
        if isinstance(value, str):
            m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(시간|분)?\s*", value)
            if m:
                value = float(m[1]) * (60 if m[2] == "시간" else 1)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if type(value) != int or not 1 <= value <= 10080:
            raise ValueError("주간 가능 시간은 분 단위 양의 정수입니다.")
    elif field in {"level", "scope", "progress"}:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("문자열 답변이 필요합니다.")
    else:
        # A single textual tool/constraint is one list item, never a fabricated fact.
        if isinstance(value, str) and value.strip():
            value = [value.strip()]
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError("문자열 목록 답변이 필요합니다: " + field)
    return value


def apply_answer(request):
    state = request.state.model_copy(deep=True)
    if request.answer:
        q = state.question
        a = request.answer
        if (
            not q
            or a.get("question_id") != q.question_id
            or q.question_id in state.answered_questions
        ):
            raise ValueError("이미 답했거나 현재 질문이 아닙니다.")
        option = next((o for o in q.options if o.option_id == a.get("option_id")), None)
        if not option:
            raise ValueError("현재 질문의 선택지가 아닙니다.")
        ref = a.get("message_id")
        if not any(
            m.message_id == ref
            and m.role == "user"
            and m.speaker_id == request.owner_member_id
            for m in request.conversation
        ):
            raise ValueError("답변 메시지가 없습니다.")
        status = {"VALUE": "KNOWN", "UNKNOWN": "UNKNOWN", "SKIP": "SKIPPED"}[
            option.action
        ]
        state.fields[q.field] = PersonalField(
            value=normalize(q.field, option.value) if status == "KNOWN" else None,
            status=status,
            evidence_refs=[ref],
        )
        state.answered_questions.append(q.question_id)
        state.answers_since_summary += 1
        state.question = None
    new = [
        m.message_id
        for m in request.conversation
        if m.role == "user" and m.message_id not in state.processed_message_ids
    ]
    if not request.answer:
        state.answers_since_summary += len(new)
    state.processed_message_ids = list(
        dict.fromkeys([*state.processed_message_ids, *new])
    )
    return state


def personal_coach(request: PersonalRequest, *, runtime=None):
    try:
        request = PersonalRequest.model_validate(request)
    except ValidationError as exc:
        return input_error("personal", exc)
    fresh_refs = {m.message_id for m in request.conversation if m.role == "user"} - set(
        request.state.processed_message_ids
    )
    selected_field = (
        request.state.question.field
        if request.answer and request.state.question
        else None
    )
    try:
        state = (
            apply_answer(request)
            if request.action == "TALK" or request.answer
            else request.state.model_copy(deep=True)
        )
    except ValueError as e:
        return code_result("personal", needed=[str(e)])
    request = request.model_copy(update={"state": state, "answer": None})
    if request.action == "PLAN":
        missing = [
            k
            for k in REQUIRED
            if k not in state.fields or state.fields[k].status != "KNOWN"
        ]
        if missing:
            return code_result(
                "personal.plan",
                data=PersonalReply(
                    reply="확인한 답변은 유지됩니다. 필요한 조건만 같은 상담에서 알려주세요.",
                    state=state,
                    missing=missing,
                ),
                needed=missing,
            )
        try:
            for key in REQUIRED:
                state.fields[key].value = normalize(key, state.fields[key].value)
        except ValueError as e:
            return code_result(
                "personal.plan",
                data=PersonalReply(reply="개인 조건의 값을 확인해주세요.", state=state),
                needed=[str(e)],
            )
        # Completed Q/A and old AI replies are not needed to rewrite a plan.
        evidence = {ref for f in state.fields.values() for ref in f.evidence_refs}
        request = request.model_copy(
            update={
                "conversation": [
                    m
                    for m in request.conversation
                    if m.role == "user" and m.message_id in evidence | fresh_refs
                ]
            }
        )
    refs = {m.message_id for m in request.conversation if m.role == "user"}
    availability = state.fields.get("available_weekdays")
    days = (
        confirmed_weekdays(availability.value)
        if availability and availability.status == "KNOWN"
        else None
    )
    schedule_hint = ""
    if request.action == "PLAN" and days is not None:
        from datetime import timedelta

        dates = [
            request.goal.start_date + timedelta(days=i)
            for i in range((request.goal.end_date - request.goal.start_date).days + 1)
            if (request.goal.start_date + timedelta(days=i)).weekday() in days
        ]
        if not dates:
            return code_result(
                "personal.plan",
                data=PersonalReply(
                    reply="정한 기간 안에 선택한 작업 요일이 없습니다. 개인 시간이나 공동 기간을 수정해주세요.",
                    state=state,
                ),
                needed=["기간 안에 가능한 작업일"],
            )
        weeks = {((day - request.goal.start_date).days // 7) for day in dates}
        schedule_hint = (
            "\n본인이 명시적으로 확인한 가능 요일의 작업일: "
            + ", ".join(d.isoformat() for d in dates)
            + f'. 실제 수행일 scheduled_date는 이 날짜 중에서 선택하세요. due_date는 공동 기한이며 수행일과 구분합니다. 실제 작업 가능한 주차 {len(weeks)}개, 총 시간 상한 {len(weeks)*state.fields["weekly_minutes"].value}분입니다.\n'
        )

    warnings = []

    def check(out):
        if isinstance(out, PersonalPlanOutput):
            if set(out.condition_updates) - FIELDS:
                raise ValueError("지원하지 않는 개인 항목입니다.")
            # A PLAN may include newly typed information. Persist it with its real
            # source instead of using it only in model prose and losing it later.
            for key, field in out.condition_updates.items():
                if (
                    not field.evidence_refs
                    or not set(field.evidence_refs) <= fresh_refs
                ):
                    raise ValueError(
                        "새 조건에는 이번에 추가된 실제 본인 발언 근거만 사용하세요."
                    )
                if field.status == "KNOWN":
                    field.value = normalize(key, field.value)
                elif field.status in {"UNKNOWN", "SKIPPED"}:
                    field.value = None
                if key == "available_weekdays" and field.status == "KNOWN":
                    field.status = "SUGGESTED"
                previous = state.fields.get(key)
                if (
                    previous
                    and previous.status == "KNOWN"
                    and (field.status != "KNOWN" or previous.value != field.value)
                ):
                    out.review_required.append(
                        "새 답변으로 바뀐 개인 조건 확인: " + key
                    )
            if out.review_required:
                out.plan = None
                return
            if not out.plan:
                raise ValueError(
                    "확인된 필수 조건으로 개인 plan을 작성하세요. 선택 정보는 추가 관문이 아닙니다."
                )
            effective = {**state.fields, **out.condition_updates}
            availability = effective.get("available_weekdays")
            effective_days = (
                confirmed_weekdays(availability.value)
                if availability and availability.status == "KNOWN"
                else None
            )
            issues = validate_plan(
                out.plan,
                request.goal,
                owner=request.owner_member_id,
                weekly_minutes=effective["weekly_minutes"].value,
                allowed_weekdays=effective_days,
            )
            if not issues["valid"]:
                raise ValueError("; ".join(issues["issues"]))
            assign_ids(out.plan)
            return
        unsupported = set(out.state.fields) - FIELDS
        if unsupported:
            emit("personal.fields.omitted", {"unsupported_fields": sorted(unsupported)})
            warnings.append(
                "지원하지 않는 개인 항목은 확정 조건에 저장하지 않았습니다: "
                + ", ".join(sorted(unsupported))
            )
            out.state.fields = {
                k: v for k, v in out.state.fields.items() if k in FIELDS
            }
        # Restore trusted unchanged values BEFORE checking model-generated references.
        # A hallucinated reference cannot destroy an unchanged, already-confirmed fact.
        for key, field in state.fields.items():
            incoming = out.state.fields.get(key)
            new_refs = set(incoming.evidence_refs if incoming else []) - set(
                field.evidence_refs
            )
            if key == selected_field or not new_refs.intersection(fresh_refs):
                out.state.fields[key] = field
        for key, field in out.state.fields.items():
            if not set(field.evidence_refs) <= refs:
                raise ValueError("실제 본인 발언만 근거로 사용하세요.")
            if (
                key == "available_weekdays"
                and field.status == "KNOWN"
                and selected_field != key
                and not (key in state.fields and state.fields[key].status == "KNOWN")
            ):
                # Model interpretation alone is not confirmation of a prohibition.
                field.status = "SUGGESTED"
            if field.status == "KNOWN":
                if not field.evidence_refs:
                    raise ValueError(
                        "확인된 개인 조건에는 실제 본인 발언 근거가 필요합니다: " + key
                    )
                field.value = normalize(key, field.value)
        out.state.answered_questions = list(state.answered_questions)
        out.state.processed_message_ids = list(state.processed_message_ids)
        question = out.state.question
        if question:
            if question.field not in FIELDS:
                raise ValueError("지원하지 않는 질문입니다.")
            field = out.state.fields.get(question.field)
            if (
                field
                and field.status in {"KNOWN", "SKIPPED", "UNKNOWN"}
                and field.evidence_refs
            ):
                emit("question.repeat.omitted", {"field": question.field})
                warnings.append(
                    "이미 답한 항목의 반복 질문을 제외했습니다: " + question.field
                )
                out.state.question = None
                question = None
            if question is not None:
                question.question_id = str(uuid4())
                ids = [o.option_id for o in question.options]
                if len(ids) != len(set(ids)):
                    raise ValueError("선택지 ID 중복")
                # A model sometimes expresses the always-available free text input as
                # a VALUE button with no value. It is not a confirmed answer and must
                # not discard the valid conversation or become an UNKNOWN selection.
                empty = [
                    o.option_id
                    for o in question.options
                    if o.action == "VALUE" and o.value is None
                ]
                if empty:
                    emit(
                        "question.options.normalized",
                        {
                            "removed_empty_value_options": empty,
                            "free_text_available": True,
                        },
                    )
                    question.options = [
                        o for o in question.options if o.option_id not in empty
                    ]
                for o in question.options:
                    if o.action == "VALUE":
                        o.value = normalize(question.field, o.value)
                    elif o.value is not None:
                        raise ValueError("모름·건너뛰기는 null 값입니다.")
        out.state.answers_since_summary = (
            0 if state.answers_since_summary >= 2 else state.answers_since_summary
        )

    result = (runtime if runtime is not None else Runtime()).run(
        "personal." + request.action.lower(),
        request,
        PersonalTalkReply if request.action == "TALK" else PersonalPlanOutput,
        """당신은 모임 목표 안에서 본인의 기여 계획을 만드는 비공개 코치입니다.
reply와 summary는 현재 진행을 쉽게 파악하도록 작성하고, 필요한 판단 근거·대안·작업 분석은 details에 충분히 설명하세요. 글자 수나 문장 수 때문에 중요한 정보를 생략하지 마세요. 다음 답변 버튼은 question에 제공하고, 불필요한 기존 설명 반복은 피하세요.
입력에는 공개 공동 조건과 이 사람의 자료만 있습니다. 다른 구성원의 개인 조건을 추정하거나 요청하지 마세요.
담당 역할의 결과물 범위·경험·가능 시간·진척·도구·제약·도움 중 실제 계획에 영향을 주는 내용만 알아내세요.
질문 하나와 이유를 제시하고 활동에 맞는 답변 버튼을 제공하세요. 수준·시간 두 질문 뒤에도 진단을 계속할 수 있습니다.
이미 답한 질문을 반복하지 마세요. answers_since_summary>=2이면 중간 정리와 실행 초안을 먼저 보여주고 진단을 계속하세요.
매번 '마지막 하나'라고 하지 마세요. 단순 표현 차이는 추천 문장으로 처리하고 필수 관문을 늘리지 마세요.
모르는 값은 UNKNOWN, 제약 건너뛰기는 SKIPPED, 제약 없음은 KNOWN의 빈 목록입니다. 값을 임의 확정하지 마세요.
질문 field는 허용 항목만, weekly_minutes는 분 단위 정수, level/scope/progress는 문자열, available_weekdays는 월0~일6 정수 목록, 나머지는 문자열 목록입니다.
preferred_times는 선호이며 금지 조건이 아닙니다. 우선 맞추되 어려우면 대안과 이유를 설명하세요. available_weekdays만 본인이 명시적으로 확인한 엄격한 가능 요일입니다. 단순히 '주말 선호'라고 말한 것을 '평일 불가'로 바꾸지 마세요. 불가 여부가 계획을 바꾸는 경우에만 확인 질문으로 구별하세요.
SKIP과 UNKNOWN 옵션 value는 null입니다. KNOWN의 evidence_refs에는 본인 메시지 ID만 사용하세요.
직접 입력창은 화면에서 항상 제공됩니다. 직접 입력을 의미하는 VALUE/null 버튼은 만들지 말고 실제 선택 가능한 값만 버튼으로 제공하세요.
PLAN 요청에는 담당 결과물·완료 기준·날짜별 작업·시간 예산·조정 규칙을 구체적으로 작성하세요.
초보자에게는 필요한 도구 익히기·작은 시도·검토 시간을 작업에 포함하고, 숙련자에게는 실제 진척에 맞는 범위를 제안하세요. 숫자만 줄여 불가능한 작업을 가능하다고 포장하지 마세요. 공동 완료 기준(예: 다른 사람의 실제 실행 검증)을 혼자 한 가상 점검으로 대체하면 안 됩니다. 협업이 필요하면 의존 작업과 요청할 도움을 설명하세요.
Task의 due_date는 완료 기한, scheduled_date는 실제 수행일입니다. 둘을 구별하고 가능 요일·주간 예산은 수행일 기준으로 배치하세요. 선행 작업 수행일보다 후속 수행일을 앞서게 하지 마세요.
시간 합계는 코드가 계산합니다. reply/details/weekly_outcomes에 총 분량을 쓸 경우 반드시 실제 tasks의 분 합계와 일치시켜야 합니다. 사용 가능한 상한을 실제 배분한 시간이라고 설명하지 마세요.
PLAN의 state는 이미 확인된 입력입니다. 기존 state 전체·기존 evidence_refs·질문 이력을 다시 출력하거나 바꾸지 마세요. 새 본인 발언이 있으면 새로 추가·변경된 항목만 condition_updates에 실제 새 발언 ID와 함께 적으세요. 기존 KNOWN 값과 충돌하면 plan=null과 review_required로 확인을 요청하세요. 추가 선택 조건도 계획에 적용했으면 condition_updates에 남겨 이후 모듈에서 유실되지 않게 하세요. reply·요약·상세 설명·plan을 작성합니다. 입력에서 확인되지 않은 작업 시각·방법은 제안임을 표시하세요.
실제로 가능 요일이 확인돼 한정된 경우에는 그 안에 배치하세요. 선호만 있는 경우에는 그 선호를 우선 반영하고 벗어나는 작업은 이유와 대안을 표시하세요.
weekly_outcomes에 시작일부터 7일씩 나눈 주차별 성과를 쓰고, 첫7일에는 바로 실행할 수 있는 작은 행동으로 날짜·분량·완료 기준을 구체화하세요. 단순히 '준비하기'를 반복하지 마세요.
level/weekly_minutes/scope가 확인되지 않으면 plan=null로 보류하고 같은 창에서 질문과 선택지로 보완하세요.
위 세 필수 조건이 확인됐으면 PLAN을 작성하세요. preferred_times/tools/constraints/support/progress는 추가 필수 관문이 아닙니다. 없는 내용은 개인 계획의 한계나 선택적 제안으로 표시하세요.
assigned_tasks가 있으면 승인된 본인 담당 작업입니다. 공동 목표 전체나 다른 사람의 분량을 개인 과제로 가져오지 말고 이 범위 안에서 세분화하세요. 개인 scope가 승인 배정과 실제로 충돌해 조정이 필요하면 plan=null, review_required에 구체적인 공동 변경/개인 범위 확인 항목을 적으세요. AI가 역할을 조용히 바꾸면 안 됩니다.
공동 작업의 estimated_minutes는 공동 초안 단계의 예상치이며 본인이 약속한 고정 소요 시간이 아닙니다. 본인의 경험·진척·주간 예산으로 세부 작업 시간을 다시 산정하세요. 공동 예상치가 개인 예산보다 크다는 이유만으로 시간 증가나 마감 변경을 강요하지 마세요. 범위·완료 기준을 유지하면서 효율적인 작업 방법과 시간 배분을 먼저 검토하고, 실제로 불가능할 때만 근거와 조정 선택지를 제시하세요.
기존 state를 다시 추출하거나 덮어쓰지 말되, 새 본인 발언의 비용·도구 등 조건은 계획에 반영하세요. 새 발언이 확인된 시간 예산·범위와 충돌하면 plan=null과 review_required로 재확인을 요청하세요.
공동 역할·기간·조건을 바꾸지 마세요. 불가능하면 필요한 공동 변경을 제안하되 임의로 확정하지 마세요.
review_required는 실제 담당 범위·확인된 시간 예산·기한·불가 조건끼리 충돌해 계획을 만들 수 없는 경우만 사용하세요. 다른 담당자의 자료 수신·검토 참여·작업 전에 알아낼 자료 범위는 정상적인 협업 의존성입니다. 이런 사항은 plan의 작업·완료 기준·adaptation_rules와 details에 조건부 실행/지연 대응으로 적고 계획을 제공하세요. 실행 전 확인할 것이 남았다는 이유로 plan=null로 만들면 안 됩니다.
public_dependencies는 공개 공동 작업의 필요한 선행 작업입니다. 다른 사람의 개인 조건을 추정하지 말고, 본인이 요청·수신·확인할 결과와 지연 시 조정 규칙을 계획하세요. 외부 선행 작업 ID를 개인 tasks의 dependencies에 섞지 마세요. 개인 내부 의존성과 공개 협업 의존성을 구분하세요.
confirmed goal의 마일스톤·반복 횟수·부분 주차 조건을 지키세요. 마지막 짧은 주를 무조건 새 전체 주로 간주하지 마세요.
Task는 실제 행동·결과물·완료 기준을 명시하고 owner_member_id는 본인만 씁니다. 개인 사유는 private_notes에만 둡니다.
개인 조건·민감 사유를 공개한다는 표현을 쓰지 마세요. 그룹 공개는 확인한 기여 목표·할 일·기한만입니다."""
        + schedule_hint,
        check=check,
        owner=request.owner_member_id,
    )
    result.warnings.extend(dict.fromkeys(warnings))
    if result.status == "OK" and request.action == "PLAN":
        data = result.data
        for key, field in data.condition_updates.items():
            state.fields[key] = field
        state.processed_message_ids = list(
            dict.fromkeys([*state.processed_message_ids, *fresh_refs])
        )
        result.data = PersonalReply(
            reply=data.reply,
            state=state,
            plan=data.plan,
            missing=data.review_required or data.missing,
            summary=data.summary,
            details=data.details,
            time_allocation=(
                time_allocation(
                    data.plan, request.goal, state.fields["weekly_minutes"].value
                )
                if data.plan
                else None
            ),
        )
        if data.review_required:
            result.status = "NEEDS_INPUT"
            result.needed_inputs = data.review_required
    return result
