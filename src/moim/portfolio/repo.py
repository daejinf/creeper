"""Portfolio storage: the only code that reads or writes the `portfolio_*` tables (and the one source read).

Every function takes the owner as a REQUIRED keyword argument `user_id` (no default) and puts it in every WHERE
clause; a missing, empty or malformed id raises before any SQL runs. There is no function that reads across users.
The caller owns the transaction (`s`). The one cross-user write, marking interrupted jobs failed at startup, lives in
`service.PortfolioService.fail_interrupted` and returns a count only.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import and_, delete, func, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..execution.files import UUID_RE
from ..models import (
    execution_files,
    execution_records,
    group_members,
    portfolio_documents,
    portfolio_items,
    portfolio_jobs,
    portfolio_nodes,
    portfolio_projects,
    portfolio_summaries,
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


def search_scope(s: Session, *, user_id: str, project_id: str | None = None, since: datetime | None = None,
                 until: datetime | None = None) -> list:
    """Stage 8C (RAG_DESIGN 3.1): every node of the owner's READY items, the scope of one question. The owner and
    `READY` are fixed here first; a project or completion-time filter only narrows inside that. Rows carry the
    node, its file name (no file for a summary node) and the item's completion time, in submission order."""
    uid = _uid(user_id)
    n, i, d, j = portfolio_nodes, portfolio_items, portfolio_documents, portfolio_jobs
    q = (select(n.c.id, n.c.project_id, n.c.item_id, n.c.document_id, n.c.kind, n.c.position, n.c.locator,
                n.c.text, n.c.index_text, n.c.bigrams, n.c.token_estimate, n.c.prev_id, n.c.next_id,
                n.c.source_block_ids, n.c.embedding, n.c.embed_model, n.c.embed_dim, d.c.file_name, i.c.completed_at)
         .join(i, and_(i.c.id == n.c.item_id, i.c.user_id == uid))
         .join(j, and_(j.c.item_id == i.c.id, j.c.user_id == uid, j.c.state == "READY"))
         .outerjoin(d, and_(d.c.id == n.c.document_id, d.c.user_id == uid))
         .where(n.c.user_id == uid))
    if project_id is not None:
        q = q.where(n.c.project_id == project_id)
    if since is not None:
        q = q.where(i.c.completed_at >= since)
    if until is not None:
        q = q.where(i.c.completed_at < until)
    rows = s.execute(q.order_by(i.c.completed_at, i.c.id, n.c.kind != "doc_summary", n.c.position)).all()
    if since is not None or until is not None:
        return rows  # a time filter is about completions; a project summary spans them, so it stays out
    # Stage 8D (RAG_DESIGN 2.5.1): each project's summary node (no item) joins the same pool, after the items. Only a
    # project with at least one READY item in scope contributes one.
    pids = list(dict.fromkeys(r.project_id for r in rows))
    if not pids:
        return rows
    ps = s.execute(select(n.c.id, n.c.project_id, n.c.item_id, n.c.document_id, n.c.kind, n.c.position, n.c.locator,
                          n.c.text, n.c.index_text, n.c.bigrams, n.c.token_estimate, n.c.prev_id, n.c.next_id,
                          n.c.source_block_ids, n.c.embedding, n.c.embed_model, n.c.embed_dim,
                          literal(None).label("file_name"), literal(None).label("completed_at"))
                   .where(n.c.user_id == uid, n.c.kind == "project_summary", n.c.project_id.in_(pids))
                   .order_by(n.c.project_id, n.c.position)).all()
    return rows + ps


def block_places(s: Session, *, user_id: str, item_ids: list[str]) -> dict:
    """`{(item_id, block_id): (document_id, file_name, locator)}` of the given items of the owner: where a summary
    node's evidence blocks are in the attachments (citations resolve to `file · place`)."""
    uid = _uid(user_id)
    ids = [x for x in item_ids if _id(x)]
    if not ids:
        return {}
    rows = s.execute(select(portfolio_documents.c.id, portfolio_documents.c.item_id, portfolio_documents.c.file_name,
                            portfolio_documents.c.blocks)
                     .where(portfolio_documents.c.user_id == uid, portfolio_documents.c.item_id.in_(ids))).all()
    out: dict = {}
    for r in rows:
        for b in r.blocks or []:
            out[(r.item_id, b.get("block_id"))] = (r.id, r.file_name, b.get("locator") or {})
    return out


