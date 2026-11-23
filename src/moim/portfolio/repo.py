"""Portfolio storage: the only code that reads or writes the `portfolio_*` tables (and the one source read).

Every function takes the owner as a REQUIRED keyword argument `user_id` (no default) and puts it in every WHERE
clause; a missing, empty or malformed id raises before any SQL runs. There is no function that reads across users.
The caller owns the transaction (`s`). The one cross-user write, marking interrupted jobs failed at startup, lives in
`service.PortfolioService.fail_interrupted` and returns a count only.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..execution.files import UUID_RE
from ..models import (
    execution_files,
    execution_records,
    portfolio_documents,
    portfolio_items,
    portfolio_jobs,
    portfolio_nodes,
    portfolio_projects,
)

NODE_FIELDS = {"context", "index_text", "bigrams", "token_estimate", "embedding", "embed_model", "embed_dim"}


class ScopeError(ValueError):
    """A portfolio call without a valid owner id: refused before any SQL."""


def _uid(user_id) -> str:
    if not isinstance(user_id, str) or not UUID_RE.fullmatch(user_id):
        raise ScopeError("portfolio access needs the owner's user id")
    return user_id


def _id(value) -> str | None:
    return value if isinstance(value, str) and UUID_RE.fullmatch(value) else None


# ---- ingest trigger -------------------------------------------------------------------------------------------------


def upsert_project(s: Session, *, user_id: str, source_group_id: str, name: str, description: str, role_name: str,
                   now: datetime) -> str:
    """One project per (owner, source group). Its copies follow the newest completion."""
    uid = _uid(user_id)
    new_id = str(uuid.uuid4())
    row = s.execute(
        pg_insert(portfolio_projects).values(id=new_id, user_id=uid, source_group_id=source_group_id, name=name,
                                             description=description, role_name=role_name, created_at=now,
                                             updated_at=now)
        .on_conflict_do_update(constraint="uq_portfolio_projects_source",
                               set_={"name": name, "description": description, "role_name": role_name,
                                     "updated_at": now})
        .returning(portfolio_projects.c.id)).scalar_one()
    return row


def create_item_with_job(s: Session, *, user_id: str, project_id: str, source_record_id: str, source_task_id: str,
                         task_title: str, due_date: date, role_name: str, memo: str | None, file_count: int,
                         completed_by: str, review_decision: str | None, completed_at: datetime) -> str | None:
    """Idempotent on `source_record_id`: returns the new job id, or None when that record already has an item."""
    uid = _uid(user_id)
    item_id = str(uuid.uuid4())
    made = s.execute(
        pg_insert(portfolio_items).values(
            id=item_id, user_id=uid, project_id=project_id, source_record_id=source_record_id,
            source_task_id=source_task_id, task_title=task_title, due_date=due_date, role_name=role_name, memo=memo,
            file_count=file_count, completed_by=completed_by, review_decision=review_decision,
            completed_at=completed_at, created_at=completed_at)
        .on_conflict_do_nothing(constraint="uq_portfolio_items_source_record")
        .returning(portfolio_items.c.id)).scalar_one_or_none()
    if made is None:
        return None
    job_id = str(uuid.uuid4())
    s.execute(insert(portfolio_jobs).values(id=job_id, user_id=uid, item_id=item_id, state="QUEUED",
                                            created_at=completed_at, updated_at=completed_at))
    return job_id


def source_files(s: Session, *, user_id: str, record_id: str) -> list:
    """The attachments of ONE completed record of this owner, in upload order. Nothing else is ever read: not another
    member's record, not a record that is no longer DONE. Empty when the record or its group is gone."""
    uid = _uid(user_id)
    if not _id(record_id):
        return []
    return s.execute(
        select(execution_files.c.id, execution_files.c.group_id, execution_files.c.original_name,
               execution_files.c.kind)
        .join(execution_records, execution_records.c.id == execution_files.c.record_id)
        .where(execution_records.c.id == record_id, execution_records.c.user_id == uid,
               execution_records.c.completion == "DONE")
        .order_by(execution_files.c.position)).all()


