"""Completion submission/review API for the existing kickoff demo."""
from functools import lru_cache
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from wemeet_ai.m2 import (
    EvidenceError, EvidenceUpload, load_prepared_evidence, stage_submission_evidence,
)
from wemeet_ai.m2.evidence import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES
from wemeet_ai.persistence import CompletionError, CompletionRepository
from wemeet_ai.persistence.errors import RevisionConflict
from wemeet_ai.shared.contracts import Contract
from wemeet_ai.shared.privacy import sanitize
from .kickoff import runtime_for


router = APIRouter(prefix='/api/kickoff/groups')
ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def configured(url):
    return CompletionRepository(url)


def repository():
    url = os.getenv('WEMEET_GOAL_DATABASE_URL')
    if not url:
        raise RuntimeError('GOAL_DATABASE_NOT_CONFIGURED')
    return configured(url)


def submission_storage_root():
    return ROOT / 'runtime' / 'submissions'


class ReviewAction(Contract):
    action_id: str = Field(min_length=1, max_length=128)


class AppealAction(ReviewAction):
    reason: str = Field(min_length=1, max_length=5000)


class VoteAction(ReviewAction):
    voter_member_id: str = Field(min_length=1, max_length=128)
    approve: bool


STATUS_BY_CODE = {
    'BASELINE_NOT_READY': 409, 'TASK_NOT_FOUND': 404, 'TASK_OWNER_MISMATCH': 403,
    'TASK_ALREADY_COMPLETED': 409, 'SUBMISSION_NOT_FOUND': 404,
    'REVIEW_NOT_READY': 409, 'APPEAL_NOT_ALLOWED': 409, 'APPEAL_SUPERSEDED': 409,
    'SELF_VOTE_NOT_ALLOWED': 403, 'VOTER_NOT_IN_GROUP': 403, 'VOTE_ALREADY_CAST': 409,
    'UNSUPPORTED_FILE_TYPE': 415, 'FILE_TOO_LARGE': 413, 'TOO_MANY_FILES': 413,
    'PDF_TOO_MANY_PAGES': 413, 'EVIDENCE_TOO_LARGE': 413,
    'CORRUPT_EVIDENCE': 422, 'CORRUPT_IMAGE': 422, 'UNSUPPORTED_IMAGE': 422,
    'VISION_NOT_SUPPORTED': 422, 'REVISION_CONFLICT': 409,
    'AI_REVIEW_FAILED': 502, 'STORAGE_FAILED': 503,
}


def failure(exc):
    if isinstance(exc, CompletionError):
        code = exc.code
    elif isinstance(exc, EvidenceError):
        code = exc.code
    elif isinstance(exc, RevisionConflict):
        code = 'REVISION_CONFLICT'
    elif isinstance(exc, (ValidationError, ValueError, KeyError)):
        code = 'INPUT_INVALID'
    else:
        code = 'STORAGE_FAILED'
    return JSONResponse(
        {'status': 'FAILED', 'error': {'code': code, 'message': str(exc) if code != 'STORAGE_FAILED' else '저장 처리를 완료하지 못했습니다.'}},
        status_code=STATUS_BY_CODE.get(code, 422 if code == 'INPUT_INVALID' else 503),
    )


def _scoped_submission(repo, group_id, member_id, submission_id):
    submission = repo.get_submission(submission_id)
    if submission['group_id'] != group_id or submission['member_id'] != member_id:
        raise CompletionError('TASK_OWNER_MISMATCH', '해당 구성원의 제출이 아닙니다.')
    return submission


def _public_submission(submission):
    return {key: value for key, value in submission.items() if key not in {'input_hash','action_id'}}


@router.get('/{gid}/members/{mid}/completion/tasks')
def tasks(gid: str, mid: str):
    try:
        return sanitize({'group_id': gid, 'member_id': mid, 'tasks': repository().list_member_tasks(gid, mid)})
    except Exception as exc:
        return failure(exc)


