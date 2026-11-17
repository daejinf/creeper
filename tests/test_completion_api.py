from types import SimpleNamespace
from uuid import uuid4
from datetime import timedelta
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sa = pytest.importorskip('sqlalchemy')
from tools.bench.server import app
from tools.bench import completion as completion_api
from wemeet_ai.m0 import AIError, Runtime
from wemeet_ai.persistence import CompletionRepository, GoalRepository
from wemeet_ai.shared.contracts import Plan, Task
from _data import sample


class FixtureClient:
    model = 'fixture-model'
    base_url = 'fixture://completion-api'
    is_fixture = True

    def __init__(self, payload=None, failure=None):
        self.payload = payload
        self.failure = failure
        self.calls = 0

    def complete_json(self, messages, output_type, options=None, *, stats=None):
        self.calls += 1
        if self.failure:
            raise AIError(self.failure, 'fixture failure')
        stats.transport_attempts += 1
        stats.actual_model = self.model
        return SimpleNamespace(parsed_output=output_type.model_validate(self.payload))


def review_payload(status, decision):
    return {
        'decision': decision, 'summary': 'API 판정 결과',
        'criteria_results': [{
            'criterion_index': 0, 'criterion': '결과 화면을 제출한다', 'status': status,
            'reason': '제출 설명과 증빙을 확인했습니다.',
            'evidence_refs': ([{'evidence_id': 'submission_description',
                                'location': '사용자 제출 설명', 'description': '제출 설명'}]
                              if status == 'MET' else []),
        }],
        'missing_items': [] if decision == 'PASS' else ['추가 결과 화면'],
        'user_message': '판정을 확인해주세요.',
    }


def png_bytes(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (24, 16), color).save(output, format='PNG')
    return output.getvalue()


@pytest.fixture
def api_case(monkeypatch, tmp_path):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            url = winreg.QueryValueEx(key, 'WEMEET_GOAL_DATABASE_URL')[0]
    except (ImportError, OSError):
        pytest.skip('Real PostgreSQL setup not available')
    engine = sa.create_engine(url, hide_parameters=True, connect_args={'connect_timeout': 3})
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            goals = GoalRepository(connection=connection)
            repository = CompletionRepository(connection=connection)
            from wemeet_ai.shared.contracts import ConfirmedGoal
            goal = ConfirmedGoal.model_validate(sample('D'))
            group_id = str(uuid4())
            members = [member.model_dump() for member in goal.members]
            members.append({'member_id': 'u3', 'nickname': '세 번째 구성원'})
            goals.save_group(group_id, 'API 완료 판정', members)
            shared_id = str(uuid4())
            goals.save_shared_goal(group_id, shared_id, goal, source_ref=shared_id)
            owner = goal.members[0].member_id
            member_goal_id = str(uuid4())
            goals.save_member_goal(
                group_id, owner, member_goal_id, shared_id,
                {'title': '결과 정리', 'deliverable': '결과 화면',
                 'completion_criteria': ['결과 화면을 제출한다'], 'confirmed': True},
                {'current_level': '경험 있음', 'weekly_available_minutes': 60,
                 'confirmed': True, 'constraints': [], 'private_notes': []},
                source_ref=member_goal_id,
            )
            task = Task(
                task_id=str(uuid4()), owner_member_id=owner, title='결과 제출',
                deliverable='결과 화면', completion_criteria=['결과 화면을 제출한다'],
                due_date=goal.start_date + timedelta(days=2), estimated_minutes=20,
            )
            plan = Plan(
                plan_version_id=str(uuid4()), goal=goal.goal, start_date=goal.start_date,
                end_date=goal.end_date, owner_member_id=owner, tasks=[task],
            )
            goals.save_plan(group_id, shared_id, plan, source_ref=plan.plan_version_id,
                            member_goal_version_id=member_goal_id)
            goals.set_heads(group_id, 1, shared_id, {owner: member_goal_id}, {owner: plan.plan_version_id})
            goals.confirm_baseline(group_id, plan.plan_version_id, owner)
            monkeypatch.setattr(completion_api, 'repository', lambda: repository)
            monkeypatch.setattr(completion_api, 'submission_storage_root', lambda: tmp_path / 'submissions')
            with TestClient(app) as client:
                yield SimpleNamespace(client=client, repository=repository, group_id=group_id,
                                      owner=owner, plan=plan, task=task, storage=tmp_path / 'submissions')
        finally:
            transaction.rollback()
    engine.dispose()