# ---- reads ----------------------------------------------------------------------------------------------------------


def list_projects(s: Session, *, user_id: str) -> list:
    uid = _uid(user_id)
    return s.execute(select(portfolio_projects).where(portfolio_projects.c.user_id == uid)
                     .order_by(portfolio_projects.c.created_at, portfolio_projects.c.id)).all()


def get_project(s: Session, *, user_id: str, project_id: str):
    uid = _uid(user_id)
    if not _id(project_id):
        return None
    return s.execute(select(portfolio_projects).where(portfolio_projects.c.id == project_id,
                                                      portfolio_projects.c.user_id == uid)).one_or_none()


def list_items(s: Session, *, user_id: str, project_id: str | None = None) -> list:
    uid = _uid(user_id)
    q = select(portfolio_items).where(portfolio_items.c.user_id == uid)
    if project_id is not None:
        q = q.where(portfolio_items.c.project_id == project_id)
    return s.execute(q.order_by(portfolio_items.c.completed_at, portfolio_items.c.id)).all()


def get_item(s: Session, *, user_id: str, item_id: str):
    uid = _uid(user_id)
    if not _id(item_id):
        return None
    return s.execute(select(portfolio_items).where(portfolio_items.c.id == item_id,
                                                   portfolio_items.c.user_id == uid)).one_or_none()


def item_for_record(s: Session, *, user_id: str, record_id: str):
    uid = _uid(user_id)
    return s.execute(select(portfolio_items).where(portfolio_items.c.source_record_id == record_id,
                                                   portfolio_items.c.user_id == uid)).one_or_none()


def list_documents(s: Session, *, user_id: str, item_id: str) -> list:
    uid = _uid(user_id)
    return s.execute(select(portfolio_documents).where(portfolio_documents.c.item_id == item_id,
                                                       portfolio_documents.c.user_id == uid)
                     .order_by(portfolio_documents.c.position)).all()


def list_nodes(s: Session, *, user_id: str, item_id: str | None = None) -> list:
    uid = _uid(user_id)
    q = select(portfolio_nodes).where(portfolio_nodes.c.user_id == uid)
    if item_id is not None:
        q = q.where(portfolio_nodes.c.item_id == item_id)
    return s.execute(q.order_by(portfolio_nodes.c.item_id, portfolio_nodes.c.position)).all()


def get_job(s: Session, *, user_id: str, job_id: str, lock: bool = False):
    uid = _uid(user_id)
    if not _id(job_id):
        return None
    q = select(portfolio_jobs).where(portfolio_jobs.c.id == job_id, portfolio_jobs.c.user_id == uid)
    return s.execute(q.with_for_update() if lock else q).one_or_none()


def job_for_item(s: Session, *, user_id: str, item_id: str):
    uid = _uid(user_id)
    return s.execute(select(portfolio_jobs).where(portfolio_jobs.c.item_id == item_id,
                                                  portfolio_jobs.c.user_id == uid)).one_or_none()


# ---- job state ------------------------------------------------------------------------------------------------------


def update_job(s: Session, *, user_id: str, job_id: str, revision: int, from_state: str, **values) -> bool:
    """Compare-and-set on (state, revision): False when the job moved on, was retried or was deleted meanwhile.
    `new_revision=` sets the revision (a retry)."""
    uid = _uid(user_id)
    if "new_revision" in values:
        values["revision"] = values.pop("new_revision")
    n = s.execute(update(portfolio_jobs).where(
        portfolio_jobs.c.id == job_id, portfolio_jobs.c.user_id == uid, portfolio_jobs.c.revision == revision,
        portfolio_jobs.c.state == from_state).values(**values)).rowcount
    return n == 1


# ---- stage results --------------------------------------------------------------------------------------------------


