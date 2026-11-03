"""Versioned synthetic scenarios. No model calls, database or web dependency."""
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from .contracts import Contract, AnalysisRequest, MemberProfile, ConfirmedDecisions

DATA_VERSION='scenarios-2026-10-v1'
DOMAINS={'software':'SW·AI 프로젝트','design':'UI·UX 디자인','course':'수업 과제·팀 발표','exam':'시험·자격증 공부','language':'언어 학습·회화','career':'취업·포트폴리오','habit':'운동·생활 습관','reading':'독서·글쓰기','creative':'영상·음악 등 창작','community':'동아리·행사·봉사'}


class Scenario(Contract):
    schema_version:str='1.0'
    data_version:str=DATA_VERSION
    case_id:str
    family_id:str
    kind:Literal['BASE','VARIANT']
    paired_case_id:str
    split:Literal['REFERENCE','DEV','TEST']
    domain:str
    title:str
    summary:str
    purpose:str
    difficulty:Literal['BASIC','INTERMEDIATE','ADVANCED']
    entry_module:Literal['A','B','C','E','PLAN']
    tags:list[str]
    group_input:AnalysisRequest
    private_conversations:dict[str,list[dict]]
    profiles:list[MemberProfile]
    confirmed_fixture:ConfirmedDecisions
    expected:dict[str,Any]
    comparison:dict[str,Any]
    reference_answer:dict[str,Any]
    reference_label:str='작성된 기준안 · AI 미실행 · 사람 검토 전'
    authoring_status:str='SYNTHETIC_AUTHORED'

    @model_validator(mode='after')
    def valid_links(self):
        ids={m.member_id for m in self.group_input.members};messages={m.message_id for m in self.group_input.conversation}
        if self.domain not in DOMAINS:raise ValueError('Unknown domain')
        if {p.member_id for p in self.profiles}!=ids:raise ValueError('Profile coverage')
        if not set(self.private_conversations)<=ids:raise ValueError('Unknown private owner')
        for owner,turns in self.private_conversations.items():
            if any(t['role']=='user' and t['speaker_id']!=owner for t in turns):raise ValueError('Private scope violation')
            private_ids=[t['message_id'] for t in turns]
            if len(private_ids)!=len(set(private_ids)) or set(private_ids)&messages:raise ValueError('Duplicate message IDs')
        if not set(self.expected.get('evidence_refs',[]))<=messages:raise ValueError('Unknown expected evidence')
        return self


@lru_cache
def all_scenarios():
    path=Path(__file__).with_name('data')/'scenarios.v1.jsonl'
    return tuple(Scenario.model_validate_json(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip())


def get_scenario(case_id):
    for case in all_scenarios():
        if case.case_id==case_id:return case.model_copy(deep=True)
    raise KeyError(case_id)


def audit_scenarios(cases=None):
    cases=list(cases or all_scenarios());errors=[];warnings=[];ids={c.case_id for c in cases};families={}
    bases=[]
    for case in cases:
        families.setdefault(case.family_id,[]).append(case)
        if case.paired_case_id not in ids:errors.append(case.case_id+': missing pair')
        if case.kind=='BASE':bases.append(case)
        if not case.expected.get('forbidden') or not case.expected.get('required_behaviors'):errors.append(case.case_id+': missing quality criteria')
        if len(case.group_input.conversation)<6 and 'short_conversation' not in case.tags:errors.append(case.case_id+': unexplained short dialogue')
        for profile in case.profiles:
            for secret in profile.constraints:
                if secret and any(secret in m.text for m in case.group_input.conversation):errors.append(case.case_id+': private condition in public dialogue')
    for family,pair in families.items():
        if len(pair)!=2 or {c.kind for c in pair}!={'BASE','VARIANT'} or len({c.split for c in pair})!=1:errors.append(family+': invalid family/split')
        if len(pair)==2 and pair[0].comparison.get('changed_fields')!=pair[1].comparison.get('changed_fields'):errors.append(family+': inconsistent comparison label')
    if len(cases)!=200 or len(bases)!=100:errors.append('Expected 100 base + 100 variants')
    if any(sum(c.domain==domain for c in cases)!=20 for domain in DOMAINS):errors.append('Domain coverage must be 20 each')
    if Counter(c.split for c in cases)!=Counter({'REFERENCE':120,'DEV':40,'TEST':40}):errors.append('Invalid split coverage')
    # Base uniqueness ignores names and dates: outcome/workflow must be different stories.
    signatures=[json.dumps([c.expected['deliverable'],c.expected['workflow']],ensure_ascii=False) for c in bases]
    if len(set(signatures))!=100:errors.append('Duplicated base outcome/workflow')
    for left in bases:
        tokens=set(left.summary.split())
        for right in bases:
            if left.case_id>=right.case_id:continue
            other=set(right.summary.split());similarity=len(tokens&other)/max(1,len(tokens|other))
            if similarity>.85:warnings.append({'left':left.case_id,'right':right.case_id,'similarity':round(similarity,3)})
    digest=hashlib.sha256('\n'.join(c.model_dump_json() for c in cases).encode()).hexdigest()
    return {'data_version':DATA_VERSION,'written':len(cases),'base':len(bases),'variants':len(cases)-len(bases),'checked':len(cases),'domains':dict(Counter(c.domain for c in cases)),'splits':dict(Counter(c.split for c in cases)),'errors':errors,'similarity_warnings':warnings,'passed':not errors,'digest':digest,'human_reviewed':0,'live_executed':0}
