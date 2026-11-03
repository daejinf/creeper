"""SW calls one facade with action + state. Default is an explicit zero-HTTP fixture."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from wemeet_ai import GoalAI
from wemeet_ai.analysis import merge_analysis
from wemeet_ai.contracts import *
from wemeet_ai.examples import design_conversation


class FixtureClient:
    model='SCRIPTED_FIXTURE_NOT_LIVE'
    base_url='offline://fixture'
    def __init__(self):self.calls=0
    def complete_text(self,messages,options=None,*,stats):
        self.calls+=1;stats.actual_model=self.model
        return SimpleNamespace(content='개발 완료는 11/10, 최종 제출은 11/15로 나누어 진행하세요. UI 시안을 먼저 전달하고 화면과 API를 연결하는 순서를 추천합니다.')
    def complete_json(self,messages,output_schema,options=None,*,stats):
        self.calls+=1;stats.actual_model=self.model
        source=design_conversation()
        if output_schema is ConversationAnalysis:
            value=ConversationAnalysis(summary='공동 데모 개발',fields={})
        elif output_schema is GoalSpecDraft:
            values=dict(goal='위밋 프로젝트 데모',expected_outcome='목표 입력부터 개인 계획 확인까지 시연',
                completion_criteria=['목표 입력부터 개인 계획 확인까지 시연된다.'],start_date='2026-10-28',end_date='2026-11-15',
                execution_method='UI 시안 공유 → 화면/API 연결 → 시연 점검',verification_method='사진 인증',privacy_scope='OBJECTIVE_ONLY')
            value=GoalSpecDraft(summary='검토용 추천 초안',fields={k:DraftField(value=v,origin='AI_SUGGESTED',reason='검토 전 추천') for k,v in values.items()})
        else:raise AssertionError('Unexpected fixture request: '+output_schema.__name__)
        return SimpleNamespace(parsed_output=output_schema.model_validate(value.model_dump()))


def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    client=FixtureClient();ai=GoalAI(client,execution_mode='REPLAY');records=[]
    def invoke(request):
        result=ai.advance_goal_setup(request)
        records.append({'request':request.model_dump(mode='json'),'response':result.model_dump(mode='json',exclude={'traces'})})
        (output/f'{len(records):02d}-{request.action.lower()}.json').write_text(json.dumps(records[-1],ensure_ascii=False,indent=2),encoding='utf-8')
        if result.status!='OK':raise RuntimeError(result.model_dump_json())
        return result.data
    source=design_conversation()
    coached=invoke(GoalSetupRequest(action='COACH',**source.model_dump()))
    reviewed=invoke(GoalSetupRequest(action='REVIEW',state=coached.state))
    # SW displays each review_item and waits for the human to review. Only this tutorial simulates the click.
    accepted=invoke(GoalSetupRequest(action='RESPOND',state=reviewed.state,actor_member_id='j',
        response=GoalSetupResponse(operation='ACCEPT',fields=list(reviewed.state.draft.fields))))
    confirmed=invoke(GoalSetupRequest(action='CONFIRM',state=accepted.state,actor_member_id='j'))
    assert confirmed.group_spec and client.calls==3
    report={'source':'SCRIPTED_FIXTURE / REPLAY','actual_http_requests':0,'fixture_requests':client.calls,
        'actions':[r['request']['action'] for r in records],'group_spec_ready':True,'service_approval':False,
        'sw_storage':['output.state','user confirmation history','group_spec.spec_id'],
        'notice':'사용자 수락은 튜토리얼에서만 모의 처리했습니다. 실제 서비스에서는 사용자 확인을 받아야 합니다.'}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='runs/goal-setup-quickstart');run(p.parse_args().output)
