"""AI-led interview around the E.coach module (D-B2 follow-up).

The AI leads: each turn it reads the member's answer, writes the NEXT question for this project and this person, and
offers answer buttons. The server never trusts that output: every part is validated here before it is stored or shown.
  * an answer is stored only if its `quote` really occurs in the member's own message, and the value must be built from
    the member's words (so a project goal can never become a personal answer);
  * a next question needs a valid unanswered field and 2-4 usable buttons, else the server's own generic question for the
    first missing item is shown instead (logged), so the member always has a question and buttons while items are missing;
  * meaningless input ("ㅇㅇㅇ", punctuation, one character) never reaches the model;
  * the server decides completion: a required item counts as answered when it is known, or when the member said "모름"
    twice (the AI re-asks once), and at the turn limit the member may always close the interview.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

REQUIRED = ("current_level", "weekly_available_minutes", "scope", "constraints")
OPTIONAL = ("tools", "current_progress", "preferred_times", "support_needed")
ORDER = REQUIRED + OPTIONAL
MAX_TURNS = 10

LABELS = {
    "weekly_available_minutes": "주간 가능 시간",
    "current_level": "현재 수준",
    "scope": "맡을 수 있는 범위",
    "constraints": "제약 조건",
    "current_progress": "진행 상황",
    "tools": "쓸 수 있는 도구",
    "preferred_times": "선호 시간대",
    "support_needed": "필요한 도움",
}
UNKNOWN_ID = "unknown"
UNKNOWN_LABEL = "잘 모르겠어요"
MSG_UNCLEAR = "답을 이해하지 못했어요. 아래에서 고르거나 직접 적어 주세요."
_WORD = re.compile(r"[0-9A-Za-z가-힣]")
_INTENT = re.compile(r"맡고\s*싶|맡을|맡겠|할\s*수\s*있|하고\s*싶|담당|하겠")
_CORRECT = re.compile(r"아니|바꿔|바꿀|수정|정정|말고|아닌|틀렸")
_BANNED = ("확인되", "진단", "필수", "항목", "상태", "입력되", "current_", "weekly_", "constraints", "KNOWN", "scope")
STRING_FIELDS = ("current_level", "scope", "current_progress")

# The server's own generic questions: the opener (nothing is known about the member yet) and the guard when the AI's
# next question is unusable. (option_id, label, value)
_OPTIONS: dict[str, list[tuple[str, str, object]]] = {
    "current_level": [("beginner", "처음이에요", "처음 해봄"), ("some", "조금 해봤어요", "조금 해봄"), ("skilled", "익숙해요", "익숙함")],
    "weekly_available_minutes": [("h2", "주 2시간 이하", 120), ("h3_5", "주 3~5시간", 240), ("h6_10", "주 6~10시간", 480)],
    "constraints": [("none", "없어요", []), ("time", "일정 제약이 있어요", ["일정 제약"]), ("tool", "도구·환경 제약이 있어요", ["도구·환경 제약"])],
    "tools": [("none", "특별히 없어요", [])],
    "current_progress": [("none", "아직 없어요", "아직 준비한 것 없음"), ("some", "조금 있어요", "조금 준비함"), ("much", "많이 진행했어요", "많이 진행함")],
    "preferred_times": [("weekday", "평일", ["평일"]), ("weekend", "주말", ["주말"]), ("any", "상관없어요", ["상관없음"])],
    "support_needed": [("none", "없어요", []), ("team", "팀원 도움이 필요해요", ["팀원 도움"])],
}
_TEXT = {
    "current_level": "이 프로젝트와 관련해 지금 어느 정도 해봤나요?",
    "weekly_available_minutes": "일주일에 프로젝트에 쓸 수 있는 시간은 어느 정도인가요?",
    "scope": "이번 프로젝트에서 맡고 싶은 부분이 있나요?",
    "constraints": "작업에 영향을 주는 일정이나 도구·환경 제약이 있나요?",
    "tools": "쓸 수 있는 도구가 있나요? 있다면 적어 주세요.",
    "current_progress": "지금까지 준비한 자료나 진행 상황이 있나요?",
    "preferred_times": "작업하기 편한 시간대는 언제인가요?",
    "support_needed": "필요한 도움이나 협업 방식이 있나요?",
}


def _generic_options(field: str, brief) -> list[tuple[str, str, object]]:
    if field == "scope":  # buttons from the ProjectBrief's deliverables
        types = list(getattr(brief, "deliverable_types", None) or [])[:4]
        return [(f"o{i}", str(t)[:20], str(t)[:60]) for i, t in enumerate(types)]
    return list(_OPTIONS.get(field, []))


def generic_question(field: str, brief, qid: str) -> dict:
    return {"question_id": qid, "field": field, "text": _TEXT[field],
            "options": [{"option_id": oid, "label": label, "value": value} for oid, label, value in _generic_options(field, brief)]}


# ---- state ------------------------------------------------------------------------------------


def new_state(prior: dict | None) -> dict:
    p = prior or {}
    return {"fields": dict(p.get("fields", {})), "answer_states": dict(p.get("answer_states", {})),
            "asks": dict(p.get("asks", {})), "unknowns": dict(p.get("unknowns", {})), "question": p.get("question")}


def known_answered(state: dict | None) -> set[str]:
    return {f for f, st in (state or {}).get("answer_states", {}).items() if st == "KNOWN"}


def missing_required(state: dict | None) -> list[str]:
    """Required items still open. "모름" counts as answered only after the member said it twice (the AI re-asks once)."""
    states = (state or {}).get("answer_states", {})
    unknowns = (state or {}).get("unknowns", {})
    out = []
    for f in REQUIRED:
        st = states.get(f)
        if st == "KNOWN" or (st == "UNKNOWN" and unknowns.get(f, 0) >= 2):
            continue
        out.append(f)
    return out


def current_question(state: dict | None, brief) -> dict | None:
    """The member's open question: the one the AI wrote last turn, or the server's opener before the first answer."""
    if state and state.get("question"):
        return state["question"]
    if state and state.get("answer_states"):
        return None
    return generic_question("current_level", brief, "q0")


def question_view(state: dict | None, brief) -> dict | None:
    q = current_question(state, brief)
    if q is None:
        return None
    options = [{"option_id": o["option_id"], "label": o["label"]} for o in q["options"]]
    options.append({"option_id": UNKNOWN_ID, "label": UNKNOWN_LABEL})
    return {"question_id": q["question_id"], "text": q["text"], "label": LABELS[q["field"]], "options": options}


def progress_view(state: dict | None) -> dict:
    return {"done": len(REQUIRED) - len(missing_required(state)), "total": len(REQUIRED)}


def meaningful(message: str) -> bool:
    """False for jamo-only, punctuation-only or one-character input: no model call, no field, no turn."""
    words = _WORD.findall(message.strip())
    return bool(words) and (len(words) >= 2 or words[0].isdigit())


# ---- values -------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def valid_value(field: str, value) -> bool:
    if field == "weekly_available_minutes":
        return type(value) is int and 0 < value <= 10080
    if field in STRING_FIELDS:
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 120
    return isinstance(value, list) and len(value) <= 6 and all(isinstance(v, str) and 0 < len(v) <= 60 for v in value)


def _coerce(field: str, value):
    if field == "weekly_available_minutes":
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(시간|분)?\s*", value)
            if m:
                n = float(m[1]) * (60 if m[2] == "시간" else 1)
                return int(n) if n.is_integer() else value
    if field not in STRING_FIELDS and field != "weekly_available_minutes" and isinstance(value, str):
        return [value] if value.strip() and value.strip() not in ("없음", "없어요") else []
    return value


def _bigrams(text: str) -> set[str]:
    t = _norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def _numbers(text: str) -> set[float]:
    return {float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)}


def _grounded(field: str, value, quote: str, message: str) -> bool:
    """The value must be built from the member's own words. A project sentence has no such overlap."""
    if field == "weekly_available_minutes":
        nums = _numbers(quote) | _numbers(message)
        return bool(nums) and (float(value) in nums or float(value) / 60 in nums)
    if isinstance(value, list):
        if not value:
            return "없" in _norm(quote) or "아니" in _norm(quote)
        return all(_norm(v) in _norm(message) for v in value)
    vb = _bigrams(value)
    return bool(vb) and len(vb & _bigrams(message)) / len(vb) >= 0.5