def replace_extraction(s: Session, *, user_id: str, item_id: str, project_id: str, documents: list[dict],
                       nodes: list[dict]) -> None:
    """The EXTRACTING result, all at once: earlier documents and nodes of the item (a retry) are replaced."""
    uid = _uid(user_id)
    s.execute(delete(portfolio_nodes).where(portfolio_nodes.c.item_id == item_id, portfolio_nodes.c.user_id == uid))
    s.execute(delete(portfolio_documents).where(portfolio_documents.c.item_id == item_id,
                                                portfolio_documents.c.user_id == uid))
    for d in documents:
        s.execute(insert(portfolio_documents).values(user_id=uid, item_id=item_id, **d))
    for n in nodes:
        s.execute(insert(portfolio_nodes).values(user_id=uid, project_id=project_id, item_id=item_id, **n))


def update_node(s: Session, *, user_id: str, node_id: str, **values) -> bool:
    """Later stages fill context, index text, tokens and the embedding. Other columns cannot be changed here."""
    uid = _uid(user_id)
    if not values or set(values) - NODE_FIELDS:
        raise ValueError("unknown node fields")
    return s.execute(update(portfolio_nodes).where(portfolio_nodes.c.id == node_id, portfolio_nodes.c.user_id == uid)
                     .values(**values)).rowcount == 1


def update_document_blocks(s: Session, *, user_id: str, document_id: str, blocks: list[dict]) -> bool:
    uid = _uid(user_id)
    return s.execute(update(portfolio_documents).where(portfolio_documents.c.id == document_id,
                                                       portfolio_documents.c.user_id == uid)
                     .values(blocks=blocks)).rowcount == 1


# ---- deletion (D-I1 I4) ---------------------------------------------------------------------------------------------


def delete_item(s: Session, *, user_id: str, item_id: str) -> int:
    """One item with its documents, nodes and job (cascade). A project left empty goes too."""
    uid = _uid(user_id)
    pid = s.execute(delete(portfolio_items).where(portfolio_items.c.id == item_id, portfolio_items.c.user_id == uid)
                    .returning(portfolio_items.c.project_id)).scalar_one_or_none()
    if pid is None:
        return 0
    _drop_empty_project(s, uid, pid)
    return 1


def delete_item_for_record(s: Session, *, user_id: str, record_id: str) -> int:
    """`되돌리기` of a completion: its copy goes in the same transaction."""
    uid = _uid(user_id)
    rows = s.execute(delete(portfolio_items).where(portfolio_items.c.source_record_id == record_id,
                                                   portfolio_items.c.user_id == uid)
                     .returning(portfolio_items.c.project_id)).scalars().all()
    for pid in set(rows):
        _drop_empty_project(s, uid, pid)
    return len(rows)


def delete_project(s: Session, *, user_id: str, project_id: str) -> int:
    uid = _uid(user_id)
    return s.execute(delete(portfolio_projects).where(portfolio_projects.c.id == project_id,
                                                      portfolio_projects.c.user_id == uid)).rowcount


def delete_all(s: Session, *, user_id: str) -> int:
    """Account deletion (D7): every portfolio row of the owner. Children go by cascade."""
    uid = _uid(user_id)
    return s.execute(delete(portfolio_projects).where(portfolio_projects.c.user_id == uid)).rowcount


def count_rows(s: Session, *, user_id: str) -> dict:
    uid = _uid(user_id)
    n = lambda t: s.execute(select(func.count()).select_from(t).where(t.c.user_id == uid)).scalar_one()  # noqa: E731
    return {"projects": n(portfolio_projects), "items": n(portfolio_items), "documents": n(portfolio_documents),
            "nodes": n(portfolio_nodes), "jobs": n(portfolio_jobs)}


def _drop_empty_project(s: Session, uid: str, project_id: str) -> None:
    left = s.execute(select(func.count()).select_from(portfolio_items).where(
        and_(portfolio_items.c.project_id == project_id, portfolio_items.c.user_id == uid))).scalar_one()
    if not left:
        s.execute(delete(portfolio_projects).where(portfolio_projects.c.id == project_id,
                                                   portfolio_projects.c.user_id == uid))
