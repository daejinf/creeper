"""M1-C accepts external facts. Review/confirmation are deterministic functions."""
from datetime import date
from typing import Any, Literal
from pydantic import Field, ValidationError
from ...shared.contracts import Contract, ReviewField, Member, ConfirmedGoal, Evidence, Error
from ...m0 import Runtime, AIError
from ...m0.runtime import code_result
from ...shared.normalization import normalize_fact

FIELDS = {"goal", "start_date", "end_date", "completion_criteria", "execution_method", "verification_method", "privacy_scope", "milestones", "roles", "frequency_per_week", "checkins"}
REQUIRED = ("goal", "start_date", "end_date", "completion_criteria", "execution_method", "verification_method", "privacy_scope")

class GoalDraft(Contract):
    fields: dict[str, ReviewField] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)

class DraftRequest(Contract):
    facts: dict[str, ReviewField] = Field(default_factory=dict)
    previous: GoalDraft | None = None
    members: list[Member] = Field(default_factory=list)
    reference_date: date = Field(default_factory=date.today)
    mode: Literal["ASSISTED", "GROUNDED", "SUGGEST"] = "ASSISTED"
    target_fields: list[str] = Field(default_factory=list)
    conflicted_fields: list[str] = Field(default_factory=list)

def field_error(key, value):
    if value is None: return "값이 필요합니다."
    if key in {"start_date", "end_date"}:
        try: date.fromisoformat(str(value))
        except ValueError: return "YYYY-MM-DD 날짜를 입력해주세요."
    elif key == "privacy_scope" and (not isinstance(value, str) or value not in {"OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"}): return "공개 범위를 선택해주세요."
    elif key == "completion_criteria" and (not isinstance(value, list) or not value or not all(isinstance(x, str) and x.strip() for x in value)): return "완료 기준은 빈 항목 없는 문자열 목록이어야 합니다."
    elif key in {"goal", "execution_method", "verification_method"} and (not isinstance(value, str) or not value.strip()): return "내용을 입력해주세요."
    elif key in {"checkins", "milestones"} and not isinstance(value, list): return "목록 형식이 필요합니다."
    elif key == "checkins" and not all(isinstance(x, str) for x in value): return "체크인은 문자열 목록입니다."
    elif key == "milestones" and not all(isinstance(x, dict) and isinstance(x.get("title"), str) for x in value): return "마일스톤에는 제목이 필요합니다."
    elif key == "roles" and (not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values())): return "담당은 구성원 ID와 역할 문자열의 대응입니다."
    elif key == "frequency_per_week" and (type(value) is not int or not 1 <= value <= 7): return "주간 횟수는 1~7 정수입니다."
    if key == "milestones":
        for row in value:
            if row.get("due_date"):
                try: date.fromisoformat(str(row["due_date"]))
                except ValueError: return "마일스톤 날짜는 YYYY-MM-DD 형식이어야 합니다."
    return None

