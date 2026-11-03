import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_supported_public_imports_do_not_import_test_adapters():
    result=subprocess.run([sys.executable,'-c',
        "import sys,wemeet_ai,wemeet_ai.api as a; assert wemeet_ai.__version__=='0.10.1'; "
        "assert all(hasattr(a,k) for k in a.__all__); "
        "assert not any(k.startswith(('fastapi','sqlalchemy','wemeet_ai.adapters')) for k in sys.modules); print(a.PUBLIC_API_VERSION)"],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='1.0'


def test_sw_entire_flow_failure_recovery_and_private_separation(tmp_path):
    spec=importlib.util.spec_from_file_location('sw_acceptance_example',ROOT/'examples/sw_m0_m1_end_to_end.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.run(tmp_path)
    assert result['actual_http_requests']==0 and result['actual_tokens']==0
    assert result['validation_passed'] and result['m2_reference_valid']
    assert result['state_restored'] and result['duplicate_confirmation_stable']
    assert result['retry_member_ids']==['coding'] and result['successful_member_plan_preserved']
    assert result['public_private_separation'] and result['human_confirmation']=='SIMULATED_TUTORIAL_ONLY'
    public=(tmp_path/'group-public.json').read_text(encoding='utf-8')+' '.join(p.read_text(encoding='utf-8') for p in (tmp_path/'group').glob('*.json'))
    assert '가족 돌봄' not in public and '병원 방문' not in public
    assert (tmp_path/'private/design/handoff-private.json').exists()
    needs=json.loads((tmp_path/result['status_examples']['NEEDS_INPUT']).read_text(encoding='utf-8'))
    failed=json.loads((tmp_path/result['status_examples']['FAILED']).read_text(encoding='utf-8'))
    assert needs['response']['status']=='NEEDS_INPUT' and failed['response']['error']['retryable']
