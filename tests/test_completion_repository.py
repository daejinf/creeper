"""Real PostgreSQL tests for M2 completion persistence and service state."""
from types import SimpleNamespace
from uuid import uuid4
from datetime import timedelta

import pytest

sa = pytest.importorskip('sqlalchemy')
from wemeet_ai.m0 import AIError, Runtime
from wemeet_ai.persistence import CompletionError, CompletionRepository, GoalRepository
from wemeet_ai.persistence import schema as db
from wemeet_ai.persistence.errors import RevisionConflict
from wemeet_ai.shared.contracts import Plan, Task
from _data import sample


@pytest.fixture
def repositories():
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
            yield goals, CompletionRepository(connection=connection), connection
        finally:
            transaction.rollback()
    engine.dispose()


def seeded(repositories, *, extra_member=False):
    from wemeet_ai.shared.contracts import ConfirmedGoal
    goals, completion, _ = repositories
    goal = ConfirmedGoal.model_validate(sample('D'))
    members = [member.model_dump() for member in goal.members]
    if extra_member:
        members.append({'member_id': 'u3', 'nickname': '세 번째 구성원'})
    group_id = str(uuid4())
    goals.save_group(group_id, '완료 판정 그룹', members)
    shared_id = str(uuid4())
    goals.save_shared_goal(group_id, shared_id, goal, source_ref=shared_id)
    owner = goal.members[0].member_id
    member_goal_id = str(uuid4())
    goals.save_member_goal(
        group_id, owner, member_goal_id, shared_id,
        {'title': '참여 설계', 'deliverable': '진행안 한 장',
         'completion_criteria': ['참여 방식 선택 가능'], 'confirmed': True},
        {'current_level': '일부 경험', 'weekly_available_minutes': 60,
         'confirmed': True, 'constraints': [], 'private_notes': []},
        source_ref=member_goal_id,
    )
    task = Task(
        task_id=str(uuid4()), owner_member_id=owner, title='초안 정리',
        deliverable='진행안 한 장', completion_criteria=['참여 방식을 선택할 수 있다'],
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
    return dict(group_id=group_id, owner=owner, plan=plan, task=task, goal=goal, completion=completion)


def evidence():
    return [{
        'evidence_id': 'ev-1', 'original_name': 'result.png', 'mime_type': 'image/png',
        'size_bytes': 100, 'sha256': 'a' * 64, 'content_kind': 'IMAGE',
        'page_count': None, 'extracted_character_count': 0, 'image_count': 1,
    }]


def submit(case, action='submit-1', description='완료 결과를 제출합니다.', evidence_rows=None):
    return case['completion'].create_submission(
        case['group_id'], case['plan'].plan_version_id, case['task'].task_id,
        case['owner'], action, description, evidence_rows if evidence_rows is not None else evidence(),
        submission_id=str(uuid4()),
    )


class FixtureClient:
    model = 'fixture-model'
    base_url = 'fixture://m2'
    is_fixture = True

    def __init__(self, payload=None, *, failure=None, connection=None):
        self.payload = payload
        self.failure = failure
        self.connection = connection
        self.calls = 0

    def complete_json(self, messages, output_type, options=None, *, stats=None):
        self.calls += 1
        if self.connection is not None:
            assert self.connection.get_nested_transaction() is None
        if self.failure:
            raise AIError(self.failure, 'fixture failure')
        stats.transport_attempts += 1
        stats.actual_model = self.model
        return SimpleNamespace(parsed_output=output_type.model_validate(self.payload))


def review_payload(status, decision):
    return {
        'decision': decision,
        'summary': '기준별 판정입니다.',
        'criteria_results': [{
            'criterion_index': 0, 'criterion': '참여 방식을 선택할 수 있다',
            'status': status, 'reason': '제출 설명을 확인했습니다.',
            'evidence_refs': ([{'evidence_id': 'submission_description',
                                'location': '사용자 제출 설명', 'description': '제출 설명'}]
                              if status == 'MET' else []),
        }],
        'missing_items': [] if decision == 'PASS' else ['결과 화면'],
        'user_message': '판정 결과를 확인해주세요.',
    }


def test_submission_uses_baseline_scope_storage_and_idempotency(repositories):
    case = seeded(repositories)
    first = submit(case)
    assert first['reused'] is False and first['evidence'][0]['sha256'] == 'a' * 64
    assert 'storage_key' not in first['evidence']
    repeated = case['completion'].create_submission(
        case['group_id'], case['plan'].plan_version_id, case['task'].task_id,
        case['owner'], 'submit-1', '완료 결과를 제출합니다.', evidence(), submission_id=str(uuid4()),
    )
    assert repeated['submission_id'] == first['submission_id'] and repeated['reused'] is True
    with pytest.raises(RevisionConflict):
        case['completion'].create_submission(
            case['group_id'], case['plan'].plan_version_id, case['task'].task_id,
            case['owner'], 'submit-1', '다른 입력', evidence(), submission_id=str(uuid4()),
        )
    with pytest.raises(ValueError):
        case['completion'].create_submission(
            case['group_id'], case['plan'].plan_version_id, case['task'].task_id,
            'u2', 'wrong-owner', '다른 사용자 제출', [], submission_id=str(uuid4()),
        )
    with pytest.raises(ValueError, match='5000'):
        case['completion'].create_submission(
            case['group_id'], case['plan'].plan_version_id, case['task'].task_id,
            case['owner'], 'too-long', 'x' * 5001, [], submission_id=str(uuid4()),
        )
    with pytest.raises(sa.exc.DBAPIError):
        with case['completion'].transaction() as connection:
            connection.execute(db.task_submissions.insert().values(
                submission_id=str(uuid4()), group_id=case['group_id'],
                plan_version_id=case['plan'].plan_version_id, task_id=case['task'].task_id,
                member_id='u2', action_id='direct-wrong-owner', description='잘못된 소유자',
                input_hash='x' * 64,
            ))
    progress = case['completion'].get_task_progress(
        case['group_id'], case['plan'].plan_version_id, case['task'].task_id, case['owner'])
    assert progress['status'] == 'SUBMITTED' and progress['latest_submission_id'] == first['submission_id']
    tasks = case['completion'].list_member_tasks(case['group_id'], case['owner'])
    assert len(tasks) == 1 and tasks[0]['baseline_id'] and tasks[0]['status'] == 'SUBMITTED'


def test_ai_failure_is_saved_and_same_operation_never_recalls_model(repositories):
    case = seeded(repositories)
    submission = submit(case, evidence_rows=[])
    failing = FixtureClient(failure='UPSTREAM_ERROR', connection=repositories[2])
    first = case['completion'].review_submission(
        submission['submission_id'], 'review-fail', [], runtime=Runtime(failing))
    second = case['completion'].review_submission(
        submission['submission_id'], 'review-fail', [], runtime=Runtime(failing))
    assert first['status'] == 'REVIEW_FAILED' and first['error_code'] == 'UPSTREAM_ERROR'
    assert second['state'] == 'FAILED' and second['reused'] is True and failing.calls == 1

    client = FixtureClient(review_payload('UNKNOWN', 'NEEDS_INPUT'), connection=repositories[2])
    reviewed = case['completion'].review_submission(
        submission['submission_id'], 'review-retry', [], runtime=Runtime(client))
    repeated = case['completion'].review_submission(
        submission['submission_id'], 'review-retry', [], runtime=Runtime(client))
    assert reviewed['status'] == 'NEEDS_INPUT' and repeated['state'] == 'PERSISTED'
    assert client.calls == 1


def test_pass_completes_immediately_and_blocks_new_submission(repositories):
    case = seeded(repositories)
    submission = submit(case, evidence_rows=[])
    client = FixtureClient(review_payload('MET', 'PASS'))
    reviewed = case['completion'].review_submission(
        submission['submission_id'], 'review-pass', [], runtime=Runtime(client))
    assert reviewed['status'] == 'COMPLETED'
    progress = case['completion'].get_task_progress(
        case['group_id'], case['plan'].plan_version_id, case['task'].task_id, case['owner'])
    assert progress['status'] == 'COMPLETED' and progress['completed_at'] is not None
    with pytest.raises(CompletionError) as error:
        submit(case, action='submit-after-complete')
    assert error.value.code == 'TASK_ALREADY_COMPLETED'


def test_appeal_votes_majority_duplicate_rules_and_completion(repositories):
    case = seeded(repositories, extra_member=True)
    submission = submit(case, evidence_rows=[])
    client = FixtureClient(review_payload('UNKNOWN', 'NEEDS_INPUT'))
    reviewed = case['completion'].review_submission(
        submission['submission_id'], 'review-needs-input', [], runtime=Runtime(client))
    appeal = case['completion'].create_appeal(
        reviewed['review']['review_id'], submission['submission_id'], case['owner'],
        'appeal-1', '제출 설명으로 확인할 수 있습니다.')
    repeated = case['completion'].create_appeal(
        reviewed['review']['review_id'], submission['submission_id'], case['owner'],
        'appeal-1', '제출 설명으로 확인할 수 있습니다.')
    assert appeal['eligible_voters'] == 2 and appeal['required_votes'] == 2
    assert repeated['appeal_id'] == appeal['appeal_id'] and repeated['reused'] is True
    with pytest.raises(CompletionError) as self_vote:
        case['completion'].cast_vote(appeal['appeal_id'], case['group_id'], case['owner'], True, 'self')
    assert self_vote.value.code == 'SELF_VOTE_NOT_ALLOWED'
    with pytest.raises(CompletionError) as outsider:
        case['completion'].cast_vote(appeal['appeal_id'], case['group_id'], 'outsider', True, 'outsider')
    assert outsider.value.code == 'VOTER_NOT_IN_GROUP'
    first_vote = case['completion'].cast_vote(appeal['appeal_id'], case['group_id'], 'u2', True, 'vote-u2')
    assert first_vote['status'] == 'OPEN' and first_vote['approve_votes'] == 1
    same_vote = case['completion'].cast_vote(appeal['appeal_id'], case['group_id'], 'u2', True, 'vote-u2')
    assert same_vote['reused'] is True
    with pytest.raises(CompletionError) as duplicate:
        case['completion'].cast_vote(appeal['appeal_id'], case['group_id'], 'u2', False, 'vote-u2-again')
    assert duplicate.value.code == 'VOTE_ALREADY_CAST'
    completed = case['completion'].cast_vote(appeal['appeal_id'], case['group_id'], 'u3', True, 'vote-u3')
    assert completed['status'] == 'APPROVED' and completed['approve_votes'] == 2
    assert case['completion'].get_task_progress(
        case['group_id'], case['plan'].plan_version_id, case['task'].task_id, case['owner'])['status'] == 'COMPLETED'


def test_new_submission_supersedes_open_appeal_and_preserves_history(repositories):
    case = seeded(repositories)
    submission = submit(case, evidence_rows=[])
    reviewed = case['completion'].review_submission(
        submission['submission_id'], 'review-old', [],
        runtime=Runtime(FixtureClient(review_payload('UNKNOWN', 'NEEDS_INPUT'))))
    appeal = case['completion'].create_appeal(
        reviewed['review']['review_id'], submission['submission_id'], case['owner'],
        'appeal-old', '새 증빙도 제출하겠습니다.')
    newer = submit(case, action='submit-new', description='새로운 증빙을 제출합니다.', evidence_rows=[])
    with case['completion'].transaction() as connection:
        old = connection.execute(sa.select(db.completion_appeals).where(
            db.completion_appeals.c.appeal_id == appeal['appeal_id'])).mappings().one()
        assert old['status'] == 'SUPERSEDED'
        assert connection.scalar(sa.select(sa.func.count()).select_from(db.completion_reviews).where(
            db.completion_reviews.c.submission_id == submission['submission_id'])) == 1
    progress = case['completion'].get_task_progress(
        case['group_id'], case['plan'].plan_version_id, case['task'].task_id, case['owner'])
    assert progress['status'] == 'SUBMITTED' and progress['latest_submission_id'] == newer['submission_id']


def test_reviewing_state_blocks_replacement_submission(repositories):
    case = seeded(repositories)
    submission = submit(case, evidence_rows=[])
    assert case['completion'].claim_review(submission['submission_id'], 'in-flight')['claimed'] is True
    with pytest.raises(CompletionError) as error:
        submit(case, action='replacement-during-review', evidence_rows=[])
    assert error.value.code == 'REVIEW_NOT_READY'