def _fact(value, message_id: str, status: str) -> dict:
    return {"value": value, "origin": "EXTRACTED" if status == "KNOWN" else "UNRESOLVED",
            "evidence": [{"source": "USER_MESSAGE", "source_id": message_id}],
            "reason": "", "review_status": "PENDING", "previous_value": None, "conflicted": False}


def _set(state: dict, field: str, value, status: str, message_id: str) -> None:
    state["fields"][field] = _fact(value if status == "KNOWN" else None, message_id, status)
    state["answer_states"][field] = status
    if status == "UNKNOWN":
        state["unknowns"][field] = state["unknowns"].get(field, 0) + 1


def _fmt(field: str, value) -> str:
    if field == "weekly_available_minutes" and isinstance(value, int):
        h, m = divmod(value, 60)
        return "주 " + (f"{h}시간" if h else "") + (f" {m}분" if m else "")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "없음"
    return str(value)


def _ack(field: str, value) -> str:
    text = f"{LABELS[field]}: {_fmt(field, value)} 기록했어요."
    return text if len(text) <= 60 else f"{LABELS[field]} 기록했어요."


# ---- applying one turn ---------------------------------------------------------------------------


def apply_button(prior: dict | None, brief, question_id: str, option_id: str):
    """(label, field, value|None, status) for a button of the member's CURRENT question, else None."""
    q = current_question(prior, brief)
    if q is None or question_id != q["question_id"]:
        return None
    if option_id == UNKNOWN_ID:
        return UNKNOWN_LABEL, q["field"], None, "UNKNOWN"
    o = next((o for o in q["options"] if o["option_id"] == option_id), None)
    return (o["label"], q["field"], o["value"], "KNOWN") if o else None


