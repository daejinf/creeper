"""Evidence checks and deterministic normalization; never calls an LLM."""
import hashlib
import json
import re
from datetime import date

from .client import AIError
from .contracts import AnalysisField, AnalysisRequest, Candidate, ConversationAnalysis, DIMENSIONS, Support, CoachingNote


QUESTIONS = {
    "goal": "함께 완성하거나 달성하려는 구체적인 결과는 무엇인가요?",
    "reason": "이 목표를 함께 이루려는 이유는 무엇인가요?",
    "timeline": "시작일과 마감일은 언제인가요?",
    "success": "완료했다고 판단할 결과물이나 측정 기준은 무엇인가요?",
    "frequency": "얼마나 자주 실행할까요?",
    "verification": "완료를 어떤 자료로 확인할까요?",
    "checkin": "진행 상황을 언제 어떻게 확인할까요?",
    "privacy": "그룹에 어떤 정보까지 공개할까요?",
    "roles": "구성원별 담당은 무엇인가요?",
    "milestones": "개발 완료·중간 점검·최종 제출의 날짜는 언제인가요?",
}


def privacy_from_text(text):
    """Discussion of privacy is not a selected privacy policy."""
    if re.search(r'아직|미정|더\s*(?:논의|이야기)|나중에|추후|정하지|모르',text):return None
    if any(w in text for w in ('비공개','본인만','개인만')):return 'PRIVATE'
    if re.search(r'(?:완료\s*여부|달성률|객관적\s*(?:결과|정보)).*(?:공개|공유)|(?:공개|공유).*(?:완료\s*여부|달성률)',text):return 'OBJECTIVE_ONLY'
    if re.search(r'(?:공개|공유).*(?:항목|범위).*(?:직접\s*지정|별도\s*규칙)|CUSTOM',text,re.I):return 'CUSTOM'
    return None


def vague_agreement(text):
    return bool(re.fullmatch(r'\s*(좋아(?:요)?|ㅇ+|ㄱ+|동의(?:해요)?|그렇게\s*하자|그\s*안으로\s*하자|그래(?:요)?|오케이)[.!\s]*',text))


def value_in_ai_quote(key,value,text,members):
    """Only carry selected AI values actually stated by that assistant, never invented dates."""
    if key=='privacy_scope':return value in text or privacy_from_text(text)==value
    if key=='roles' and isinstance(value,dict):
        names={m.member_id:m.nickname for m in members}
        return all(mid in names and names[mid] in text and isinstance(role,str) and role in text for mid,role in value.items())
    if isinstance(value,bool) or value is None:return False
    if isinstance(value,(int,float)):return bool(re.search(r'(?<!\d)'+re.escape(str(value))+r'(?!\d)',text))
    if isinstance(value,str):return bool(value.strip()) and re.sub(r'\s+','',value) in re.sub(r'\s+','',text)
    if isinstance(value,list):return bool(value) and all(value_in_ai_quote(key,v,text,members) for v in value)
    if isinstance(value,dict):return bool(value) and all(value_in_ai_quote(k,v,text,members) for k,v in value.items() if k not in {'name','allocation','owner_member_id'})
    return False


