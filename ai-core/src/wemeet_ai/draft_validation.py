"""Canonical public draft values shared by AI output, human review and preparation."""
from copy import deepcopy
from datetime import date

from pydantic import ValidationError

from .contracts import ConfirmedDecisions
from .tracing import emit


def normalize_checkins(value):
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("확인할 일정은 항목 목록이어야 합니다.")
    result = []
    for item in value:
        if isinstance(item, str):
            if not item.strip(): raise ValueError("빈 일정은 삭제하거나 내용을 입력해주세요.")
            result.append(item.strip())
        elif isinstance(item, dict):
            allowed = {"day_of_week", "weekday", "day", "time", "method", "frequency_per_week"}
            if not item or set(item) - allowed:
                raise ValueError("일정에 해석할 수 없는 항목이 있습니다. 요일·시간·인증 방식을 직접 확인해주세요.")
            parts = []
            if item.get("frequency_per_week") is not None:
                frequency = item["frequency_per_week"]
                if type(frequency) is not int or not 1 <= frequency <= 7:
                    raise ValueError("체크인 횟수는 주 1~7회의 정수여야 합니다.")
                parts.append(f"주 {frequency}회")
            days = [item[k] for k in ("day_of_week", "weekday", "day") if item.get(k) is not None]
            if len(set(str(v) for v in days)) > 1:
                raise ValueError("일정의 요일 값이 서로 다릅니다.")
            for part in ([days[0]] if days else []) + [item[k] for k in ("time", "method") if item.get(k) is not None]:
                if not isinstance(part, str) or not part.strip():
                    raise ValueError("요일·시간·인증 방식은 비어 있지 않은 문구여야 합니다.")
                parts.append(part.strip())
            if not parts: raise ValueError("확인할 일정에 내용이 없습니다.")
            result.append(" · ".join(parts))
        else:
            raise ValueError("일정 항목은 문구 또는 알려진 요일·시간·방식이어야 합니다.")
    return result


def normalize_decision_field(name, value, member_ids=None):
    if name not in ConfirmedDecisions.model_fields or name == "confirmed":
        raise ValueError("지원하지 않는 목표 명세 항목입니다.")
    if value is None: return None
    if name == "checkins": value = normalize_checkins(value)
    if name == 'roles' and isinstance(value,list):
        mapped={}
        for item in value:
            if not isinstance(item,dict) or set(item)-{'member_id','role','responsibility'} or not item.get('member_id'):
                raise ValueError('역할에는 구성원 ID와 담당 문구가 필요합니다.')
            text=item.get('responsibility') or item.get('role')
            if not isinstance(text,str) or not text.strip() or item['member_id'] in mapped:
                raise ValueError('역할 담당이 비어 있거나 같은 구성원이 중복되었습니다.')
            mapped[item['member_id']]=text.strip()
        value=mapped
    try:
        canonical = ConfirmedDecisions.model_validate({name: value}, strict=True).model_dump(mode="json")[name]
    except ValidationError as exc:
        raise ValueError("항목의 형식이 올바르지 않습니다: " + "; ".join(".".join(map(str, e["loc"])) + " · " + e["msg"] for e in exc.errors())) from exc
    if isinstance(canonical, str):
        canonical = canonical.strip()
        if not canonical: raise ValueError("내용을 입력하거나 미정으로 남겨주세요.")
    if name in {"start_date", "end_date"}:
        try: canonical = date.fromisoformat(canonical).isoformat()
        except (ValueError, TypeError) as exc: raise ValueError("올바른 날짜를 선택해주세요.") from exc
    if name in {"completion_criteria", "custom_privacy_fields"}:
        if any(not s.strip() for s in canonical): raise ValueError("빈 항목은 삭제하거나 내용을 입력해주세요.")
        canonical = [s.strip() for s in canonical]
    if name == "roles" and member_ids is not None and set(canonical) - set(member_ids):
        raise ValueError("역할에 현재 구성원이 아닌 사람이 있습니다.")
    if name == "milestones":
        for i, milestone in enumerate(canonical):
            if set(milestone) - {"title", "due_date", "owner_member_id"}:
                raise ValueError(f"{i + 1}번째 마일스톤에 지원하지 않는 항목이 있습니다.")
            if not isinstance(milestone.get("title"), str) or not milestone["title"].strip():
                raise ValueError(f"{i + 1}번째 마일스톤 제목을 입력해주세요.")
            try: milestone["due_date"] = date.fromisoformat(milestone.get("due_date", "")).isoformat()
            except (ValueError, TypeError): raise ValueError(f"{i + 1}번째 마일스톤 날짜를 선택해주세요.")
            if member_ids is not None and milestone.get("owner_member_id") is not None and milestone["owner_member_id"] not in member_ids:
                raise ValueError(f"{i + 1}번째 마일스톤의 담당자를 확인해주세요.")
    return canonical


def normalize_goal_draft(draft, members=None):
    normalized = draft.model_copy(deep=True)
    member_ids = [m.member_id for m in members] if members is not None else None
    for name, field in normalized.fields.items():
        original = deepcopy(field.value)
        field.validation_errors = []
        try:
            field.value = normalize_decision_field(name, field.value, member_ids)
            if field.value != original:
                emit("draft.field.normalized", {"field": name, "before": original, "after": field.value, "review_status": field.review_status})
        except ValueError as exc:
            field.validation_errors = [str(exc)]
            emit("draft.field.invalid", {"field": name, "value": original, "errors": field.validation_errors})
    start, end = normalized.fields.get("start_date"), normalized.fields.get("end_date")
    if start and end and not start.validation_errors and not end.validation_errors and start.value and end.value:
        if start.value >= end.value:
            end.validation_errors.append("마감일은 시작일 이후여야 합니다.")
        milestones = normalized.fields.get("milestones")
        for milestone in (milestones.value or []) if milestones and not milestones.validation_errors else []:
            if isinstance(milestone, dict) and milestone.get("due_date") and not start.value <= milestone["due_date"] <= end.value:
                normalized.fields["milestones"].validation_errors.append("마일스톤 날짜는 공동 목표 기간 안이어야 합니다.")
    return normalized
