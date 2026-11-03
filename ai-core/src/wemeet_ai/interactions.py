"""M1-A/B/C interaction policies and draft authoring, independent of adapters."""
import json
import re
from uuid import uuid4

from .client import AIError
from .contracts import CoachReply, CoachState, ConfirmedDecisions, DraftField, GoalSpecDraft, PrivateIntakeAnalysis, Result
from .privacy import sanitize
from .calendar_context import calendar_context
from .tracing import emit


def wants_plan(text):
    return bool(re.search(r"(?:계획|일정|실행안|초안).*(?:만들|작성|짜|줘|부탁)|(?:만들|작성|짜).*(?:계획|초안)|어떻게.*(?:해야|하면)|뭐.*해야", text))


def questions_in(text):
    return [q.strip() for q in re.findall(r"[^\n.!?？]*[?？]", text) if q.strip()]


class InteractionModules:
    def _coach_reply_v2(self, request):
        def operation(stats):
            dialogue=request.coaching_mode=='DIALOGUE'
            latest = next((m.text for m in reversed(request.conversation) if m.role == "user"), "")
            assistants = [m for m in request.conversation if m.role == "assistant"]
            historical = 0
            for message in reversed(assistants):
                if questions_in(message.text): historical += 1
                else: break
            count = min(2, max(historical, request.coach_state.consecutive_questions))
            proposed = wants_plan(latest)
            answered = list(request.coach_state.answered_topics)
            for index, message in enumerate(request.conversation[:-1]):
                if message.role == "assistant" and request.conversation[index + 1].role == "user":
                    answered.extend(questions_in(message.text))
            answered = list(dict.fromkeys(answered))[-50:]
            if request.coach_state.last_question_topic and latest.strip():
                answered = list(dict.fromkeys([*answered, request.coach_state.last_question_topic]))
            allow = count < 2 and (dialogue or not proposed)
            emit("coach.policy", {"allow_question": allow, "consecutive_questions": count, "plan_requested": proposed, "answered_topics": answered})
            names = {m.member_id: m.nickname for m in request.members}
            context = request.shared_context.model_dump(mode="json")
            if request.scope == "PRIVATE":
                context["roles"] = {mid: role for mid, role in context["roles"].items() if mid == request.owner_member_id}
            instructions = (
                "당신은 실행을 돕는 한국어 목표 코치입니다. 제안과 다음 행동을 먼저 설명하세요. 습관적으로 질문으로 끝내지 마세요. "
                "일반적인 보완 정보는 합리적인 가정으로 표시하고 초안을 작성할 수 있습니다. 사용자가 계획을 요청하면 즉시 구체적인 실행 초안을 작성합니다. "
                "실제로 대답이 없으면 현재 작업을 진행할 수 없는 경우에만 질문 하나를 허용합니다. "
                "이미 답한 주제는 재질문하지 마세요. '없어', 'ㄴㄴ'는 명확한 직전 질문에만 연결하고 모호하면 사실을 뒤집지 마세요. "
                "'완벽하게', '고민 금지', '이렇게 확정'처럼 압박하거나 임의 확정하지 마세요. UI와 PPT를 모두 맡았다고 하면 두 작업을 반영하고 다시 범위를 묻지 마세요. "
                "공개 그룹 문맥은 개인 대화에서도 사용하되 다른 구성원의 개인 조건을 요청하지 마세요. 공개 문맥이 없으면 한 번 짧게 알리고 가능한 실행안을 작성합니다. "
                "각 메시지의 오늘/내일은 메시지 작성일을 기준으로 해석합니다. AI가 제안한 내용은 합의가 아닙니다. "
                + ("이번 응답에는 질문·추가 정보 요청을 절대 넣지 마세요. 빠진 내용은 '가정'으로 표시하고 실행안을 제시하세요. " if not allow else "질문 없이 답할 수 있으면 질문하지 마세요. ")
                + f"기준 날짜 {request.reference_date}, 시간대 {request.timezone}. "
                + "이미 답한 질문: " + json.dumps(answered, ensure_ascii=False)
                + "\nPUBLIC_SHARED_CONTEXT:\n" + json.dumps(sanitize(context), ensure_ascii=False)
            )
            messages = [{"role": "system", "content": instructions}]
            if dialogue:
                messages[0]['content']=(
                    '당신은 그룹 목표를 함께 구체화하는 적극적인 대화 코치입니다. 자연어 채팅 답변만 작성하세요. JSON·명세 필드·폼·선택 버튼을 만들지 않습니다. 필요한 가정이라는 긴 전제 목록은 쓰지 말고, 정해진 내용·AI 추천안·답이 필요한 내용을 쉬운 말로 구분하세요. '
                    '공동 목표·완료 기준·시작일·최종 제출일·수행 방법·인증·공개 범위를 대화에서 적극적으로 파악하세요. 이미 말한 정보를 재질문하지 말고, 필요한 답이 계획에 왜 영향을 주는지 짧게 설명하세요. 현재 의견의 장단점과 빠진 중요한 조건을 짚고, 실행 가능한 대안과 이유를 구체적으로 말하세요. 사용자가 계획을 요청하면 질문보다 실행 초안을 먼저 제시하세요. '
                    '당신의 제안과 사용자 발언을 구분하고, 제안을 이미 결정·합의한 사실처럼 말하지 마세요. 없는 날짜·분량은 제안임을 밝히며 임의 확정하지 않습니다. '
                    '대답을 정해진 순서·형식으로 요구하지 말고, 수정·거절·보류와 짧은 답을 문맥에 맞게 이해하세요. 모호한 동의를 여러 대안 중 하나로 추정하지 마세요. '
                    '이미 답한 질문을 반복하거나 모든 대답 끝에 질문하지 마세요. 필요한 경우 핵심 질문 하나만 허용합니다. 개인의 민감한 조건은 그룹 채팅에서 묻지 않습니다. '
                    '두 차례 연속 질문 후에는 현재 내용의 요약과 다음 실행안을 제시하세요. 다른 단계 실행·명세 저장·전체 합의를 완료했다고 말하지 마세요. '
                    +('이번에는 질문 없이 조언·제안을 작성하세요. ' if not allow else '')
                    +f'기준 날짜 {request.reference_date}, 시간대 {request.timezone}. 오늘/내일은 각 발언의 created_at을 기준으로 해석합니다. '
                    +'이미 답한 질문: '+json.dumps(answered,ensure_ascii=False)
                    +'\nPUBLIC_SHARED_CONTEXT:\n'+json.dumps(sanitize(context),ensure_ascii=False)
                    +'\nCALENDAR_FACTS: '+json.dumps(calendar_context(request.reference_date,' '.join(m.text for m in request.conversation if m.role=='user')),ensure_ascii=False)+'\n공개 범위는 목표 활동의 그룹 공유 정책이며 웹사이트 자체의 공개/호스팅과 구분합니다. 날짜의 요일은 CALENDAR_FACTS만 사용하고 체크인 날짜를 임의로 추가하지 마세요.'
                    +'\nDISCUSSION_FOCUS: '+str(request.focus_topic or '현재 대화의 중요한 문제'))
                if self.reference_provider:
                    examples=self.reference_provider('coach-dialogue',request.model_dump(mode='json'))[:2]
                    self.used_reference_ids.update(e['case_id'] for e in examples)
                    if examples:messages[0]['content']+='\n승인된 설명 방식 참고이며 현재 사실이 아닙니다: '+json.dumps(examples,ensure_ascii=False)
            for message in request.conversation:
                content = message.text if message.role == "assistant" else f"[{names.get(message.speaker_id, '구성원')} · {message.created_at.isoformat()}] {message.text}"
                messages.append({"role": message.role, "content": sanitize(content)})
            reply = self.client.complete_text(messages, {"max_tokens": 2000}, stats=stats).content
            def forbidden(text):
                qs = questions_in(text)
                repeated = any(q in answered for q in qs)
                extra_request = re.search(r"(?:하나만|한 가지(?:만|를)|마지막으로).*(?:확인|알려|질문|여쭤)|(?:알려줄래|알려주세요|어떤가요|인가요|할까요)", text)
                return len(qs) > 1 or repeated or (not allow and bool(qs or extra_request)) or any(word in text for word in ("완벽하게", "고민 금지", "이렇게 확정"))
            if forbidden(reply):
                emit("coach.policy.repair", {"reason": "Question budget, repeated question or plan-first policy"})
                messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": "방금 응답을 간결하게 고치세요. 이미 정해진 내용과 추천 실행안을 먼저 설명하고 긴 가정 목록은 제거하세요. 허용된 경우 실제 진행에 필요한 핵심 질문 하나만 남기고, 질문 예산을 소진했다면 이번에는 요약과 제안만 제공하세요."}]
                reply = self.client.complete_text(messages, {"max_tokens": 2000}, stats=stats).content
                if (not allow and questions_in(reply)) or len(questions_in(reply))>1 or (not allow and re.search(r"알려줄래|알려주세요|하나만.*확인|한 가지만.*확인", reply)) or any(word in reply for word in ("완벽하게", "고민 금지", "이렇게 확정")):
                    raise AIError("COACH_POLICY", "질문 제한을 지키지 못했습니다. 이 응답은 채팅에 추가하지 않았습니다.")
                # Preserve the current question budget after a successful correction.
            for member in request.members:
                reply = re.sub(r'(?<![A-Za-z0-9_-])'+re.escape(member.member_id)+r'(?![A-Za-z0-9_-])',member.nickname,reply.replace('('+member.member_id+')',''))
            qs = questions_in(reply)
            question = qs[0] if allow and qs else None
            state = CoachState(consecutive_questions=min(2, count + 1) if question else 0, last_question_topic=question, answered_topics=answered)
            return CoachReply(reply=reply, kind="PLAN_PROPOSAL" if proposed else "CLARIFICATION" if question else "ADVICE", question=None if dialogue else question,
                              assumptions=["추가로 제안한 작업 순서와 분량은 검토 전 초안입니다."] if proposed else [],
                              next_action="CONTINUE" if dialogue else "REVIEW_GOAL_DRAFT" if proposed else "CONTINUE", updated_coach_state=state)
        return self._run("coach-dialogue" if request.coaching_mode=='DIALOGUE' else 'coach', request, operation)

    def analyze_private_intake(self, request):
        def operation(stats):
            if request.scope != "PRIVATE": raise AIError("INPUT_INVALID", "개인 분석은 PRIVATE 요청이어야 합니다.")
            payload = {"owner_member_id": request.owner_member_id, "conversation": [m.model_dump(mode="json") for m in request.conversation], "reference_date": str(request.reference_date), "timezone": request.timezone}
            result = self._json("intake-analyze", payload, PrivateIntakeAnalysis,
                "본인만의 개인 문답에서 current_level, weekly_available_minutes, preferred_times, constraints를 추출합니다. "
                "fields의 각 필드는 value,origin(EXTRACTED/AI_SUGGESTED/UNRESOLVED),evidence_refs,reason,review_status(PENDING)를 갖습니다. "
                "없는 시간·수준·제약을 추정해서 채우지 말고 UNRESOLVED/null로 남기세요. 시간은 분으로 정규화합니다. "
                "공동 목표나 다른 구성원의 정보를 생성하지 마세요. evidence_ids에는 실제 사용자 메시지 ID만 넣습니다.", stats)
            known = {m.message_id for m in request.conversation if m.role == "user"}
            if result.owner_member_id != request.owner_member_id: raise AIError("SCHEMA_INVALID", "개인 분석 대상이 다릅니다.")
            if set(result.fields) - {"current_level", "weekly_available_minutes", "preferred_times", "constraints"}: raise AIError("SCHEMA_INVALID", "개인 분석에 공동 목표 필드가 포함됐습니다.")
            for field in result.fields.values():
                if not set(field.evidence_refs) <= known: raise AIError("SCHEMA_INVALID", "개인 분석 근거가 잘못됐습니다.")
                if field.origin == "EXTRACTED" and not field.evidence_refs: raise AIError("SCHEMA_INVALID", "개인 분석에 근거가 없습니다.")
                field.review_status = "PENDING"
            for name in ("current_level", "weekly_available_minutes", "preferred_times", "constraints"):
                result.fields.setdefault(name, DraftField(reason="개인 문답에서 확인되지 않았습니다."))
            result.evidence_ids = sorted(known)
            return result
        return self._run("intake-analyze", request, operation)

    def draft_goal_spec(self, request):
        def operation(stats):
            allowed = set(ConfirmedDecisions.model_fields) - {"confirmed"}
            if set(request.target_fields) - allowed:
                raise AIError("SCHEMA_INVALID", "보완할 카드 이름이 잘못됐습니다.")
            if request.target_fields and not request.previous_draft:
                raise AIError("SCHEMA_INVALID", "선택 카드 보완에는 이전 초안이 필요합니다.")
            if request.draft_mode=='SUGGEST' and not request.target_fields:
                raise AIError('SCHEMA_INVALID','AI 보완은 요청한 target_fields를 명시해야 합니다.')
            from .grounding import source_catalog,constrain_grounded
            payload=request.model_dump(mode='json');payload['source_catalog']=source_catalog(request.analysis)
            policy=('GROUNDED: source_catalog에 있는 값만 그대로 선택해 정리하세요. 없는 필드는 UNRESOLVED/null입니다. 날짜·완료 기준·인증·공개 범위를 임의로 보완하지 마세요. 충돌 후보를 임의 선택하지 마세요. '
                if request.draft_mode=='GROUNDED' else 'ASSISTED: source_catalog의 고유한 값은 유지하고, 없는 필수값은 활동에 맞는 AI_SUGGESTED 추천안으로 작성하세요. 날짜는 기준일과 마일스톤을 고려하고 기간을 선택한 이유를 적으세요. 공개 범위는 미정일 때 OBJECTIVE_ONLY를 추천하세요. 충돌은 후보를 보존하고 추천임을 표시하세요. 의미 있는 목표가 없으면 목표와 관련 필수값을 null로 남기고 설명을 요청하세요. 선택 정보 metric/frequency/checkins를 억지로 채우지 마세요. ' if request.draft_mode=='ASSISTED' else 'SUGGEST: target_fields에 명시된 항목만 새 AI_SUGGESTED 제안을 작성하고 이유와 가정을 표시하세요. 새 제안값에는 대화의 가짜 근거를 붙이지 마세요. 다른 필드는 이전 초안을 유지합니다. ')
            draft = self._json("draft", payload, GoalSpecDraft,
                "대화 분석 결과를 사람이 검토할 목표 명세 초안으로 작성하세요. fields 키는 " + ','.join(sorted(allowed)) + " 입니다. "
                "각 필드는 value,origin(EXTRACTED/AI_SUGGESTED/UNRESOLVED),evidence_refs,reason,review_status(PENDING)를 갖습니다. "
                "goal/expected_outcome/execution_method/verification_method는 문자열입니다. completion_criteria/checkins/custom_privacy_fields는 반드시 문자열 배열이며 그 안에 객체를 넣지 마세요. 체크인은 '화요일 · 사진 인증'처럼 문구로 씁니다. milestones만 객체 배열이고 roles는 member_id:역할 객체입니다. "
                "metric은 {name,target,unit,allocation:PER_MEMBER|GROUP_TOTAL} 또는 null, frequency_per_week는 정수 또는 null입니다. "
                "대화에 있는 값은 근거를 연결하세요. "
                "MISSING/CONFLICTED도 C 화면은 열립니다. 미정·충돌은 해당 카드 이유에 표시하세요. "
                "ASSISTED 또는 SUGGEST의 요청 날짜에 대해 reference_date를 기준으로 기간을 추천하고 이유를 적으세요. "
                "GROUNDED는 없는 공개 범위를 생성하지 않습니다. ASSISTED에서는 객관적 결과 공개를 미확인 추천으로 표시합니다. "
                "서로 다른 후보는 analysis에 보존하고, GROUNDED에서는 충돌값을 선택하지 마세요. "
                "목표조차 없으면 goal은 UNRESOLVED/null로 두고 필요한 설명을 reason에 적으세요. 불필요한 metric/frequency/checkins는 null 또는 빈 배열로 남깁니다. "
                "개발 완료일, 개인 중간 목표일, 최종 제출일은 다른 마일스톤이며 충돌로 합치지 마세요. "
                "start_date/end_date는 전체 목표 기간입니다. milestones는 {title,due_date,owner_member_id?}의 배열입니다. "
                "미정값은 null입니다. 개인 수준·가능 시간·제약은 포함하지 마세요. 이전 검토 수정사항을 변경하지 마세요. "+policy, stats)
            if set(draft.fields) - allowed: raise AIError("SCHEMA_INVALID", "목표 명세 초안에 허용되지 않은 필드가 있습니다.")
            known = set(request.analysis.evidence_ids) | set(request.shared_context.evidence_refs)
            candidates = [c for f in request.analysis.fields.values() for c in f.candidates]
            if request.draft_mode=='GROUNDED':draft=constrain_grounded(draft,request.analysis)
            if request.draft_mode=='ASSISTED':
                from .grounding import constrain_assisted
                draft=constrain_assisted(draft,request.analysis,request.previous_draft)
            for name, field in draft.fields.items():
                if request.draft_mode=='SUGGEST' and name in request.target_fields and field.value is not None:
                    field.origin='AI_SUGGESTED';field.evidence_refs=[];field.reason='사용자가 요청한 AI 보완안입니다. 대화에서 확정된 값이 아닙니다. '+field.reason
                if not set(field.evidence_refs) <= known: raise AIError("SCHEMA_INVALID", "목표 명세 초안 근거가 잘못됐습니다.")
                field.review_status = "PENDING"
                if field.value is None: field.origin = "UNRESOLVED"
                if field.origin == "EXTRACTED" and request.draft_mode=='SUGGEST':
                    supporting = [c for c in candidates if set(c.evidence_refs) & set(field.evidence_refs)]
                    serialized = json.dumps(field.value, ensure_ascii=False, sort_keys=True)
                    exact = any(serialized == json.dumps(c.value, ensure_ascii=False, sort_keys=True) or (isinstance(field.value, str) and field.value in json.dumps(c.value, ensure_ascii=False)) for c in supporting)
                    if not supporting or not exact or any(c.inferred or c.origin == "ASSISTANT_PROPOSAL" for c in supporting):
                        field.origin = "AI_SUGGESTED"
                        field.reason = "대화 근거를 바탕으로 AI가 정리·보완한 값입니다. 확인이 필요합니다. " + field.reason
            for name in allowed: draft.fields.setdefault(name, DraftField(reason="아직 확인되지 않았습니다."))
            if request.previous_draft:
                for name, field in request.previous_draft.fields.items():
                    if field.review_status in ("ACCEPTED", "EDITED") or (request.target_fields and name not in request.target_fields):
                        draft.fields[name] = field.model_copy(deep=True)
            draft.draft_id = str(uuid4())
            from .draft_validation import normalize_goal_draft
            draft = normalize_goal_draft(draft, request.analysis.members)
            emit("draft.review_required", {"pending": [k for k, v in draft.fields.items() if v.review_status == "PENDING"]})
            return draft
        return self._run("draft", request, operation)
