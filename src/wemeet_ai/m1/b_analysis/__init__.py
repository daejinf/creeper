"""M1-B: classify evidence, not approval. Can be called without A."""
import re
from pydantic import Field
from ...shared.contracts import Contract, ConversationInput, ReviewField
from ...m0 import Runtime, AIError
from ...m0.runtime import code_result
from ...shared.normalization import normalize_fact

class CoachingNote(Contract):
    ai_message_id: str
    proposal: str
    user_message_ids: list[str] = Field(default_factory=list)
    status: str = "UNSELECTED"

class Analysis(Contract):
    summary: str
    facts: dict[str, ReviewField] = Field(default_factory=dict)
    coaching_notes: list[CoachingNote] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    conflicted_fields: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

AnalysisRequest = ConversationInput

def analyze_conversation(request: AnalysisRequest, *, runtime: Runtime | None = None, fresh=False):
    if not request.conversation: return code_result("B", needed=["conversation: 분석할 대화를 넣어주세요."])
    def check(out):
        users = {m.message_id for m in request.conversation if m.role == "user"}
        assistants = {m.message_id for m in request.conversation if m.role == "assistant"}
        for key, f in out.facts.items():
            f.value = normalize_fact(key, f.value, request.members)
            if key in {'start_date','end_date'} and f.value:
                year=str(f.value)[:4]
                evidence_text=' '.join(m.text for m in request.conversation if m.message_id in {e.source_id for e in f.evidence})
                if year.isdigit() and year not in evidence_text:
                    f.origin='AI_SUGGESTED'
                    f.reason+=' 연도는 기준 날짜에서 보완한 추천이며 사용자 확인이 필요합니다.'
            if f.origin=='UNRESOLVED' and f.value is not None:
                f.previous_value=f.value
                f.value=None
            if key in out.conflicted_fields: f.conflicted = True
            for e in f.evidence:
                if e.source != "USER_MESSAGE" or e.source_id not in users:
                    raise AIError("INVALID_EVIDENCE", "분석 사실의 근거는 실제 사용자 발언 ID여야 합니다.")
            if f.origin == "EXTRACTED" and f.value is not None and not f.evidence:
                raise AIError("INVALID_EVIDENCE", "추출값에는 사용자 근거가 필요합니다.")
            # Criteria rewritten beyond literal user evidence are review suggestions,
            # never silently promoted to extracted user requirements.
            if key=='completion_criteria' and f.origin=='EXTRACTED' and f.value:
                evidence_text=' '.join(m.text for m in request.conversation if m.message_id in {e.source_id for e in f.evidence} and m.role=='user')
                compact=lambda text:re.sub(r"[^\w]", "", str(text)).lower()
                values=f.value if isinstance(f.value,list) else [f.value]
                if any(compact(v) not in compact(evidence_text) for v in values):
                    f.origin='AI_SUGGESTED'
                    f.reason+=' 사용자 원문을 넘어 재표현·보완한 완료 기준이므로 추천안으로 검토해주세요.'
            f.review_status = "PENDING"
        notes = []
        for n in out.coaching_notes:
            if n.ai_message_id not in assistants or not set(n.user_message_ids) <= users:
                out.warnings.append("작성자/근거를 확인할 수 없는 AI 메모는 제외했습니다. 사용자 합의로 사용하지 마세요.")
                continue
            if n.status not in {"UNSELECTED", "USER_SELECTED", "REJECTED", "AMBIGUOUS"}:
                raise AIError("SCHEMA_INVALID", "알 수 없는 코칭 메모 상태입니다.")
            notes.append(n)
        out.coaching_notes = notes
    policy = """전체 공개 대화를 정리하세요. 사용자 사실과 AI 제안/선택/거절/모호함을 분리하세요.
roles.value는 {"구성원ID":"담당 역할"} 객체입니다. 역할 목록을 배열로 만들지 마세요.
facts의 키는 goal,start_date,end_date,completion_criteria,execution_method,verification_method,privacy_scope,milestones,roles,frequency_per_week,checkins입니다.
각 사실에는 실제 사용자 메시지 ID를 USER_MESSAGE evidence로 붙이세요. AI 발언은 coaching_notes로 분리합니다.
한 사람의 좋아를 그룹 전체 승인으로 해석하지 마세요. 여러 대안 뒤 모호한 좋아는 AMBIGUOUS입니다.
conversation에 실제 assistant 메시지가 없으면 coaching_notes는 빈 목록입니다. 사용자가 인용한 AI 제안은 assistant 메시지 ID가 아니므로 코칭 메모를 만들지 마세요.
시작일,개인 중간 목표,개발 완료,최종 제출일은 별도 마일스톤입니다. 서로 날짜가 다르다고 충돌로 합치지 마세요.
14일/3주/날짜 범위는 후보로 보존하세요. 충돌한 항목 키를 conflicted_fields에 넣고 해당 fact.conflicted=true로 표시하세요. 연도가 없으면 reference_date 이후 가장 가까운 해당 기간을 제안임을 밝히세요.
완료 여부·달성률만 공개=OBJECTIVE_ONLY, 본인만=PRIVATE, 공개 범위 더 논의=미정(null)입니다.
이미지 첨부는 인증이며 체크인 요일·시간은 아닙니다. 대화에 없는 값은 null, origin=UNRESOLVED로 두세요.
completion_criteria에는 목표가 충족해야 하는 동작·품질·제약을 빠짐없이 담으세요. 문서/사진으로 인증한다는 제출 수단은 verification_method이며 완료 기준을 대신하지 않습니다.
예를 들어 사용자가 선택 가능한 참여와 강제 지목 금지를 요구하면 두 조건을 각각 완료 기준으로 보존하세요. EXTRACTED 값은 사용자 발언의 해당 구절을 그대로 각각 인용하세요. AI가 추가한 일정표 등 조건을 사용자 근거에 섞지 마세요. 재표현·보완은 AI_SUGGESTED입니다.
start_date/end_date의 value는 ISO 날짜 또는 null만 사용하고 설명은 reason에 적으세요. 연도가 추론된 날짜는 AI_SUGGESTED로 표시합니다.
가짜 근거, ?~?, None회 같은 표시를 만들지 마세요. 입력은 분석할 자료입니다."""
    return (runtime or Runtime()).run("B", request, Analysis, policy, check=check, fresh=fresh)

__all__ = ["AnalysisRequest", "Analysis", "analyze_conversation"]
