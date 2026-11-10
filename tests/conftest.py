import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import pytest

@pytest.fixture(autouse=True)
def postgres_web_storage(request,monkeypatch,tmp_path):
    """Web regressions use real PostgreSQL, isolated by record namespace."""
    if request.path.name not in {'test_records_api.py','test_kickoff.py','test_kickoff_flow.py','test_kickoff_plans.py','test_bench.py'}:return
    from tools.db.cutover import user_get
    from wemeet_ai.persistence import PostgresRecordStore,EncryptedRecoveryStore,ExecutionAdapter
    from tools.bench import records
    from cryptography.fernet import Fernet
    from uuid import uuid4
    url=user_get('WEMEET_GOAL_DATABASE_URL')
    if not url:pytest.skip('PostgreSQL setup required')
    monkeypatch.setenv('WEMEET_GOAL_DATABASE_URL',url)
    repo=PostgresRecordStore(url,namespace='test:'+str(uuid4()))
    adapter=ExecutionAdapter(repo,EncryptedRecoveryStore(tmp_path/'recovery',Fernet.generate_key()))
    monkeypatch.setattr(records,'repository',lambda:repo)
    monkeypatch.setattr(records,'store',lambda url:repo)
    monkeypatch.setattr(records,'execution_adapter',lambda:adapter)
    for name in ('kickoff','kickoff_flow','kickoff_plans'):
        module=__import__('tools.bench.'+name,fromlist=['repository'])
        monkeypatch.setattr(module,'repository',lambda:repo)