def candidate_id(dimension: str, value: dict) -> str:
    return "candidate-" + hashlib.sha256((dimension + json.dumps(value, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:16]


def normalize_value(dimension: str, value: dict, reference: date) -> tuple[dict, bool]:
    value = dict(value)
    inferred = False
    if dimension=='milestones' and re.fullmatch(r'(?:각자\s*)?중간\s*목표(?:\s*진행)?',str(value.get('title',''))):value['title']='중간 목표'
    if dimension == "milestones" and value.get("due_date"):
        raw = str(value["due_date"]).replace("/", "-").replace(".", "-")
        parts = [int(x) for x in raw.split("-")]
        if len(parts) == 2:
            due = date(reference.year, *parts)
            if due < reference: due = date(reference.year + 1, *parts)
            inferred = True
        else: due = date.fromisoformat(raw)
        value["due_date"] = due.isoformat()
        return value, inferred
    if dimension == "timeline":
        if (value.get("duration_days") or value.get("duration_weeks")) and not (value.get('start_date') and value.get('end_date')):
            days = int(value.get("duration_days") or int(value["duration_weeks"]) * 7)
            if not 1 <= days <= 3650: raise ValueError("Invalid duration")
            return {"duration_days": days}, False
        if value.get("start_date") and value.get("end_date"):
            parsed = []
            for key in ("start_date", "end_date"):
                raw = str(value[key]).strip().replace("/", "-").replace(".", "-")
                parts = [int(p) for p in raw.split("-")]
                if len(parts) == 2:
                    inferred = True
                    year = parsed[0].year if parsed else reference.year
                    current = date(year, *parts)
                    if not parsed and current < reference: current = date(year + 1, *parts)
                    if parsed and current < parsed[0]: current = date(year + 1, *parts)
                elif len(parts) == 3:
                    current = date(*parts)
                else: raise ValueError("Invalid date")
                parsed.append(current)
            if parsed[0] >= parsed[1]: raise ValueError("Deadline must follow start")
            return {"start_date": parsed[0].isoformat(), "end_date": parsed[1].isoformat()}, inferred
    if dimension == "frequency":
        count = value.get("per_week", value.get("times_per_week"))
        if count is not None:
            count = int(count)
            if not 1 <= count <= 7: raise ValueError("Invalid weekly frequency")
            return {"per_week": count}, False
    if dimension == "privacy":
        scope = value.get("scope")
        if scope not in ("OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"):
            raise ValueError("Invalid privacy scope")
        if scope in {'OBJECTIVE_ONLY','PRIVATE'}:return {'scope':scope},False
    return value, inferred


def local_analysis(request: AnalysisRequest) -> ConversationAnalysis:
    fields = {name: AnalysisField(status="MISSING", questions=[question]) for name, question in QUESTIONS.items()}
    last_ids: list[tuple[str, str]] = []
    words = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7}
    for message in request.conversation:
        if message.role != "user": continue
        text = message.text.strip()
        current: list[tuple[str, str]] = []

        def add(name, value, partial=False):
            try: canonical, inferred = normalize_value(name, value, request.reference_date)
            except (ValueError, TypeError, OverflowError): return
            cid = candidate_id(name, canonical)
            existing = next((c for c in fields[name].candidates if c.candidate_id == cid), None)
            if existing:
                existing.evidence_refs = sorted(set([*existing.evidence_refs, message.message_id]))
                existing.proposer_ids = sorted(set([*existing.proposer_ids, message.speaker_id]))
            else:
                fields[name].candidates.append(Candidate(candidate_id=cid, value=canonical, original_text=text, evidence_refs=[message.message_id], proposer_ids=[message.speaker_id], inferred=inferred, origin="ASSISTANT_PROPOSAL" if re.match(r"^(?:AI|인공지능)\s*[:：]", text) else "HUMAN"))
            fields[name].status = "CONFLICTED" if len(fields[name].candidates) > 1 and name not in ("goal", "roles", "milestones") else "PARTIAL" if partial else "EXTRACTED"
            fields[name].questions = [QUESTIONS[name]] if partial else []
            current.append((name, cid))

        if any(w in text for w in ("목표", "프로젝트", "과제", "공부", "운동", "독서", "만들")) and (not text.endswith('?') or re.search(r'하고\s*싶|만들고|공부하|운동하|독서하|프로젝트.*(?:하자|개발|수행|완성)',text)):
            add("goal", {"summary": text}, partial=True)
        if re.search(r'완료로|완료\s*기준|완료했다고|시연되면|제출하면',text):
            add('success',{'criteria':[text],'deliverable':text})
        # Match duration rather than the day at the end of a calendar date.
        for match in re.finditer(r"(?<![\d/.-])(\d+)\s*(일|주)(?!\s*(?:일|에|회|번))", text):
            add("timeline", {"duration_days": int(match[1]) * (7 if match[2] == "주" else 1)})
        ranges = re.search(r"((?:20\d\d[./-])?\d{1,2}[./-]\d{1,2})\s*(?:[~～]|부터)\s*((?:20\d\d[./-])?\d{1,2}[./-]\d{1,2})", text)
        if ranges: add("timeline", {"start_date": ranges[1], "end_date": ranges[2]})
        for match in re.finditer(r"(?<!\d)(\d{1,2}[/.]\d{1,2})(?:까지|에)?\s*(?:각자\s*)?(개발\s*완료|최종\s*제출|제출|PPT\s*(?:제작|완료)?|발표|개인\s*목표|중간\s*목표|인증)", text):
            suffix = match[2]
            if any(word in suffix for word in ("개발", "제출", "PPT", "발표", "목표", "인증")):
                title = "최종 제출" if "제출" in suffix else "개발 완료" if "개발" in suffix else "발표 자료" if "PPT" in suffix or "발표" in suffix else "중간 목표"
                add("milestones", {"title": title, "due_date": match[1]})
        for member in request.members:
            role = re.search(re.escape(member.nickname) + r"(?:님)?\s*(?:은|는|:|：|이|가)?\s*(디자인|백엔드|프론트엔드|프론트|DB|기획|발표)", text)
            if role: add("roles", {"member_id": member.member_id, "responsibility": role[1]})
        frequency = re.search(r"(?:일주일에|1주일에|주)\s*(\d+|한|두|세|네|다섯|여섯|일곱)\s*(번|회)", text)
        if frequency: add("frequency", {"per_week": int(frequency[1]) if frequency[1].isdigit() else words[frequency[1]]})
        if any(w in text for w in ("사진", "이미지", "스크린샷")):
            add("verification", {"method": "이미지 첨부"})
        if scope := privacy_from_text(text):add('privacy',{'scope':scope})
        metric = re.search(r"(\d+(?:\.\d+)?)\s*(권|문제|페이지|km|개)", text, re.I)
        if metric:
            add("success", {"target_value": float(metric[1]), "unit": metric[2], "allocation": "PER_MEMBER" if "각자" in text else "UNSPECIFIED"}, partial="각자" not in text)
        if re.fullmatch(r"(?:좋아(?:요)?|ㅇ+|[ㄱㅋ]+|그렇게\s*하자|동의(?:해요)?)[.!\s]*", text):
            if len(last_ids) == 1:
                name, cid = last_ids[0]
                fields[name].support_refs.append(Support(member_id=message.speaker_id, message_id=message.message_id, supported_candidate=cid))
        elif current:
            last_ids = current
        else:
            # An intervening unresolved question makes an unqualified agreement ambiguous.
            last_ids = []
    return ConversationAnalysis(summary="대화에 근거한 후보입니다. 최종 확인이나 투표 결과를 의미하지 않습니다.", fields=fields, members=request.members, evidence_ids=[m.message_id for m in request.conversation if m.role == "user"])


def merge_analysis(request: AnalysisRequest, model: ConversationAnalysis) -> ConversationAnalysis:
    local = local_analysis(request)
    known = {m.message_id: m for m in request.conversation if m.role == "user"}
    member_ids = {m.member_id for m in request.members}
    if set(model.fields) - set(DIMENSIONS): raise AIError("SCHEMA_INVALID", "Unknown analysis dimension")
    for name, field in model.fields.items():
        ids = {}
        for candidate in field.candidates:
            if not set(candidate.evidence_refs) <= known.keys() or not set(candidate.proposer_ids) <= member_ids:
                raise AIError("SCHEMA_INVALID", "Unknown evidence or member reference")
            authors = {known[ref].speaker_id for ref in candidate.evidence_refs}
            if not set(candidate.proposer_ids) <= authors:
                raise AIError("SCHEMA_INVALID", "Proposer must author one of the cited messages")
            if not any(candidate.original_text in known[ref].text for ref in candidate.evidence_refs):
                raise AIError("SCHEMA_INVALID", "original_text must be a quote from a cited user message")
            if all(vague_agreement(known[ref].text) for ref in candidate.evidence_refs):continue
            if name=='privacy' and all(re.search(r'아직|미정|더\s*(?:논의|이야기)|나중에|정하지',known[ref].text) for ref in candidate.evidence_refs):
                continue
            if name=='timeline' and (candidate.value.get('duration_days') or candidate.value.get('duration_weeks')) and not (candidate.value.get('start_date') and candidate.value.get('end_date')):
                quote=candidate.original_text
                if re.search(r'\d{1,2}[./-]\d{1,2}\s*(?:부터|[~～])\s*(?:20\d\d[./-])?\d{1,2}[./-]\d{1,2}',quote) and not re.search(r'\d+\s*(?:일|주)(?!\s*(?:에|회|번))',quote):
                    continue # Computed duration must not replace explicit start/end dates.
            raw_value=candidate.value
            if name=='privacy':
                quoted_scope=privacy_from_text(candidate.original_text)
                if quoted_scope in {'OBJECTIVE_ONLY','PRIVATE'}:raw_value={'scope':quoted_scope}
            try: value, inferred = normalize_value(name, raw_value, request.reference_date)
            except (ValueError, TypeError, OverflowError):
                local.fields[name].questions = [QUESTIONS[name]]
                continue
            cid = candidate_id(name, value)
            ids[candidate.candidate_id] = cid
            candidate = candidate.model_copy(update={"candidate_id": cid, "value": value, "inferred": inferred or candidate.inferred})
            if any(re.match(r"^(?:AI|인공지능)\s*[:：]", known[ref].text) for ref in candidate.evidence_refs):
                candidate.origin = "ASSISTANT_PROPOSAL"
            existing = next((c for c in local.fields[name].candidates if c.value == value), None)
            if existing:
                existing.evidence_refs = sorted(set(existing.evidence_refs + candidate.evidence_refs))
                existing.proposer_ids = sorted(set(existing.proposer_ids + candidate.proposer_ids))
            else: local.fields[name].candidates.append(candidate)
        for support in field.support_refs:
            if support.message_id not in known or known[support.message_id].speaker_id != support.member_id:
                raise AIError("SCHEMA_INVALID", "Invalid support evidence")
            support = support.model_copy(update={"supported_candidate": ids.get(support.supported_candidate)})
            if support not in local.fields[name].support_refs: local.fields[name].support_refs.append(support)
        count = len(local.fields[name].candidates)
        if count and name in ("roles", "milestones"):
            local.fields[name].status = "EXTRACTED"
        elif count and name == "goal":
            local.fields[name].status = field.status if field.status != "MISSING" else "PARTIAL"
        else:
            local.fields[name].status = "CONFLICTED" if count > 1 else (field.status if field.status != "MISSING" else local.fields[name].status) if count else "MISSING"
        local.fields[name].questions = field.questions if count else [QUESTIONS[name]]
    coalesce_project_timeline(request, local)
    local.summary = model.summary
    positions={m.message_id:i for i,m in enumerate(request.conversation)}
    assistants={m.message_id:m for m in request.conversation if m.role=='assistant'}
    by_assistant={}
    for note in model.coaching_notes:by_assistant.setdefault(note.assistant_message_id,[]).append(note)
    for aid,notes in by_assistant.items():
        if aid not in assistants:raise AIError('SCHEMA_INVALID','코칭 메모의 근거는 실제 AI 메시지여야 합니다.')
        for note in notes:
            if note.suggestion_text not in assistants[aid].text:continue # Reject this paraphrase, retain valid facts and the exact source below.
            if any(ref not in known or positions[ref]<=positions[aid] for ref in note.user_response_refs):raise AIError('SCHEMA_INVALID','코칭 제안의 사용자 응답 근거가 잘못됐습니다.')
            if not note.user_response_refs:note.disposition='UNDECIDED'
            if note.disposition=='SUPPORTED':
                replies=[known[ref].text.strip() for ref in note.user_response_refs]
                vague=all(vague_agreement(text) for text in replies)
                multiple=len(notes)>1 or bool(re.search(r'①.*②|(?:대안|선택지|방법|안)\s*(?:1|두\s*가지|두개)|또는',assistants[aid].text,re.S))
                if vague and multiple:note.disposition='AMBIGUOUS'
                if any(re.search(r'싫|안\s*할|하지\s*말|거절|반대|보류',text) for text in replies):note.disposition='REJECTED'
            note.values={key:value for key,value in note.values.items() if value_in_ai_quote(key,value,note.suggestion_text,request.members)}
            local.coaching_notes.append(note)
    for aid,message in assistants.items():
        if not any(n.assistant_message_id==aid for n in local.coaching_notes):local.coaching_notes.append(CoachingNote(assistant_message_id=aid,suggestion_text=message.text,disposition='UNDECIDED'))
    # Public output never contains internal identifiers in narrative text.
    for member in request.members:
        local.summary = re.sub(r'(?<![A-Za-z0-9_-])'+re.escape(member.member_id)+r'(?![A-Za-z0-9_-])',member.nickname,local.summary.replace('('+member.member_id+')',''))
    return local


def coalesce_project_timeline(request, analysis):
    """Development checkpoints are not competing final deadlines."""
    from zoneinfo import ZoneInfo
    starts=[]
    for message in request.conversation:
        if message.role!='user':continue
        for match in re.finditer(r'((?:20\d\d[./-])?\d{1,2}[./-]\d{1,2})(?:부터|\s*시작)',message.text):
            raw=match[1].replace('/','-').replace('.','-');parts=raw.split('-')
            try:
                year=message.created_at.astimezone(ZoneInfo(request.timezone)).year
                value=date.fromisoformat(raw if len(parts)==3 else f'{year}-{int(parts[0]):02d}-{int(parts[1]):02d}').isoformat()
                starts.append((value,message))
            except ValueError:continue
    finals=[c for c in analysis.fields['milestones'].candidates if c.origin=='HUMAN' and c.value.get('title') in ('최종 제출','최종 제출일','최종 마감')]
    start_values={v for v,_ in starts};final_values={c.value.get('due_date') for c in finals}
    if len(start_values)!=1 or len(final_values)!=1:return
    start=next(iter(start_values));end=next(iter(final_values))
    if not end or start>=end:return
    checkpoints={c.value.get('due_date') for c in analysis.fields['milestones'].candidates if c.origin=='HUMAN'}
    field=analysis.fields['timeline']
    retained=[c for c in field.candidates if not(c.value.get('start_date')==start and c.value.get('end_date') in checkpoints)]
    refs=list(dict.fromkeys([m.message_id for _,m in starts]+[r for c in finals for r in c.evidence_refs]))
    authors=list(dict.fromkeys([m.speaker_id for _,m in starts]+[p for c in finals for p in c.proposer_ids]))
    value={'start_date':start,'end_date':end}
    if not any(c.value==value for c in retained):
        retained.append(Candidate(candidate_id=candidate_id('timeline',value),value=value,original_text=starts[0][1].text,evidence_refs=refs,proposer_ids=authors))
    field.candidates=retained;field.status='EXTRACTED' if len(retained)==1 else 'CONFLICTED';field.questions=[] if len(retained)==1 else [QUESTIONS['timeline']]