def block_texts(s: Session, *, user_id: str, keys: list[tuple[str, str]]) -> dict:
    """Stage 8D: `{(item_id, block_id): text}` of the owner's blocks: the source of a server-cut evidence excerpt."""
    uid = _uid(user_id)
    ids = sorted({k[0] for k in keys if _id(k[0])})
    if not ids:
        return {}
    want = set(keys)
    rows = s.execute(select(portfolio_documents.c.item_id, portfolio_documents.c.blocks)
                     .where(portfolio_documents.c.user_id == uid, portfolio_documents.c.item_id.in_(ids))).all()
    out: dict = {}
    for r in rows:
        for b in r.blocks or []:
            k = (r.item_id, b.get("block_id"))
            if k in want:
                out[k] = str(b.get("text") or "")
    return out


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


def replace_nodes(s: Session, *, user_id: str, item_id: str, project_id: str, kind: str, nodes: list[dict]) -> None:
    """Stage 8B: every node of one kind of the item is replaced (chunks rebuilt after DESCRIBING, the doc_summary)."""
    uid = _uid(user_id)
    if kind not in ("chunk", "doc_summary"):
        raise ValueError("unknown node kind")
    s.execute(delete(portfolio_nodes).where(portfolio_nodes.c.item_id == item_id, portfolio_nodes.c.user_id == uid,
                                            portfolio_nodes.c.kind == kind))
    for n in nodes:
        if n.get("kind") != kind:
            raise ValueError("node kind mismatch")
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


# ---- summaries (stage 8D, RAG_DESIGN 2.5.1-2.5.2) ----------------------------------------------------------------


def get_summary(s: Session, *, user_id: str, project_id: str | None, lock: bool = False):
    """The project summary of `project_id`, or the portfolio summary when `project_id` is None."""
    uid = _uid(user_id)
    t = portfolio_summaries
    q = select(t).where(t.c.user_id == uid)
    q = q.where(t.c.scope == "PORTFOLIO") if project_id is None else q.where(t.c.scope == "PROJECT",
                                                                             t.c.project_id == project_id)
    return s.execute(q.with_for_update() if lock else q).one_or_none()


def list_summaries(s: Session, *, user_id: str) -> list:
    uid = _uid(user_id)
    return s.execute(select(portfolio_summaries).where(portfolio_summaries.c.user_id == uid)).all()


def start_summary(s: Session, *, user_id: str, project_id: str | None, action_id: str, now: datetime) -> tuple[str, int]:
    """Claim a run: insert the row or move it to RUNNING with a new revision. Returns ("STARTED", rev), ("SAME", rev)
    for an action id seen before (no second run), or ("BUSY", rev) while another run is in progress. The content of
    the last good run stays until a new one succeeds."""
    uid = _uid(user_id)
    row = get_summary(s, user_id=uid, project_id=project_id, lock=True)
    if row is None:
        made = s.execute(pg_insert(portfolio_summaries).values(
            id=str(uuid.uuid4()), user_id=uid, project_id=project_id,
            scope="PROJECT" if project_id else "PORTFOLIO", state="RUNNING", revision=1, last_action_id=action_id,
            created_at=now, updated_at=now).on_conflict_do_nothing().returning(portfolio_summaries.c.id)
        ).scalar_one_or_none()
        if made is not None:
            return "STARTED", 1
        row = get_summary(s, user_id=uid, project_id=project_id, lock=True)  # a concurrent first run won
    if row.last_action_id == action_id:
        return "SAME", row.revision
    if row.state == "RUNNING":
        return "BUSY", row.revision
    rev = row.revision + 1
    s.execute(update(portfolio_summaries).where(portfolio_summaries.c.id == row.id,
                                                portfolio_summaries.c.user_id == uid)
              .values(state="RUNNING", error_code=None, revision=rev, last_action_id=action_id, updated_at=now))
    return "STARTED", rev


def finish_summary(s: Session, *, user_id: str, project_id: str | None, revision: int, content: dict | None,
                   basis: dict | None, error_code: str | None, meta: dict, now: datetime) -> bool:
    """End a run (compare-and-set on the revision). A failure keeps the earlier content and basis."""
    uid = _uid(user_id)
    t = portfolio_summaries
    q = update(t).where(t.c.user_id == uid, t.c.revision == revision, t.c.state == "RUNNING")
    q = q.where(t.c.scope == "PORTFOLIO") if project_id is None else q.where(t.c.project_id == project_id)
    values: dict = dict(stage_meta=meta, updated_at=now)
    if error_code is None:
        values.update(state="READY", content=content, basis=basis or {}, generated_at=now, error_code=None)
    else:
        values.update(state="FAILED", error_code=error_code[:64])
    return s.execute(q.values(**values)).rowcount == 1


