"""Optional result cache. No database/framework dependency; private scope is explicit."""
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from typing import Protocol

VALIDATOR_VERSION = 'contracts-2026-10-v10.2-assisted-calendar'


class ResultCache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, value: dict, ttl: int = 86400) -> None: ...
    def lock(self, key: str): ...


class MemoryResultCache:
    def __init__(self):
        self.values = {}; self.locks = {}; self.guard = threading.Lock()

    def get(self, key):
        row = self.values.get(key)
        if row and row[0] > time.time(): return json.loads(json.dumps(row[1]))
        return None

    def put(self, key, value, ttl=86400):
        self.values[key] = (time.time()+ttl, json.loads(json.dumps(value)))

    @contextmanager
    def lock(self, key):
        with self.guard: lock = self.locks.setdefault(key, threading.RLock())
        with lock: yield


def fingerprint(task, payload, *, scope, owner, model, endpoint, credential, prompt_version, variant, references):
    payload = json.loads(json.dumps(payload, default=str))
    # Personal changes must not invalidate the public group plan or another member.
    if task == 'group-plan': payload.pop('member_profiles', None)
    if isinstance(payload.get('goal_spec'),dict): payload['goal_spec'].pop('member_profiles',None)
    data = [task,payload,scope,owner,model,endpoint,credential,prompt_version,variant,VALIDATOR_VERSION,references]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
