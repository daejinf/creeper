"""Explicit allow-list bundle. Never includes SQLite files, .env, traces or local sessions."""
import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

def package(root,wheel,output):
    root=Path(root);wheel=Path(wheel);version=wheel.name.split('-')[1];output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    sources={
        'README.md':root/'README.md','SW_INTEGRATION.md':root/'SW_INTEGRATION.md','SW_M2_CONTRACT.md':root/'SW_M2_CONTRACT.md',
        'SCENARIOS.md':root/'SCENARIOS.md','VERIFICATION.md':root/'VERIFICATION.md','PUBLIC_API.md':root/'PUBLIC_API.md','HISTORY.md':root/'HISTORY.md',
        'examples/sw_m0_m1_end_to_end.py':root/'examples/sw_m0_m1_end_to_end.py','examples/goal_setup_quickstart.py':root/'examples/goal_setup_quickstart.py','examples/sw_quickstart.py':root/'examples/sw_quickstart.py','examples/recheck_saved.py':root/'examples/recheck_saved.py',
        'contracts.json':root/'runs/contracts-v101.json','openapi.json':root/'runs/openapi-v101.json','public-api.json':root/'runs/public-api-v101.json','dist/'+wheel.name:wheel,
        'samples/OK.json':root/'runs/v101-samples/OK.json','samples/NEEDS_INPUT.json':root/'runs/v101-samples/NEEDS_INPUT.json','samples/FAILED.json':root/'runs/v101-samples/FAILED.json','samples/M2_REFERENCE.json':root/'runs/v101-samples/M2_REFERENCE.json',
    }
    files={name:path.read_bytes() for name,path in sources.items()}
    files['START_HERE.md']=('''# SW팀 시작 순서

이 묶음은 설치 가능한 Python AI 코어와 연결 예제입니다. 서비스 DB·권한·승인은 포함하지 않습니다.

1. Python 3.11 이상에서 가상환경을 만듭니다.
2. `python -m pip install dist/'''+wheel.name+'''`
3. `python examples/sw_m0_m1_end_to_end.py --output demo-output`
4. `demo-output/report.json`에서 actual_http_requests=0, validation_passed=true, m2_reference_valid=true, state_restored=true를 확인합니다.
5. README와 PUBLIC_API.md, SW_INTEGRATION.md를 따라 실제 사용자 확인·저장소를 연결합니다.
6. samples의 JSON은 작성된 fixture입니다. 실제 LIVE 결과·사람 검토 정답이 아닙니다.

웹 도구도 사용할 경우 `python -m pip install "dist/'''+wheel.name+'''[web]"`로 설치합니다.
일반 LIVE 사용은 허용합니다. 이 튜토리얼 자체는 모델 호출 없이 작성된 fixture로 계약을 검증합니다.
README의 `pip install -e .`와 pytest 명령은 전체 소스 저장소를 받은 개발자용입니다. 이 전달 묶음에서는 위 wheel 설치 명령을 사용합니다.
SQLite DB·개인 세션·키·원본 실행 로그는 이 묶음에 포함하지 않습니다.
''').encode('utf-8')
    secret=os.getenv('DEEPSEEK_API_KEY','')
    for name,blob in files.items():
        if secret and secret.encode() in blob:raise ValueError('Secret detected in bundle: '+name)
    manifest={'schema_version':'1.0','package_version':version,'public_api_version':'1.0','goal_setup_state_schema_version':'1.0','live_quality':'NEW_COACH_1_AND_STORED_PLAN_3_CODE_CHECKED_HUMAN_REVIEW_PENDING','sw_team_acceptance':'PENDING','verification':json.loads((root/'runs/v101-release-report.json').read_text(encoding='utf-8')),
        'contents':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()}}
    files['MANIFEST.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name,data)
    print(json.dumps({'bundle':str(output),'files':len(files),'contains_db_or_env':False}));return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='ai-core');p.add_argument('--wheel',required=True);p.add_argument('--output',default='ai-core/dist/WE-MEET_SW_HANDOFF_v0.10.1.zip');a=p.parse_args();package(a.root,a.wheel,a.output)