def draft_goal_spec(request: DraftRequest, *, runtime: Runtime | None = None, fresh=False):
    request = request.model_copy(deep=True)
    for key, f in request.facts.items():
        f.value=normalize_fact(key,f.value,request.members)
        if f.origin=='UNRESOLVED' and f.value is not None:
            f.previous_value=f.value;f.value=None
    if set(request.facts) - FIELDS or set(request.target_fields) - FIELDS:
        return code_result("C", error=Error(code="INVALID_FIELD", message="지원하지 않는 목표 항목입니다."))
    if request.mode == "SUGGEST" and not request.target_fields:
        return code_result("C", needed=["target_fields: 제안할 항목을 선택해주세요."])
    if not any(f.value for f in request.facts.values()) and not request.previous:
        return code_result("C", GoalDraft(fields={"goal": ReviewField(reason="무엇을 함께 하려는지 알려주세요.")}), needed=["goal: 목표 설명"])
    def check(out):
        if set(out.fields) - FIELDS: raise AIError("INVALID_FIELD", "지원하지 않는 항목입니다.")
        # A model may return only the missing cards. Existing facts are data,
        # not optional model output, and must survive without another paid call.
        for key, original in request.facts.items():
            if original.value is not None and not (request.mode=='SUGGEST' and key in request.target_fields):
                out.fields[key]=original.model_copy(deep=True)
            elif key not in out.fields:
                out.fields[key]=original.model_copy(deep=True)
        for key in REQUIRED:
            out.fields.setdefault(key,ReviewField(reason='아직 정해지지 않은 내용입니다. 같은 카드에서 수정하거나 제안을 요청하세요.'))

        for key, f in out.fields.items():
            f.value = normalize_fact(key, f.value, request.members)
            f.review_status = "PENDING"
            issue=field_error(key,f.value) if f.value is not None else None
            if issue:
                f.previous_value=f.value
                f.value=None;f.origin='UNRESOLVED';f.conflicted=True
                f.reason=issue+' 원래 값은 previous_value에 보존했습니다. 이 카드만 수정해주세요.'
                continue
            original = request.facts.get(key)
            if original and original.value == f.value and original.evidence:
                f.origin, f.evidence = original.origin, original.evidence
            elif f.value is not None:
                f.origin, f.evidence = "AI_SUGGESTED", []
                if request.mode == "GROUNDED": f.value, f.origin = None, "UNRESOLVED"
            if request.mode == "SUGGEST" and key not in request.target_fields:
                continue
    result = (runtime or Runtime()).run("C", request, GoalDraft,
        """공동 목표 검토표를 작성하세요. facts는 SW 제공 자료이고 이전 단계 실행은 필요 없습니다.
ASSISTED: 있는 사실은 보존하고 없는 날짜/인증/공개 범위/완료 기준은 확인 전 AI 추천으로 제안합니다.
GROUNDED: 없는 값은 null. SUGGEST: target_fields만 보완하세요. 추천에는 가짜 evidence를 붙이지 마세요.
날짜는 ISO 문자열, checkins는 문자열 목록, privacy_scope는 OBJECTIVE_ONLY/PRIVATE/CUSTOM입니다.
공개 범위 미정이면 OBJECTIVE_ONLY를 추천합니다. 날짜 추천 이유는 reference_date와 작업량을 설명합니다.
실제 목표조차 없으면 임의 프로젝트를 만들지 마세요. 충돌한 후보는 conflicts에 남깁니다.""", check=check, fresh=fresh)
    if result.status != "OK": return result
    out = result.data
    if request.mode == "SUGGEST":
        out.fields = {**(request.previous.fields if request.previous else request.facts), **{k:v for k,v in out.fields.items() if k in request.target_fields}}
    if request.previous:
        for key, old in request.previous.fields.items():
            if old.review_status not in {"ACCEPTED", "EDITED", "REVIEW_REQUIRED"}: continue
            new_fact = request.facts.get(key)
            saved = old.model_copy(deep=True)
            if key in request.conflicted_fields or new_fact and (new_fact.conflicted or new_fact.value is not None and new_fact.value != old.value):
                saved.review_status = "REVIEW_REQUIRED"
                saved.conflicted = True
                saved.previous_value = old.value
                saved.reason = "새 정보와 이전 확인값이 다르거나 충돌합니다. 새 후보: " + str(new_fact.value if new_fact else None)
                out.conflicts.append(key + ": 새 정보를 검토해주세요.")
            if key not in request.target_fields: out.fields[key] = saved
    return result

def review_field(draft: GoalDraft, field: str, *, value: Any = None, edit=False, actor_id: str):
    """No model call. Returns a copy; invalid edits preserve the original draft."""
    out = draft.model_copy(deep=True)
    if field not in FIELDS: return code_result("C.review", draft, needed=[field + ": 지원하지 않는 항목"])
    old = out.fields.get(field, ReviewField())
    chosen = normalize_fact(field, value if edit else old.value)
    error = field_error(field, chosen)
    if error: return code_result("C.review", draft, needed=[field + ": " + error])
    old.previous_value, old.value = old.value, chosen
    old.review_status = "EDITED" if edit else "ACCEPTED"
    old.conflicted = False
    old.evidence = [*old.evidence, Evidence(source="SW_CONFIRMED", source_id=actor_id)]
    out.fields[field] = old
    return code_result("C.review", out)

def confirm_goal(draft: GoalDraft, *, members: list[Member]):
    missing = [k for k in REQUIRED if k not in draft.fields or draft.fields[k].review_status not in {"ACCEPTED", "EDITED"} or draft.fields[k].conflicted or field_error(k, draft.fields[k].value)]
    if not members: missing.append("members")
    if missing: return code_result("C.confirm", needed=missing)
    values = {k:draft.fields[k].value for k in REQUIRED}
    try:
        optional = {k: normalize_fact(k, f.value) for k, f in draft.fields.items() if k in {"milestones", "roles", "checkins", "frequency_per_week"} and f.review_status in {"ACCEPTED", "EDITED"} and f.value is not None}
        goal = ConfirmedGoal(**values, members=members, confirmed=True,
            evidence=[e for f in draft.fields.values() for e in f.evidence], **optional)
    except ValidationError as exc:
        return code_result("C.confirm", needed=[".".join(map(str,e["loc"])) + ": " + e["msg"] for e in exc.errors()])
    except ValueError: return code_result("C.confirm", needed=["공동 조건의 형식을 확인해주세요."])
    return code_result("C.confirm", goal)

__all__ = ["DraftRequest", "GoalDraft", "draft_goal_spec", "review_field", "confirm_goal"]