def _interpret_text(state: dict, field: str, message: str, mid: str, data) -> tuple[bool, object]:
    text = _norm(message)
    if not (data.understood and data.quote.strip() and _norm(data.quote) in text):
        return False, None
    quote = data.quote.strip()
    value = _coerce(field, data.value)
    if field in STRING_FIELDS and not (valid_value(field, value) and _grounded(field, value, quote, message)):
        value = quote[:120]  # fall back to the member's own words
    if not valid_value(field, value) or not _grounded(field, value, quote, message):
        return False, None
    _set(state, field, value, "KNOWN", mid)
    return True, value


def _apply_extras(state: dict, message: str, mid: str, data, skip: str) -> None:
    text = _norm(message)
    for extra in data.extras[:4]:
        f = extra.field
        quote = extra.quote.strip()
        if f == skip or not quote or _norm(quote) not in text:
            continue
        if state["answer_states"].get(f) == "KNOWN" and not _CORRECT.search(message):
            continue  # a known item changes only when the member corrects it
        if f == "scope" and not _INTENT.search(quote):
            continue  # scope only from an explicit "맡고 싶다/할 수 있다"
        v = _coerce(f, extra.value)
        if f in STRING_FIELDS and not (valid_value(f, v) and _grounded(f, v, quote, message)):
            v = quote[:120]
        if valid_value(f, v) and _grounded(f, v, quote, message):
            _set(state, f, v, "KNOWN", mid)


def _usable_text(text: str, limit: int) -> bool:
    t = text.strip()
    return 2 <= len(t) <= limit and "\n" not in t and not any(b in t for b in _BANNED)


def _clean_options(field: str, raw, brief) -> list[dict]:
    out, seen = [], set()
    for o in raw[:4]:
        label = o.label.strip()
        if not (1 <= len(label) <= 20) or label in seen or any(b in label for b in _BANNED) or label == UNKNOWN_LABEL:
            continue
        value = _coerce(field, o.value) if o.value is not None else (label if field in STRING_FIELDS else None)
        if field in STRING_FIELDS and isinstance(value, str):
            value = value[:120]
        if not valid_value(field, value):
            continue
        seen.add(label)
        out.append({"option_id": f"o{len(out)}", "label": label, "value": value})
    return out