def submit_url(case):
    return (f'/api/kickoff/groups/{case.group_id}/members/{case.owner}/plans/'
            f'{case.plan.plan_version_id}/tasks/{case.task.task_id}/submissions')


def submit_file(case, action='submit-1', description='완료 결과입니다.', content=None):
    return case.client.post(
        submit_url(case), data={'action_id': action, 'description': description},
        files=[('files[]', ('proof.png', content if content is not None else png_bytes(), 'image/png'))],
    )


def test_file_submit_review_and_refresh_restore(api_case, monkeypatch):
    initial = api_case.client.get(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/completion/tasks')
    assert initial.status_code == 200 and initial.json()['tasks'][0]['status'] == 'TODO'
    submitted = submit_file(api_case)
    assert submitted.status_code == 200
    submission_id = submitted.json()['submission_id']
    assert submitted.json()['status'] == 'SUBMITTED' and 'storage_key' not in str(submitted.json())
    stored = api_case.storage / api_case.group_id / submission_id
    assert stored.is_dir() and len(list(stored.glob('*.bin'))) == 1

    repeated = submit_file(api_case)
    assert repeated.status_code == 200 and repeated.json()['reused'] is True
    assert repeated.json()['submission_id'] == submission_id
    assert len([path for path in (api_case.storage / api_case.group_id).iterdir() if path.is_dir()]) == 1

    fixture = FixtureClient(review_payload('MET', 'PASS'))
    monkeypatch.setattr(completion_api, 'runtime_for', lambda group_id: Runtime(fixture))
    reviewed = api_case.client.post(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/submissions/{submission_id}/review',
        json={'action_id': 'review-1'},
    )
    assert reviewed.status_code == 200 and reviewed.json()['status'] == 'COMPLETED'
    restored = initial = api_case.client.get(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/completion/tasks').json()['tasks'][0]
    assert restored['status'] == 'COMPLETED'
    assert restored['latest_submission']['submission_id'] == submission_id
    assert restored['latest_review']['decision'] == 'PASS'
    blocked = submit_file(api_case, action='submit-after-complete', description='완료 뒤 재제출')
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'TASK_ALREADY_COMPLETED'


def test_needs_input_appeal_vote_and_state_restore(api_case, monkeypatch):
    submitted = submit_file(api_case, action='submit-appeal').json()
    fixture = FixtureClient(review_payload('UNKNOWN', 'NEEDS_INPUT'))
    monkeypatch.setattr(completion_api, 'runtime_for', lambda group_id: Runtime(fixture))
    reviewed = api_case.client.post(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/submissions/{submitted["submission_id"]}/review',
        json={'action_id': 'review-needs-input'},
    ).json()
    review_id = reviewed['review']['review_id']
    appealed = api_case.client.post(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/reviews/{review_id}/appeals',
        json={'action_id': 'appeal-1', 'reason': '제출 자료로 확인 가능합니다.'},
    )
    assert appealed.status_code == 200 and appealed.json()['appeal']['required_votes'] == 2
    appeal_id = appealed.json()['appeal']['appeal_id']
    self_vote = api_case.client.post(
        f'/api/kickoff/groups/{api_case.group_id}/appeals/{appeal_id}/votes',
        json={'action_id': 'self', 'voter_member_id': api_case.owner, 'approve': True},
    )
    assert self_vote.status_code == 403 and self_vote.json()['error']['code'] == 'SELF_VOTE_NOT_ALLOWED'
    vote_url = f'/api/kickoff/groups/{api_case.group_id}/appeals/{appeal_id}/votes'
    first = api_case.client.post(
        vote_url, json={'action_id': 'vote-u2', 'voter_member_id': 'u2', 'approve': True})
    assert first.status_code == 200 and first.json()['status'] == 'APPEALED'
    assert first.json()['appeal']['approve_votes'] == 1
    duplicate = api_case.client.post(
        vote_url, json={'action_id': 'vote-u2-again', 'voter_member_id': 'u2', 'approve': False})
    assert duplicate.status_code == 409
    assert duplicate.json()['error']['code'] == 'VOTE_ALREADY_CAST'
    result = api_case.client.post(
        vote_url, json={'action_id': 'vote-u3', 'voter_member_id': 'u3', 'approve': True})
    assert result.json()['status'] == 'COMPLETED'
    restored = api_case.client.get(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/completion/tasks').json()['tasks'][0]
    assert restored['status'] == 'COMPLETED'
    assert restored['latest_appeal']['status'] == 'APPROVED'
    assert restored['latest_appeal']['approve_votes'] == 2


def test_ai_failure_file_error_and_cross_member_scope(api_case, monkeypatch):
    bad = submit_file(api_case, action='bad-file', content=b'not png')
    assert bad.status_code == 422 and bad.json()['error']['code'] == 'CORRUPT_IMAGE'
    wrong_scope_url = (f'/api/kickoff/groups/{api_case.group_id}/members/u2/plans/'
                       f'{api_case.plan.plan_version_id}/tasks/{api_case.task.task_id}/submissions')
    wrong_scope = api_case.client.post(
        wrong_scope_url, data={'action_id': 'wrong-owner-submit', 'description': '잘못된 소유자'},
        files=[('files[]', ('proof.png', png_bytes(), 'image/png'))],
    )
    assert wrong_scope.status_code == 403
    assert wrong_scope.json()['error']['code'] == 'TASK_OWNER_MISMATCH'
    group_storage = api_case.storage / api_case.group_id
    assert not group_storage.exists() or not list(group_storage.iterdir())
    submitted = submit_file(api_case, action='submit-failure').json()
    fixture = FixtureClient(failure='UPSTREAM_ERROR')
    monkeypatch.setattr(completion_api, 'runtime_for', lambda group_id: Runtime(fixture))
    url = (f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/submissions/'
           f'{submitted["submission_id"]}/review')
    invalid = api_case.client.post(url, json={})
    assert invalid.status_code == 422 and invalid.json()['error']['code'] == 'INPUT_INVALID'
    failed = api_case.client.post(url, json={'action_id': 'review-failure'})
    repeated = api_case.client.post(url, json={'action_id': 'review-failure'})
    assert failed.status_code == 502 and failed.json()['status'] == 'REVIEW_FAILED'
    assert repeated.status_code == 502 and fixture.calls == 1
    cross = api_case.client.post(
        f'/api/kickoff/groups/{api_case.group_id}/members/u2/submissions/{submitted["submission_id"]}/review',
        json={'action_id': 'cross-review'},
    )
    assert cross.status_code == 403 and cross.json()['error']['code'] == 'TASK_OWNER_MISMATCH'
    restored = api_case.client.get(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/completion/tasks').json()['tasks'][0]
    assert restored['status'] == 'REVIEW_FAILED' and restored['review_error_code'] == 'UPSTREAM_ERROR'
    success = FixtureClient(review_payload('MET', 'PASS'))
    monkeypatch.setattr(completion_api, 'runtime_for', lambda group_id: Runtime(success))
    retried = api_case.client.post(url, json={'action_id': 'review-retry'})
    assert retried.status_code == 200 and retried.json()['status'] == 'COMPLETED'
    assert success.calls == 1
    restored = api_case.client.get(
        f'/api/kickoff/groups/{api_case.group_id}/members/{api_case.owner}/completion/tasks').json()['tasks'][0]
    assert restored['status'] == 'COMPLETED'
    assert restored['latest_review']['decision'] == 'PASS'
