"""A: actionable, reviewable coaching. No repository or service dependency."""
import json
from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL

from .client import AIError
from .contracts import CoachReply, CoachState, ProactiveCoachOutput
from .draft_validation import normalize_decision_field
from .interactions import wants_plan
from .tracing import emit


class ProactiveModules:
    def proactive_coach(self, request):
        def operation(stats):
            ids={m.message_id for m in request.conversation if m.role=='user'}|set(request.shared_context.evidence_refs)
            members=[m.member_id for m in request.members]
            def check(result):
                for proposal in result.proposals:
                    if not proposal.field_updates or not proposal.evidence_refs or not set(proposal.evidence_refs)<=ids:
                        raise AIError('PLAN_INVALID','추천안에는 반영할 값과 유효한 대화 근거가 필요합니다.')
                    valid={};proposal.field_errors={}
                    for key,value in proposal.field_updates.items():
                        try:valid[key]=normalize_decision_field(key,value,members)
                        except ValueError as exc:proposal.field_errors[key]=str(exc)
                    proposal.field_updates=valid
                    proposal.proposal_id=str(uuid4());proposal.status='PENDING'
            output=self._checked_json('coach-proactive',request.model_dump(mode='json'),ProactiveCoachOutput,
                '적극적인 공동 목표 코치입니다. 현재 문제를 진단하고 실행 가능한 추천안 1~3개, 선택 이유와 다음 행동을 제시하세요. '
                '사용자의 계획 요청에는 실행 초안을 먼저 제시하고, 정확도를 높이는 질문은 최대 하나를 question에 둡니다. 이미 답한 내용은 반복하지 마세요. '
                'observations에는 파악한 사실, missing_or_conflicting에는 실제 부족·충돌만 씁니다. proposals의 title/rationale은 구체적인 한국어입니다. '
                'field_updates는 공동 목표 명세의 goal, expected_outcome, completion_criteria, start_date, end_date, execution_method, verification_method, privacy_scope, checkins, milestones, roles, frequency_per_week, metric, custom_privacy_fields 중 필요한 항목만 씁니다. '
                'completion_criteria/checkins/custom_privacy_fields는 문자열 배열입니다. roles는 반드시 JSON 객체 {"실제_member_id":"담당 문구"}이며 배열이 아닙니다. milestones는 title/due_date/owner_member_id?의 배열입니다. 제안 날짜는 기준 날짜를 활용하되 제안임을 이유에 밝힙니다. '
                '개인 정보는 요구하지 마세요. AI 추천이나 개인 한 명의 수락을 그룹 전체 합의로 표현하지 마세요. 질문 순서나 숫자 형식을 강제하지 마세요. 사용자가 모름·보류라고 한 값은 사실로 만들지 마세요. '
                '실행안은 구체적으로 제시하되 공격적 표현이나 압박은 피하세요. evidence_refs는 실제 사용자 메시지 ID만 사용합니다.',stats,check)
            return CoachReply(reply=output.reply,kind='PLAN_PROPOSAL',question=output.question,
                observations=output.observations,missing_or_conflicting=output.missing_or_conflicting,
                proposals=output.proposals,next_actions=output.next_actions,
                updated_coach_state=CoachState(answered_topics=request.coach_state.answered_topics),next_action='CONTINUE')
        return self._run('coach-proactive',request,operation)

    def review_coach_proposal(self, request):
        def operation(_):
            proposal=request.proposal.model_copy(deep=True)
            if request.decision=='ACCEPTED' and not proposal.field_updates:raise AIError('INPUT_INVALID','반영할 유효한 항목이 없습니다. 초안에서 직접 확인해주세요.')
            if not proposal.proposal_id:raise AIError('INPUT_INVALID','추천안 ID가 필요합니다.')
            if proposal.status!='PENDING' and proposal.status!=request.decision:
                raise AIError('INPUT_INVALID','이미 처리한 추천안은 다시 생성하거나 별도로 수정해주세요.')
            for key,value in proposal.field_updates.items():
                normalize_decision_field(key,value)
            proposal.status=request.decision
            event_id=str(uuid5(NAMESPACE_URL,proposal.proposal_id+':'+request.reviewer.member_id+':'+request.decision))
            event={'event_id':event_id,'proposal':proposal.model_dump(mode='json'),'reviewer_member_id':request.reviewer.member_id,
                'reviewed_at':datetime.now(timezone.utc).isoformat(),'group_consensus':False}
            emit('coach.proposal.reviewed',event)
            return event
        return self._run('proposal-review',request,operation,uses_ai=False)
