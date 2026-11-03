import json
import time
from collections import Counter
import pytest
from wemeet_ai.scenarios import all_scenarios,get_scenario,audit_scenarios,DATA_VERSION
from wemeet_ai.scenario_inputs import entry_request
from wemeet_ai.references import select_references
from wemeet_ai.adapters.scenario_store import ScenarioStore
from wemeet_ai.adapters.scenario_eval import EvaluationManager


def test_200_real_briefs_pairs_and_partition():
    cases=all_scenarios();report=audit_scenarios()
    assert report['passed'] and report['checked']==200
    assert Counter(c.kind for c in cases)=={'BASE':100,'VARIANT':100}
    assert Counter(c.split for c in cases)=={'REFERENCE':120,'DEV':40,'TEST':40}
    for case in cases:
        other=get_scenario(case.paired_case_id)
        assert case.family_id==other.family_id and case.split==other.split
        assert other.paired_case_id==case.case_id
        assert case.comparison['changed_fields']
        assert case.model_dump()!=other.model_dump()
        assert entry_request(case)


def test_all_examples_have_private_public_separation_and_uncalled_reference():
    for c in all_scenarios():
        public=c.group_input.model_dump_json()
        assert c.authoring_status=='SYNTHETIC_AUTHORED'
        assert 'AI 미실행' in c.reference_label
        assert c.expected['forbidden'] and c.expected['required_behaviors']
        for secret in c.expected['privacy_sentinels']:assert secret not in public
        assert c.expected['evidence_refs']


def test_reference_approval_requires_human_and_forbids_eval_leak(tmp_path):
    store=ScenarioStore(tmp_path/'test.sqlite3')
    assert store.reviews()=={}
    train=next(c for c in all_scenarios() if c.split=='REFERENCE')
    test=next(c for c in all_scenarios() if c.split=='TEST')
    with pytest.raises(ValueError):store.review(train.case_id,'APPROVED','','')
    with pytest.raises(ValueError):store.review(test.case_id,'APPROVED','test reviewer','')
    store.review(train.case_id,'APPROVED','fixture reviewer','test database only')
    assert store.counts()['reference_approved']==1
    assert store.counts()['live_attempted_cases']==0


def test_reference_lookup_max_two_no_family_no_answer_keys():
    pool=list(all_scenarios())
    current=pool[0]
    chosen=select_references(pool,'coach-proactive',{'text':' '.join(c.summary for c in pool)},current.family_id)
    assert len(chosen)==2
    for item in chosen:
        c=get_scenario(item['case_id'])
        assert c.split=='REFERENCE' and c.entry_module=='A' and c.family_id!=current.family_id
        assert 'expected_public_values' not in item and 'evidence_refs' not in item
    assert select_references([], 'analyze', {'text':'no pool'})==[]


def test_live_found_role_array_normalization_is_unambiguous():
    from wemeet_ai.draft_validation import normalize_decision_field
    assert normalize_decision_field('roles',[{'member_id':'a','role':'검색 구현'}],['a'])=={'a':'검색 구현'}
    with pytest.raises(ValueError):normalize_decision_field('roles',[{'member_id':'a','role':'A'},{'member_id':'a','role':'B'}],['a'])


def test_resume_cache_and_retry_only_failure(tmp_path,monkeypatch):
    from wemeet_ai.adapters import scenario_eval
    calls=[]
    def execute(case,core):
        calls.append(case.case_id)
        return {'status':'DONE','response':{'label':'UNIT_TEST_NOT_LIVE'},'checks':{'passed':True},'metrics':{'http_attempts':0,'total_tokens':0,'latency_ms':0}}
    monkeypatch.setattr(scenario_eval,'execute_scenario',execute)
    store=ScenarioStore(tmp_path/'test.sqlite3');manager=EvaluationManager(store)
    ids=[c.case_id for c in all_scenarios()[:6]]
    job=manager.create(ids,['baseline']);manager.start(job['id'])
    for t in manager.threads.values():t.join(15)
    first=store.job(job['id']);assert first['counts']['DONE']==5 and first['counts']['PENDING']==1 and first['status']=='PAUSED'
    manager.start(job['id'],automatic=True)
    for t in manager.threads.values():t.join(15)
    assert store.job(job['id'])['status']=='COMPLETED' and len(calls)==6
    same=manager.create(ids,['baseline']);manager.start(same['id'],automatic=True)
    for t in manager.threads.values():t.join(15)
    assert store.job(same['id'])['cached']==6 and len(calls)==6
    assert store.counts()['live_attempted_cases']==0 # fake tests never count as LIVE


def test_api_gallery_loading_never_calls_provider(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from wemeet_ai.adapters import web
    monkeypatch.setenv('WEMEET_DB_PATH',str(tmp_path/'api.sqlite3'));web.repository.cache_clear()
    with TestClient(web.app) as client:
        listing=client.get('/ai/v1/scenarios').json()
        assert listing['counts']['written']==200 and len(listing['items'])==20
        case=listing['items'][0]['case_id']
        assert client.get('/scenarios').status_code==200
        assert client.get('/ai/v1/scenarios/'+case+'/entry').status_code==200
        assert client.get('/ai/v1/scenarios/audit').json()['passed']
        assert client.get('/ai/v1/scenarios?kind=BASE&module=E').json()['total_filtered']==20
        assert client.get('/ai/v1/scenarios').json()['counts']['live_attempted_cases']==0
    web.repository.cache_clear()