def replace_project_summary_node(s: Session, *, user_id: str, project_id: str, node: dict | None) -> None:
    """The searchable `project_summary` node of a project (no item, no document): replaced, or removed with None."""
    uid = _uid(user_id)
    s.execute(delete(portfolio_nodes).where(portfolio_nodes.c.user_id == uid, portfolio_nodes.c.project_id == project_id,
                                            portfolio_nodes.c.kind == "project_summary"))
    if node is not None:
        if node.get("kind") != "project_summary":
            raise ValueError("node kind mismatch")
        values = {k: v for k, v in node.items() if k not in ("item_id", "document_id", "user_id", "project_id")}
        s.execute(insert(portfolio_nodes).values(user_id=uid, project_id=project_id, item_id=None, document_id=None,
                                                 **values))


def ready_items(s: Session, *, user_id: str, project_id: str | None = None) -> list:
    """The owner's items whose job is READY, in completion order."""
    uid = _uid(user_id)
    i, j = portfolio_items, portfolio_jobs
    q = (select(i).join(j, and_(j.c.item_id == i.c.id, j.c.user_id == uid, j.c.state == "READY"))
         .where(i.c.user_id == uid))
    if project_id is not None:
        q = q.where(i.c.project_id == project_id)
    return s.execute(q.order_by(i.c.completed_at, i.c.id)).all()


def summary_nodes(s: Session, *, user_id: str, item_ids: list[str]) -> list:
    """The `doc_summary` nodes of the given items of the owner (their claims are in `locator.claims`)."""
    uid = _uid(user_id)
    ids = [x for x in item_ids if _id(x)]
    if not ids:
        return []
    return s.execute(select(portfolio_nodes).where(portfolio_nodes.c.user_id == uid,
                                                   portfolio_nodes.c.kind == "doc_summary",
                                                   portfolio_nodes.c.item_id.in_(ids))
                     .order_by(portfolio_nodes.c.item_id)).all()


def item_overview(s: Session, *, user_id: str) -> list:
    """One row per item of the owner: project, title, completion, job id/state/revision/error, chunk count."""
    uid = _uid(user_id)
    i, j, n = portfolio_items, portfolio_jobs, portfolio_nodes
    chunks = (select(func.count()).select_from(n)
              .where(n.c.user_id == uid, n.c.item_id == i.c.id, n.c.kind == "chunk").scalar_subquery())
    return s.execute(select(i.c.id, i.c.project_id, i.c.task_title, i.c.completed_at, i.c.file_count,
                            j.c.id.label("job_id"), j.c.state, j.c.revision, j.c.error_code,
                            chunks.label("chunks"))
                     .join(j, and_(j.c.item_id == i.c.id, j.c.user_id == uid))
                     .where(i.c.user_id == uid).order_by(i.c.completed_at, i.c.id)).all()


def documents_of(s: Session, *, user_id: str, item_ids: list[str]) -> list:
    """File name, kind, status and blocks of the given items' documents."""
    uid = _uid(user_id)
    ids = [x for x in item_ids if _id(x)]
    if not ids:
        return []
    d = portfolio_documents
    return s.execute(select(d.c.id, d.c.item_id, d.c.file_name, d.c.kind, d.c.status, d.c.position, d.c.blocks)
                     .where(d.c.user_id == uid, d.c.item_id.in_(ids)).order_by(d.c.item_id, d.c.position)).all()


def importable_records(s: Session, *, user_id: str) -> list[tuple[str, list[str]]]:
    """D-I1 I6: the owner's own DONE records with attachments, in a group where the owner is a current active member,
    that have no portfolio item yet: `[(record_id, [file kinds])]` in completion order."""
    uid = _uid(user_id)
    r, f, m = execution_records, execution_files, group_members
    rows = s.execute(
        select(r.c.id, f.c.kind)
        .join(f, f.c.record_id == r.c.id)
        .join(m, and_(m.c.group_id == r.c.group_id, m.c.user_id == uid))
        .outerjoin(portfolio_items, and_(portfolio_items.c.source_record_id == r.c.id,
                                         portfolio_items.c.user_id == uid))
        .where(r.c.user_id == uid, r.c.completion == "DONE", r.c.task_id.is_not(None),
               portfolio_items.c.id.is_(None))
        .order_by(r.c.created_at, r.c.id, f.c.position)).all()
    out: dict[str, list[str]] = {}
    for rid, kind in rows:
        out.setdefault(rid, []).append(kind)
    return list(out.items())


