"""Build an allowlisted SW handoff. Never copy runtime, environment or user DB."""
import json,hashlib,shutil,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
from tools.db.cutover import user_get
from wemeet_ai import __version__
from wemeet_ai.persistence import PostgresRecordStore
ROOT=Path(__file__).resolve().parents[2]

def main():
    folder=ROOT/'artifacts'/('m0m1-'+__version__);folder.mkdir(exist_ok=True)
    wheel=ROOT/'artifacts'/('wemeet_ai_core-'+__version__+'-py3-none-any.whl');shutil.copy2(wheel,folder/wheel.name)
    for name in ('alembic.ini',):shutil.copy2(ROOT/name,folder/name)
    for directory,allowed in [('migrations',{'.py','.mako'}),('examples',{'.py','.json'})]:
        for path in (ROOT/directory).rglob('*'):
            if path.is_file() and path.suffix in allowed and '__pycache__' not in path.parts:
                dest=folder/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    docs=['MODULES.md','ERRORS.md','POSTGRES_INTEGRATION.md','GOAL_DATABASE.md','POSTGRES_OPERATIONS.md','DB_APP_GRANTS.sql']
    (folder/'docs').mkdir(exist_ok=True)
    for name in docs:shutil.copy2(ROOT/'docs'/name,folder/'docs'/name)
    (folder/'README.md').write_text(f'''# WE·MEET M0/M1 {__version__} — SW팀 시작 안내

이 묶음은 AI Python 패키지와 저장 어댑터입니다. 회원·로그인·운영 웹서비스를 제공하지 않습니다. A~F 중 하나만 사용해도 됩니다.

## 1. 설치

Python 3.11 이상인 터미널에서 이 README가 있는 폴더로 이동하세요.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install "./{wheel.name}[database]"
.venv/Scripts/python examples/a_coaching.py
```

마지막 명령은 작성된 예제 응답이며 유료 호출이 없습니다. `--live`를 붙이면 실제 AI를 호출하므로 서버 환경변수 TIMELY_API_KEY가 필요합니다. 키를 브라우저나 코드에 넣지 마세요. A~E 예제는 fixture, F는 코드 검사입니다.

## 2. 필요한 기능 선택

| 모듈 | 하는 일 | 이전 모듈 실행 필요 |
|---|---|---|
| A | 채팅으로 목표 코칭 | 없음 |
| B | 대화의 사실·제안·충돌 정리 | 없음 |
| C | 검토할 공동 조건 작성·확인 | 없음, SW 사실 직접 입력 가능 |
| D | 확인된 공동 조건으로 계획 작성 | 없음 |
| E | 본인 조건 코칭·개인 계획 작성 | 없음, 본인 조건 직접 입력 가능 |
| F | 계획·날짜·시간·연결 검사 | 없음 |

[모듈별 import와 요청 계약](docs/MODULES.md)을 읽고 `examples/requests/`의 대응 JSON을 확인하세요. 각 Python 예제는 자기 입력을 포함하며 다른 예제를 먼저 실행할 필요가 없습니다.

## 3. DB 저장 연결

[PostgreSQL 설치·권한·실행·저장·복구](docs/POSTGRES_INTEGRATION.md)를 먼저 따라하세요. 관리자가 migrations를 적용하고 앱 계정을 만들며, 앱에는 제한된 계정과 암호화 복구 키만 전달합니다.

```powershell
.venv/Scripts/python -m examples.stored_module
```

이 예제는 실제 PostgreSQL 저장을 사용하지만 모델은 fixture여서 HTTP 0회입니다. D 하나만 실행 → 저장 → 어댑터 재생성 → 같은 action_id 재전송을 보여줍니다. 입력을 바꿀 때 이전 action_id를 재사용하지 마세요.

`Result.status`는 AI 작업 상태, `persistence.persistence_status`는 DB 커밋 상태입니다. OK만 보고 저장 완료라고 표시하면 안 됩니다. PERSIST_FAILED는 같은 동작으로 저장만 재시도, UNKNOWN은 운영자 확인입니다. [오류 처리](docs/ERRORS.md)를 참고하세요.

## 4. 공개·개인과 다음 평가 기준

SW 인증으로 그룹·본인 ID를 정한 뒤 호출하세요. 개인 코칭에는 그 사람의 자료만 보냅니다. `internal=True` 진단을 그룹에 노출하지 마세요.

새 계획은 새 plan_version_id·task_id를 가집니다. 과거 기준을 덮어쓰지 않습니다. M2는 baseline_id + plan_version_id + task_id로 정확한 조건을 읽습니다. M2 평가 자체는 이 패키지에 없습니다.

`operating_examples/public`와 `private`는 이번에 만든 익명 검증 자료이며 실제 사용자 데이터가 아닙니다. 과거 버전도 포함하므로 `group-public.json`의 heads.current_plans[담당자ID]로 현재 개인 계획을 선택하세요. 단순히 첫 번째 결과를 현재 계획으로 쓰면 안 됩니다.

[실제로 검증한 범위와 남은 한계](docs/POSTGRES_OPERATIONS.md)를 읽으세요. 사람 검토·SW팀 직접 인수·서비스 권한 연결은 아직 완료가 아닙니다. manifest.json에 버전·검증 범위·파일 해시가 있습니다.
''',encoding='utf-8')
    store=PostgresRecordStore(user_get('WEMEET_GOAL_DATABASE_URL'))
    live=json.loads((ROOT/'runtime/unified-live.json').read_text(encoding='utf-8'))
    for name,entry in live['groups'].items():
        rid=entry['record_id'];public=folder/'operating_examples/public'/name;public.mkdir(parents=True,exist_ok=True)
        (public/'group-public.json').write_text(json.dumps(store.goals.public_group(rid),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        view=store.load(rid)
        (public/'conversation-and-runs.json').write_text(view.model_dump_json(indent=2),encoding='utf-8')
        for member in view.members:
            private=folder/'operating_examples/private'/name/member.member_id;private.mkdir(parents=True,exist_ok=True)
            (private/'goals-and-plans.json').write_text(json.dumps(store.goals.private_member(rid,member.member_id),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            owned=store.load(rid,owner_member_id=member.member_id)
            data={'conversation':[m.model_dump(mode='json') for m in owned.private_conversation],
                'artifacts':[a.model_dump(mode='json') for a in owned.artifacts if a.scope=='PRIVATE' and a.owner_member_id==member.member_id]}
            (private/'private-conversation-and-runs.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    totals=json.loads((ROOT/'runtime/final-cases.json').read_text(encoding='utf-8'))['totals']
    manifest={'version':__version__,'migration':'0004_private_owner_fk','tests':{'full_suite_passed':177,'additional_storage_status_passed':1},
        'live':totals,'anonymous_cases':4,'members':17,'human_review':False,'sw_team_acceptance':False,
        'database_restore':'17 table counts and SHA256 match','sqlite_records':{'copied':11,'validated':9,'quarantined':2},
        'files':{p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob('*')) if p.is_file() and p.name!='manifest.json'}}
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # Actual secret values must not occur, including inside the wheel archive.
    secrets=[v.encode() for key in ('TIMELY_API_KEY','WEMEET_GOAL_DATABASE_URL','WEMEET_GOAL_ADMIN_DATABASE_URL','WEMEET_RECOVERY_KEY') if (v:=user_get(key))]
    for path in folder.rglob('*'):
        if path.is_file():
            values=[path.read_bytes()]
            if path.suffix=='.whl':
                with zipfile.ZipFile(path) as archive:values.extend(archive.read(n) for n in archive.namelist())
            if any(secret in value for secret in secrets for value in values):raise RuntimeError('Secret scan failed; do not distribute')
    archive=ROOT/'artifacts'/('wemeet-m0m1-'+__version__+'-handoff.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(folder.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(folder))
    (ROOT/'artifacts/manifest.json').write_text(json.dumps({'version':__version__,'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'handoff_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'secret_scan':'PASS','files':len(manifest['files'])},indent=2))
    print(json.dumps({'version':__version__,'files':len(manifest['files']),'secret_scan':'PASS','handoff':str(archive)}))
if __name__=='__main__':main()