@router.post('/{gid}/members/{mid}/plans/{plan_id}/tasks/{task_id}/submissions')
async def submit(gid: str, mid: str, plan_id: str, task_id: str, request: Request):
    submission_id = str(uuid4())
    staged = None
    try:
        form = await request.form()
        action_id = str(form.get('action_id') or '').strip()
        description = str(form.get('description') or '').strip()
        uploads = [item for key, item in form.multi_items()
                   if key in {'files', 'files[]'} and hasattr(item, 'filename') and item.filename]
        if not action_id or not description:
            raise ValueError('action_id and description are required')
        if len(uploads) > MAX_FILES:
            raise EvidenceError('TOO_MANY_FILES', f'제출당 파일은 최대 {MAX_FILES}개입니다.')
        file_inputs = []
        total = 0
        for upload in uploads:
            content = await upload.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise EvidenceError('FILE_TOO_LARGE', '파일당 최대 크기는 10MB입니다.')
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise EvidenceError('EVIDENCE_TOO_LARGE', '제출 파일 전체 크기는 최대 20MB입니다.')
            file_inputs.append(EvidenceUpload(upload.filename, content))
        if file_inputs:
            staged = stage_submission_evidence(
                gid, submission_id, file_inputs, storage_root=submission_storage_root())
        result = repository().create_submission(
            gid, plan_id, task_id, mid, action_id, description,
            staged.prepared.evidence if staged else [], submission_id=submission_id,
            finalize_storage=staged.commit if staged else None,
        )
        if result.get('reused') and staged:
            staged.discard()
        public = _public_submission(result)
        return sanitize({'submission_id': public['submission_id'], 'status': 'SUBMITTED',
                         'evidence': public['evidence'], 'reused': result.get('reused', False)})
    except Exception as exc:
        if staged:
            try:
                staged.discard()
            except Exception:
                pass
        return failure(exc)


@router.post('/{gid}/members/{mid}/submissions/{submission_id}/review')
def review(gid: str, mid: str, submission_id: str, body: dict):
    try:
        action = ReviewAction.model_validate(body)
        repo = repository()
        submission = _scoped_submission(repo, gid, mid, submission_id)
        prepared = load_prepared_evidence(
            gid, submission_id, submission['evidence'], storage_root=submission_storage_root())
        result = repo.review_submission(
            submission_id, action.action_id, prepared, runtime=runtime_for(gid))
        if result.get('status') == 'REVIEW_FAILED' or result.get('state') == 'FAILED':
            code = result.get('error_code') or 'AI_REVIEW_FAILED'
            return JSONResponse({'status': 'REVIEW_FAILED', 'error': {'code': code,
                'message': 'AI 판정을 완료하지 못했습니다. 저장된 제출로 새 동작을 시도할 수 있습니다.'}},
                status_code=STATUS_BY_CODE.get(code, 502))
        return sanitize(result)
    except Exception as exc:
        return failure(exc)


@router.post('/{gid}/members/{mid}/reviews/{review_id}/appeals')
def appeal(gid: str, mid: str, review_id: str, body: dict):
    try:
        action = AppealAction.model_validate(body)
        repo = repository()
        review_row = repo.get_review(review_id)
        submission = _scoped_submission(repo, gid, mid, review_row['submission_id'])
        result = repo.create_appeal(
            review_id, submission['submission_id'], mid, action.action_id, action.reason)
        return sanitize({'status': 'APPEALED', 'appeal': result})
    except Exception as exc:
        return failure(exc)


@router.post('/{gid}/appeals/{appeal_id}/votes')
def vote(gid: str, appeal_id: str, body: dict):
    try:
        action = VoteAction.model_validate(body)
        result = repository().cast_vote(
            appeal_id, gid, action.voter_member_id, action.approve, action.action_id)
        status = 'COMPLETED' if result['status'] == 'APPROVED' else 'APPEALED'
        return sanitize({'status': status, 'appeal': result})
    except Exception as exc:
        return failure(exc)
