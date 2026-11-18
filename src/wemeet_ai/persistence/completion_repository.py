"""PostgreSQL persistence and service flow for M2 completion review."""
from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import schema as db
from .errors import RevisionConflict
from .repository import GoalRepository, digest
from ..m2 import (
    CompletionReview,
    CompletionReviewRequest,
    EvidenceMetadata,
    PreparedEvidence,
    SubmissionSnapshot,
    TaskSnapshot,
    review_completion,
)


class CompletionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CompletionRepository:
    def __init__(self, url=None, *, connection=None, goal_repository: GoalRepository | None = None):
        self.goals = goal_repository or GoalRepository(url, connection=connection)

    @contextmanager
    def transaction(self):
        with self.goals.transaction() as connection:
            yield connection

    @staticmethod
    def _action_lock(connection, namespace, group_id, action_id):
        connection.execute(sa.text(
            'SELECT pg_advisory_xact_lock(hashtextextended(:action_key, 0))'
        ), {'action_key': f'{namespace}:{group_id}:{action_id}'})

    @staticmethod
    def _progress_key(row):
        return (
            db.task_progress.c.group_id == row['group_id'],
            db.task_progress.c.plan_version_id == row['plan_version_id'],
            db.task_progress.c.task_id == row['task_id'],
            db.task_progress.c.member_id == row['member_id'],
        )

    def _evaluation_context(self, group_id, plan_version_id, task_id, member_id):
        try:
            return self.goals.evaluation_context(
                group_id, plan_version_id, task_id, owner_member_id=member_id)
        except ValueError as exc:
            message = str(exc)
            if '구성원 범위' in message:
                raise CompletionError('TASK_OWNER_MISMATCH', '해당 구성원의 과제가 아닙니다.') from exc
            if '평가 기준' in message:
                raise CompletionError('BASELINE_NOT_READY', message) from exc
            if 'task_id' in message or 'plan_version_id' in message:
                raise CompletionError('TASK_NOT_FOUND', message) from exc
            raise

    def _submission(self, connection, submission_id):
        row = connection.execute(sa.select(db.task_submissions).where(
            db.task_submissions.c.submission_id == submission_id
        )).mappings().first()
        if not row:
            raise CompletionError('SUBMISSION_NOT_FOUND', '제출을 찾을 수 없습니다.')
        output = dict(row)
        evidence = connection.execute(sa.select(db.submission_evidence).where(
            db.submission_evidence.c.submission_id == submission_id
        ).order_by(db.submission_evidence.c.evidence_id)).mappings()
        output['evidence'] = [
            {key: value for key, value in item.items() if key not in {'storage_key', 'extraction_status', 'created_at', 'submission_id'}}
            for item in evidence
        ]
        return output

    def get_submission(self, submission_id):
        with self.transaction() as connection:
            return self._submission(connection, submission_id)

    def create_submission(
        self, group_id, plan_version_id, task_id, member_id, action_id, description,
        evidence: list[EvidenceMetadata | dict], *, submission_id=None, finalize_storage=None,
    ):
        if not action_id:
            raise ValueError('action_id is required')
        if not isinstance(description, str) or not description.strip():
            raise ValueError('description is required')
        if len(description) > 5000:
            raise ValueError('description must be at most 5000 characters')
        items = [EvidenceMetadata.model_validate(item) for item in evidence]
        # Required boundary: baseline, exact task, and owner are checked by the existing repository.
        self._evaluation_context(group_id, plan_version_id, task_id, member_id)
        signature = digest([
            group_id, plan_version_id, task_id, member_id, description.strip(),
            sorted((item.model_dump(mode='json', exclude={'evidence_id'}) for item in items),
                   key=lambda item: (item['sha256'], item['original_name'], item['mime_type'])),
        ])
        submission_id = submission_id or str(uuid4())
        with self.transaction() as connection:
            self._action_lock(connection, 'submission', group_id, action_id)
            old = connection.execute(sa.select(db.task_submissions).where(
                db.task_submissions.c.group_id == group_id,
                db.task_submissions.c.action_id == action_id,
            )).mappings().first()
            if old:
                if old['input_hash'] != signature:
                    raise RevisionConflict('같은 action_id의 제출 입력이 다릅니다.')
                return self._submission(connection, old['submission_id']) | {'reused': True}
            progress = connection.execute(sa.select(db.task_progress).where(
                db.task_progress.c.group_id == group_id,
                db.task_progress.c.plan_version_id == plan_version_id,
                db.task_progress.c.task_id == task_id,
                db.task_progress.c.member_id == member_id,
            ).with_for_update()).mappings().first()
            if progress and progress['status'] == 'COMPLETED':
                raise CompletionError('TASK_ALREADY_COMPLETED', '완료된 과제에는 다시 제출할 수 없습니다.')
            if progress and progress['status'] not in {'REVIEW_NEEDED','NEEDS_INPUT','REVIEW_FAILED','APPEALED'}:
                raise CompletionError('REVIEW_NOT_READY', '현재 제출의 검토가 끝난 뒤 새 증빙을 제출해주세요.')
            submission = dict(
                submission_id=submission_id, group_id=group_id, plan_version_id=plan_version_id,
                task_id=task_id, member_id=member_id, action_id=action_id,
                description=description.strip(), input_hash=signature,
            )
            connection.execute(db.task_submissions.insert().values(**submission))
            for item in items:
                connection.execute(db.submission_evidence.insert().values(
                    submission_id=submission_id,
                    storage_key=f'{group_id}/{submission_id}/{item.evidence_id}.bin',
                    extraction_status='READY', **item.model_dump(),
                ))
            if finalize_storage is not None:
                finalize_storage()
            previous_submissions = sa.select(db.task_submissions.c.submission_id).where(
                db.task_submissions.c.group_id == group_id,
                db.task_submissions.c.plan_version_id == plan_version_id,
                db.task_submissions.c.task_id == task_id,
                db.task_submissions.c.member_id == member_id,
            )
            connection.execute(db.completion_appeals.update().where(
                db.completion_appeals.c.submission_id.in_(previous_submissions),
                db.completion_appeals.c.status == 'OPEN',
            ).values(status='SUPERSEDED', resolved_at=sa.func.now()))
            progress_values = dict(
                group_id=group_id, plan_version_id=plan_version_id, task_id=task_id, member_id=member_id,
                status='SUBMITTED', latest_submission_id=submission_id, latest_review_id=None,
                latest_appeal_id=None, latest_review_operation_id=None, review_input_hash=None,
                review_error_code=None, review_error_message=None, completed_at=None, revision=0,
            )
            connection.execute(insert(db.task_progress).values(**progress_values).on_conflict_do_update(
                index_elements=['group_id','plan_version_id','task_id','member_id'],
                set_={key: value for key, value in progress_values.items() if key not in {'group_id','plan_version_id','task_id','member_id','revision'}} |
                     {'revision': db.task_progress.c.revision + 1, 'updated_at': sa.func.now()},
            ))
            return self._submission(connection, submission_id) | {'reused': False}

    def get_task_progress(self, group_id, plan_version_id, task_id, member_id):
        with self.transaction() as connection:
            row = connection.execute(sa.select(db.task_progress).where(
                db.task_progress.c.group_id == group_id,
                db.task_progress.c.plan_version_id == plan_version_id,
                db.task_progress.c.task_id == task_id,
                db.task_progress.c.member_id == member_id,
            )).mappings().first()
            if row:
                return dict(row)
            return dict(group_id=group_id, plan_version_id=plan_version_id, task_id=task_id,
                        member_id=member_id, status='TODO', revision=0)

    def list_member_tasks(self, group_id, member_id):
        with self.transaction() as connection:
            if not connection.execute(sa.select(db.members.c.member_id).where(
                db.members.c.group_id == group_id, db.members.c.member_id == member_id
            )).first():
                raise CompletionError('TASK_OWNER_MISMATCH', '해당 그룹 구성원이 아닙니다.')
            rows = connection.execute(sa.select(
                db.plans.c.plan_version_id, db.baselines.c.baseline_id, db.tasks,
                db.task_progress.c.status, db.task_progress.c.latest_submission_id,
                db.task_progress.c.latest_review_id, db.task_progress.c.latest_appeal_id,
                db.task_progress.c.review_error_code, db.task_progress.c.review_error_message,
                db.task_progress.c.completed_at, db.task_progress.c.revision,
            ).join(db.baselines, db.baselines.c.plan_version_id == db.plans.c.plan_version_id)
              .join(db.tasks, db.tasks.c.plan_version_id == db.plans.c.plan_version_id)
              .outerjoin(db.task_progress, sa.and_(
                  db.task_progress.c.group_id == db.plans.c.group_id,
                  db.task_progress.c.plan_version_id == db.tasks.c.plan_version_id,
                  db.task_progress.c.task_id == db.tasks.c.task_id,
                  db.task_progress.c.member_id == member_id,
              )).where(db.plans.c.group_id == group_id, db.plans.c.owner_member_id == member_id)
              .order_by(db.tasks.c.due_date, db.tasks.c.task_id)).mappings()
            output = []
            for row in rows:
                item = dict(row) | {'status': row['status'] or 'TODO', 'revision': row['revision'] or 0}
                item['latest_submission'] = None
                item['latest_review'] = None
                item['latest_appeal'] = None
                if row['latest_submission_id']:
                    submission = self._submission(connection, row['latest_submission_id'])
                    item['latest_submission'] = {key: value for key, value in submission.items()
                                                 if key not in {'input_hash','action_id'}}
                if row['latest_review_id']:
                    review = connection.execute(sa.select(db.completion_reviews).where(
                        db.completion_reviews.c.review_id == row['latest_review_id']
                    )).mappings().one()
                    item['latest_review'] = dict(review)
                if row['latest_appeal_id']:
                    appeal = self._appeal_state(connection, row['latest_appeal_id'])
                    item['latest_appeal'] = {key: value for key, value in appeal.items()
                                             if key not in {'input_hash','action_id'}}
                output.append(item)
            return output

    def get_review(self, review_id):
        with self.transaction() as connection:
            row = connection.execute(sa.select(db.completion_reviews).where(
                db.completion_reviews.c.review_id == review_id
            )).mappings().first()
            if not row:
                raise CompletionError('REVIEW_NOT_READY', '판정 기록을 찾을 수 없습니다.')
            return dict(row)

    def claim_review(self, submission_id, operation_id):
        if not operation_id:
            raise ValueError('operation_id is required')
        signature = digest(['M2.review', submission_id])
        with self.transaction() as connection:
            submission = self._submission(connection, submission_id)
            existing = connection.execute(sa.select(db.completion_reviews).where(
                db.completion_reviews.c.operation_id == operation_id
            )).mappings().first()
            if existing:
                if existing['submission_id'] != submission_id:
                    raise RevisionConflict('같은 operation_id가 다른 제출에 사용됐습니다.')
                return {'claimed': False, 'state': 'PERSISTED', 'review': dict(existing), 'reused': True}
            progress = connection.execute(sa.select(db.task_progress).where(
                *self._progress_key(submission)
            ).with_for_update()).mappings().one()
            if progress['latest_review_operation_id'] == operation_id:
                if progress['review_input_hash'] != signature:
                    raise RevisionConflict('같은 operation_id의 판정 입력이 다릅니다.')
                if progress['status'] == 'REVIEW_FAILED':
                    return {'claimed': False, 'state': 'FAILED', 'error_code': progress['review_error_code'], 'reused': True}
                return {'claimed': False, 'state': 'RUNNING', 'reused': True}
            if progress['latest_submission_id'] != submission_id:
                raise CompletionError('REVIEW_NOT_READY', '현재 제출만 검토할 수 있습니다.')
            if progress['status'] == 'COMPLETED':
                raise CompletionError('TASK_ALREADY_COMPLETED', '완료된 과제입니다.')
            if progress['status'] not in {'SUBMITTED','REVIEW_FAILED','REVIEW_NEEDED','NEEDS_INPUT'}:
                raise CompletionError('REVIEW_NOT_READY', '현재 상태에서는 AI 판정을 시작할 수 없습니다.')
            connection.execute(db.task_progress.update().where(*self._progress_key(submission)).values(
                status='REVIEWING', latest_review_operation_id=operation_id, review_input_hash=signature,
                review_error_code=None, review_error_message=None, updated_at=sa.func.now(),
                revision=db.task_progress.c.revision + 1,
            ))
            return {'claimed': True, 'state': 'RUNNING', 'reused': False}

    def save_review(self, submission_id, operation_id, review: CompletionReview | dict, *, meta=None):
        review = CompletionReview.model_validate(review)
        meta_data = meta.model_dump(mode='json') if hasattr(meta, 'model_dump') else (meta or {})
        with self.transaction() as connection:
            existing = connection.execute(sa.select(db.completion_reviews).where(
                db.completion_reviews.c.operation_id == operation_id
            )).mappings().first()
            if existing:
                if existing['submission_id'] != submission_id:
                    raise RevisionConflict('같은 operation_id가 다른 제출에 사용됐습니다.')
                return {'status': 'COMPLETED' if existing['decision'] == 'PASS' else existing['decision'],
                        'review': dict(existing), 'reused': True}
            submission = self._submission(connection, submission_id)
            progress = connection.execute(sa.select(db.task_progress).where(
                *self._progress_key(submission)
            ).with_for_update()).mappings().one()
            if progress['latest_review_operation_id'] != operation_id or progress['status'] != 'REVIEWING':
                raise RevisionConflict('AI 판정 실행 소유권이 현재 진행 상태와 다릅니다.')
            review_id = str(uuid4())
            values = dict(
                review_id=review_id, submission_id=submission_id, operation_id=operation_id,
                decision=review.decision, summary=review.summary,
                criteria_results=[item.model_dump(mode='json') for item in review.criteria_results],
                missing_items=review.missing_items, user_message=review.user_message,
                model=meta_data.get('model'), prompt_version=meta_data.get('prompt_version'),
                run_id=meta_data.get('run_id'),
            )
            connection.execute(db.completion_reviews.insert().values(**values))
            status = 'COMPLETED' if review.decision == 'PASS' else review.decision
            connection.execute(db.task_progress.update().where(*self._progress_key(submission)).values(
                status=status, latest_review_id=review_id, latest_appeal_id=None,
                completed_at=sa.func.now() if status == 'COMPLETED' else None,
                review_error_code=None, review_error_message=None, updated_at=sa.func.now(),
                revision=db.task_progress.c.revision + 1,
            ))
            return {'status': status, 'review': values, 'reused': False}

    def save_review_failure(self, submission_id, operation_id, error_code, message):
        with self.transaction() as connection:
            submission = self._submission(connection, submission_id)
            progress = connection.execute(sa.select(db.task_progress).where(
                *self._progress_key(submission)
            ).with_for_update()).mappings().one()
            if progress['latest_review_operation_id'] != operation_id:
                raise RevisionConflict('AI 판정 실행 소유권이 현재 진행 상태와 다릅니다.')
            if progress['status'] == 'REVIEW_FAILED':
                return {'status': 'REVIEW_FAILED', 'error_code': progress['review_error_code'], 'reused': True}
            if progress['status'] != 'REVIEWING':
                raise RevisionConflict('이미 종료된 AI 판정을 실패 상태로 바꿀 수 없습니다.')
            connection.execute(db.task_progress.update().where(*self._progress_key(submission)).values(
                status='REVIEW_FAILED', review_error_code=error_code,
                review_error_message=str(message)[:1000], updated_at=sa.func.now(),
                revision=db.task_progress.c.revision + 1,
            ))
            return {'status': 'REVIEW_FAILED', 'error_code': error_code, 'reused': False}

    def review_submission(self, submission_id, operation_id, prepared_evidence: list[PreparedEvidence], *, runtime=None, fresh=False):
        claim = self.claim_review(submission_id, operation_id)
        if not claim['claimed']:
            return claim
        try:
            submission = self.get_submission(submission_id)
            context = self._evaluation_context(
                submission['group_id'], submission['plan_version_id'], submission['task_id'],
                submission['member_id'],
            )
            task = context['task']
            request = CompletionReviewRequest(
                submission_id=submission_id, baseline_id=context['baseline_id'], group_id=submission['group_id'],
                plan_version_id=submission['plan_version_id'], task_id=submission['task_id'],
                owner_member_id=submission['member_id'],
                task=TaskSnapshot(**{key: task[key] for key in TaskSnapshot.model_fields}),
                submission=SubmissionSnapshot(description=submission['description'], submitted_at=submission['created_at']),
                evidence=[EvidenceMetadata.model_validate(item) for item in submission['evidence']],
            )
            result = review_completion(request, prepared_evidence, runtime=runtime, fresh=fresh)
            if result.status != 'OK':
                code = result.error.code if result.error else 'AI_REVIEW_FAILED'
                message = result.error.message if result.error else 'AI 판정에 실패했습니다.'
                return self.save_review_failure(submission_id, operation_id, code, message)
            return self.save_review(submission_id, operation_id, result.data, meta=result.meta)
        except Exception as exc:
            try:
                return self.save_review_failure(submission_id, operation_id, 'AI_REVIEW_FAILED', str(exc))
            except RevisionConflict:
                raise

    def create_appeal(self, review_id, submission_id, member_id, action_id, reason, *, appeal_id=None):
        if not action_id or not isinstance(reason, str) or not reason.strip():
            raise ValueError('action_id and reason are required')
        with self.transaction() as connection:
            submission = self._submission(connection, submission_id)
            signature = digest([review_id, submission_id, member_id, reason.strip()])
            self._action_lock(connection, 'appeal', submission['group_id'], action_id)
            old = connection.execute(sa.select(db.completion_appeals).where(
                db.completion_appeals.c.group_id == submission['group_id'],
                db.completion_appeals.c.action_id == action_id,
            )).mappings().first()
            if old:
                if old['input_hash'] != signature:
                    raise RevisionConflict('같은 action_id의 소명 입력이 다릅니다.')
                return self._appeal_state(connection, old['appeal_id']) | {'reused': True}
            if submission['member_id'] != member_id:
                raise CompletionError('TASK_OWNER_MISMATCH', '제출자만 소명할 수 있습니다.')
            review = connection.execute(sa.select(db.completion_reviews).where(
                db.completion_reviews.c.review_id == review_id,
                db.completion_reviews.c.submission_id == submission_id,
            )).mappings().first()
            if not review or review['decision'] == 'PASS':
                raise CompletionError('APPEAL_NOT_ALLOWED', '소명 가능한 미통과 판정이 아닙니다.')
            progress = connection.execute(sa.select(db.task_progress).where(
                *self._progress_key(submission)
            ).with_for_update()).mappings().one()
            if progress['latest_review_id'] != review_id or progress['status'] not in {'REVIEW_NEEDED','NEEDS_INPUT'}:
                raise CompletionError('APPEAL_NOT_ALLOWED', '현재 판정에 대해서만 소명할 수 있습니다.')
            open_appeal = connection.execute(sa.select(db.completion_appeals.c.appeal_id).where(
                db.completion_appeals.c.submission_id == submission_id,
                db.completion_appeals.c.status == 'OPEN',
            )).first()
            if open_appeal:
                raise CompletionError('APPEAL_NOT_ALLOWED', '이미 열린 소명이 있습니다.')
            eligible = connection.scalar(sa.select(sa.func.count()).select_from(db.members).where(
                db.members.c.group_id == submission['group_id'], db.members.c.member_id != member_id
            ))
            required = eligible // 2 + 1
            appeal_id = appeal_id or str(uuid4())
            connection.execute(db.completion_appeals.insert().values(
                appeal_id=appeal_id, review_id=review_id, submission_id=submission_id,
                group_id=submission['group_id'], member_id=member_id, action_id=action_id,
                input_hash=signature, reason=reason.strip(), required_votes=required,
                eligible_voters=eligible, status='OPEN',
            ))
            connection.execute(db.task_progress.update().where(*self._progress_key(submission)).values(
                status='APPEALED', latest_appeal_id=appeal_id, updated_at=sa.func.now(),
                revision=db.task_progress.c.revision + 1,
            ))
            return self._appeal_state(connection, appeal_id) | {'reused': False}

    def _appeal_state(self, connection, appeal_id):
        appeal = connection.execute(sa.select(db.completion_appeals).where(
            db.completion_appeals.c.appeal_id == appeal_id
        )).mappings().one()
        approvals = connection.scalar(sa.select(sa.func.count()).select_from(db.completion_votes).where(
            db.completion_votes.c.appeal_id == appeal_id,
            db.completion_votes.c.approve.is_(True),
        ))
        return dict(appeal) | {'approve_votes': approvals}

    def cast_vote(self, appeal_id, group_id, voter_member_id, approve, action_id):
        if not action_id:
            raise ValueError('action_id is required')
        signature = digest([appeal_id, group_id, voter_member_id, bool(approve)])
        with self.transaction() as connection:
            self._action_lock(connection, 'vote', group_id, action_id)
            old = connection.execute(sa.select(db.completion_votes).where(
                db.completion_votes.c.group_id == group_id,
                db.completion_votes.c.action_id == action_id,
            )).mappings().first()
            if old:
                if old['input_hash'] != signature:
                    raise RevisionConflict('같은 action_id의 투표 입력이 다릅니다.')
                return self._appeal_state(connection, old['appeal_id']) | {'reused': True}
            appeal = connection.execute(sa.select(db.completion_appeals).where(
                db.completion_appeals.c.appeal_id == appeal_id,
                db.completion_appeals.c.group_id == group_id,
            ).with_for_update()).mappings().first()
            if not appeal:
                raise CompletionError('APPEAL_NOT_ALLOWED', '소명을 찾을 수 없습니다.')
            if appeal['status'] != 'OPEN':
                code = 'APPEAL_SUPERSEDED' if appeal['status'] == 'SUPERSEDED' else 'APPEAL_NOT_ALLOWED'
                raise CompletionError(code, '열린 소명에만 투표할 수 있습니다.')
            member = connection.execute(sa.select(db.members.c.member_id).where(
                db.members.c.group_id == group_id, db.members.c.member_id == voter_member_id,
            )).first()
            if not member:
                raise CompletionError('VOTER_NOT_IN_GROUP', '그룹 구성원만 투표할 수 있습니다.')
            if voter_member_id == appeal['member_id']:
                raise CompletionError('SELF_VOTE_NOT_ALLOWED', '제출자는 자신의 소명에 투표할 수 없습니다.')
            if connection.execute(sa.select(db.completion_votes.c.appeal_id).where(
                db.completion_votes.c.appeal_id == appeal_id,
                db.completion_votes.c.voter_member_id == voter_member_id,
            )).first():
                raise CompletionError('VOTE_ALREADY_CAST', '이미 이 소명에 투표했습니다.')
            connection.execute(db.completion_votes.insert().values(
                appeal_id=appeal_id, group_id=group_id, voter_member_id=voter_member_id,
                action_id=action_id, input_hash=signature, approve=bool(approve),
            ))
            approvals = connection.scalar(sa.select(sa.func.count()).select_from(db.completion_votes).where(
                db.completion_votes.c.appeal_id == appeal_id,
                db.completion_votes.c.approve.is_(True),
            ))
            if approvals >= appeal['required_votes']:
                connection.execute(db.completion_appeals.update().where(
                    db.completion_appeals.c.appeal_id == appeal_id
                ).values(status='APPROVED', resolved_at=sa.func.now()))
                submission = self._submission(connection, appeal['submission_id'])
                connection.execute(db.task_progress.update().where(
                    *self._progress_key(submission), db.task_progress.c.latest_appeal_id == appeal_id
                ).values(status='COMPLETED', completed_at=sa.func.now(), updated_at=sa.func.now(),
                         revision=db.task_progress.c.revision + 1))
            return self._appeal_state(connection, appeal_id) | {'reused': False}