def _choose_next(state: dict, brief, data, field_just_asked: str, understood: bool, turns_left: int, qid: str, current=None):
    """The question to show next: the AI's when it is valid, else the server's generic question for the first missing item."""
    missing = missing_required(state)
    asks = state["asks"]
    nxt = data.next
    chosen = None
    if nxt is not None and _usable_text(nxt.text, 140):
        f = nxt.field
        st = state["answer_states"].get(f)
        reask = (f == field_just_asked and not understood) or (st == "UNKNOWN" and state["unknowns"].get(f, 0) < 2)
        open_ = st != "KNOWN" and (reask or asks.get(f, 0) == 0)
        if open_ and (not missing or f in missing or turns_left > len(missing)):
            opts = _clean_options(f, nxt.options, brief)
            if len(opts) >= 2:
                chosen = {"question_id": qid, "field": f, "text": nxt.text.strip(), "options": opts}
    if chosen is None and not understood and current and state["answer_states"].get(current["field"]) != "KNOWN" and turns_left >= 1:
        return current, False  # not understood and the AI gave nothing usable: the same question stays
    guarded = False
    if chosen is None and missing and turns_left >= 1:
        chosen = generic_question(missing[0], brief, qid)
        guarded = True
    if chosen is not None:
        f = chosen["field"]
        if not (f == field_just_asked and not understood):
            asks[f] = asks.get(f, 0) + 1
    return chosen, guarded


@dataclass(frozen=True)
class CoachOutcome:
    ok: bool
    state: dict | None
    reply: str | None
    error_code: str | None
    model: str | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    guarded: bool = False


def run_turn(client, owner_id: str, prior: dict | None, brief, message: str, button, seq: int) -> CoachOutcome:
    """One AI turn (one Timely call): interpret the answer, then lead with the next question and buttons."""
    from wemeet_ai.m0 import Runtime
    from wemeet_ai.m1.e_member_plan import TurnRequest, interview_turn

    q = current_question(prior, brief)
    state = new_state(prior)
    mid = f"t{seq}u"
    field = q["field"] if q else "current_level"
    state["asks"].setdefault(field, 1)  # the question on screen counts as asked
    if button is not None:  # a button is already a final answer; the server applies it
        _, bf, bvalue, bstatus = button
        _set(state, bf, bvalue, bstatus, mid)
    project = {"name": brief.name, "content": brief.content, "start": brief.start_date.isoformat(), "end": brief.end_date.isoformat(),
               "deliverables": list(brief.deliverable_types)}
    known = {f: _fmt(f, (state["fields"].get(f) or {}).get("value")) for f in known_answered(state)}
    request = TurnRequest(
        owner_member_id=owner_id, project=project, question_field=field, question=(q or {}).get("text", _TEXT[field]),
        answer=message, answer_kind="button" if button is not None else "text", known=known,
        asked_unknown=sorted(f for f, st in state["answer_states"].items() if st == "UNKNOWN"),
        missing_required=missing_required(state), turns_left=max(0, MAX_TURNS - seq),
    )
    runtime = Runtime(client, extra_options={"reasoning_effort": "low"})
    result = interview_turn(request, runtime=runtime)
    usage = result.meta.usage
    common = dict(model=result.meta.model, latency_ms=result.meta.latency_ms, prompt_tokens=usage.prompt_tokens,
                  completion_tokens=usage.completion_tokens, total_tokens=usage.total_tokens)
    if result.status != "OK" or result.data is None:
        return CoachOutcome(False, None, None, result.error.code if result.error else "NEEDS_INPUT", **common)
    data = result.data
    understood, value = True, None
    if button is not None:
        reply = _ack(button[1], button[2]) if button[3] == "KNOWN" else "모른다고 기록했어요."
    else:
        understood, value = _interpret_text(state, field, message, mid, data)
        if understood:
            reply = _ack(field, value)
        else:
            r = data.reply.strip()
            reply = r if _usable_text(r, 60) else MSG_UNCLEAR
    _apply_extras(state, message, mid, data, skip=field)
    nq, guarded = _choose_next(state, brief, data, field, understood, max(0, MAX_TURNS - seq), f"q{seq}", q)
    state["question"] = nq
    return CoachOutcome(True, state, reply, None, guarded=guarded, **common)


# ---- the owner's view of a MemberState (right panel) ---------------------------------------------


def state_rows(state: dict | None) -> list[dict]:
    """[{"field", "label", "status", "value"}] for every interview field. Only the owner ever receives this."""
    fields = (state or {}).get("fields", {})
    answers = (state or {}).get("answer_states", {})
    rows = []
    for f in ORDER:
        status = answers.get(f)
        value = _fmt(f, (fields.get(f) or {}).get("value")) if status == "KNOWN" else None
        rows.append({"field": f, "label": LABELS[f], "required": f in REQUIRED, "status": status or "EMPTY", "value": value})
    return rows