# ---- deletion (D-I1 I4) ---------------------------------------------------------------------------------------------


def delete_item(s: Session, *, user_id: str, item_id: str) -> int:
    """One item with its documents, nodes and job (cascade). A project left empty goes too."""
    uid = _uid(user_id)
    pid = s.execute(delete(portfolio_items).where(portfolio_items.c.id == item_id, portfolio_items.c.user_id == uid)
                    .returning(portfolio_items.c.project_id)).scalar_one_or_none()
    if pid is None:
        return 0
    _prune_summaries(s, uid, {item_id}, {pid})
    _drop_empty_project(s, uid, pid)
    return 1


def delete_item_for_record(s: Session, *, user_id: str, record_id: str) -> int:
    """`되돌리기` of a completion: its copy goes in the same transaction."""
    uid = _uid(user_id)
    gone = s.execute(delete(portfolio_items).where(portfolio_items.c.source_record_id == record_id,
                                                   portfolio_items.c.user_id == uid)
                     .returning(portfolio_items.c.id, portfolio_items.c.project_id)).all()
    rows = [g.project_id for g in gone]
    if gone:
        _prune_summaries(s, uid, {g.id for g in gone}, set(rows))
    for pid in set(rows):
        _drop_empty_project(s, uid, pid)
    return len(rows)


def delete_project(s: Session, *, user_id: str, project_id: str) -> int:
    """The project with everything under it (cascade: items, documents, nodes, jobs, its summary). The portfolio
    summary loses every entry resting on those items."""
    uid = _uid(user_id)
    if not _id(project_id):
        return 0
    items = set(s.execute(select(portfolio_items.c.id).where(portfolio_items.c.project_id == project_id,
                                                             portfolio_items.c.user_id == uid)).scalars().all())
    if items:
        _prune_summaries(s, uid, items, set())
    return s.execute(delete(portfolio_projects).where(portfolio_projects.c.id == project_id,
                                                      portfolio_projects.c.user_id == uid)).rowcount


def delete_all(s: Session, *, user_id: str) -> int:
    """Account deletion (D7): every portfolio row of the owner. Children go by cascade."""
    uid = _uid(user_id)
    s.execute(delete(portfolio_summaries).where(portfolio_summaries.c.user_id == uid))
    return s.execute(delete(portfolio_projects).where(portfolio_projects.c.user_id == uid)).rowcount


def count_rows(s: Session, *, user_id: str) -> dict:
    uid = _uid(user_id)
    n = lambda t: s.execute(select(func.count()).select_from(t).where(t.c.user_id == uid)).scalar_one()  # noqa: E731
    return {"projects": n(portfolio_projects), "items": n(portfolio_items), "documents": n(portfolio_documents),
            "nodes": n(portfolio_nodes), "jobs": n(portfolio_jobs), "summaries": n(portfolio_summaries)}


def _prune_summaries(s: Session, uid: str, gone_items: set[str], projects: set[str]) -> None:
    """Stage 8D (D-I1 I4): a deleted item takes every summary sentence or skill that rests on it (that text came from
    its material). The touched projects' searchable summary nodes go too (they would still hold the text); the
    summaries show as "오래됨" until the owner makes them again."""
    t = portfolio_summaries
    rows = s.execute(select(t.c.id, t.c.content).where(t.c.user_id == uid, t.c.content.is_not(None))
                     .with_for_update()).all()
    for r in rows:
        changed = False
        new = {}
        for k, v in (r.content or {}).items():
            if isinstance(v, list):
                kept = [e for e in v if not any(ref[0] in gone_items for ref in e.get("refs", []))]
                changed = changed or len(kept) != len(v)
                new[k] = kept
            else:
                new[k] = v
        if changed:
            s.execute(update(t).where(t.c.id == r.id, t.c.user_id == uid).values(content=new))
    for pid in projects:
        s.execute(delete(portfolio_nodes).where(portfolio_nodes.c.user_id == uid, portfolio_nodes.c.project_id == pid,
                                                portfolio_nodes.c.kind == "project_summary"))


def _drop_empty_project(s: Session, uid: str, project_id: str) -> None:
    left = s.execute(select(func.count()).select_from(portfolio_items).where(
        and_(portfolio_items.c.project_id == project_id, portfolio_items.c.user_id == uid))).scalar_one()
    if not left:
        s.execute(delete(portfolio_projects).where(portfolio_projects.c.id == project_id,
                                                   portfolio_projects.c.user_id == uid))
