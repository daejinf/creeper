import os
import pytest
from uuid import uuid4
from wemeet_coach.persistence.store import Store
from cryptography.fernet import Fernet


@pytest.fixture
def store(tmp_path):
    url = os.environ.get("WEMEET_COACH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("새 PostgreSQL 설정 필요")
    return Store(url, tmp_path / "recovery", Fernet.generate_key())


def members(n):
    return [
        {"member_id": "u" + str(i), "nickname": "검증구성원" + str(i)} for i in range(n)
    ]


def proposal(ms):
    from wemeet_coach.shared.contracts import GroupProposal

    return GroupProposal.model_validate(
        {
            "goal": {
                "title": "팀 발표 준비",
                "start_date": "2026-11-08",
                "end_date": "2026-11-22",
                "completion_criteria": ["발표 시연을 한 번 검토한다"],
                "execution_method": "역할별로 준비하고 합친다",
                "verification_method": "공유 문서",
                "roles": {m["member_id"]: "담당 결과 정리" for m in ms},
            },
            "tasks": [
                {
                    "task_id": str(uuid4()),
                    "owner_member_id": m["member_id"],
                    "title": "담당 결과물 초안",
                    "deliverable": "검토 문서",
                    "due_date": "2026-11-12",
                    "estimated_minutes": 20,
                    "completion_criteria": ["담당 결과 확인 가능"],
                }
                for m in ms
            ],
        }
    )


def approve(store, n=3):
    ms = members(n)
    g = store.create("검증 · " + str(n), ms, purpose="VALIDATION")
    gid = g["group_id"]
    body = proposal(ms).model_dump(mode="json")
    store.edit_proposal(gid, str(uuid4()), ms[0]["member_id"], body, g["revision"])
    pid = store.view(gid)["proposal"]["id"]
    for m in ms:
        store.vote(gid, pid, m["member_id"], True, "", "", str(uuid4()))
    return gid, pid, ms


@pytest.fixture(autouse=True)
def no_paid_calls_in_unit_tests(monkeypatch):
    import requests

    def blocked(*args, **kwargs):
        raise AssertionError(
            "Unit tests must inject a transport; real provider calls belong to evaluations"
        )

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
