"""stage 8A: portfolio RAG copies (D-I1): projects, items, documents, nodes and ingest jobs

Revision ID: 0014
Revises: 0013

New tables only, all owned by one user: every row has `user_id` (users ON DELETE CASCADE) and every child repeats it in
a composite foreign key to its parent's (id, user_id), so a row can never sit under another user's parent. The source
columns (`source_group_id`, `source_record_id`, `source_task_id`, `source_file_id`) have NO foreign key to the group
tables on purpose: the copy outlives a left or deleted group (D-I1 I4). `portfolio_items.source_record_id` is unique:
it is the idempotency key of the ingest trigger.

Not created here (D-I1, RAG_DESIGN 4): original file bytes, a question/answer table, any database extension. The
migration runs as the migration owner role; the runtime app role gets select/insert/update/delete on these tables
through the owner's default privileges set up in stage 7 (`tools/ops_db.py setup-roles`/`grant`), nothing more.

Downgrade drops these five tables only (portfolio copies are lost; nothing else changes).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

FILE_KINDS = "('PNG','JPEG','WEBP','PDF','DOCX','HWP','HWPX','TXT','MD','PPTX','XLSX')"
STAGES = "('EXTRACTING','DESCRIBING','UNDERSTANDING','EMBEDDING')"


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=None if nullable else sa.func.now())


def _owner_fk(table: str, col: str, parent: str, name: str):
    return sa.ForeignKeyConstraint([col, "user_id"], [f"{parent}.id", f"{parent}.user_id"], ondelete="CASCADE",
                                   name=name)


def _user_fk(table: str):
    return sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name=op.f(f"fk_{table}_user_id_users"))


def upgrade() -> None:
    op.create_table(
        "portfolio_projects",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_group_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("role_name", sa.String(30), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_projects")),
        _user_fk("portfolio_projects"),
        sa.UniqueConstraint("id", "user_id", name="uq_portfolio_projects_owner"),
        sa.UniqueConstraint("user_id", "source_group_id", name="uq_portfolio_projects_source"),
    )
    op.create_table(
        "portfolio_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_record_id", sa.String(36), nullable=False),
        sa.Column("source_task_id", sa.String(36), nullable=False),
        sa.Column("task_title", sa.String(60), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("role_name", sa.String(30), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("completed_by", sa.String(6), nullable=False),
        sa.Column("review_decision", sa.String(14), nullable=True),
        _ts("completed_at"),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_items")),
        _user_fk("portfolio_items"),
        _owner_fk("portfolio_items", "project_id", "portfolio_projects", "fk_portfolio_items_project"),
        sa.UniqueConstraint("source_record_id", name="uq_portfolio_items_source_record"),
        sa.UniqueConstraint("id", "user_id", name="uq_portfolio_items_owner"),
        sa.CheckConstraint("completed_by in ('AI','TEAM','SELF','MANUAL')", name=op.f("ck_portfolio_items_completed_by")),
        sa.CheckConstraint("review_decision is null or review_decision in ('PASS','REVIEW_NEEDED','NEEDS_INPUT')",
                           name=op.f("ck_portfolio_items_review_decision")),
        sa.CheckConstraint("file_count between 1 and 5", name=op.f("ck_portfolio_items_file_count")),
    )
    op.create_index("ix_portfolio_items_project", "portfolio_items", ["user_id", "project_id"])
    op.create_table(
        "portfolio_documents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_file_id", sa.String(36), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("truncated_reason", sa.String(32), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocks", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_documents")),
        _user_fk("portfolio_documents"),
        _owner_fk("portfolio_documents", "item_id", "portfolio_items", "fk_portfolio_documents_item"),
        sa.UniqueConstraint("id", "user_id", name="uq_portfolio_documents_owner"),
        sa.UniqueConstraint("item_id", "position", name="uq_portfolio_documents_position"),
        sa.CheckConstraint(f"kind in {FILE_KINDS}", name=op.f("ck_portfolio_documents_kind")),
        sa.CheckConstraint("status in ('EXTRACTED','NAME_ONLY','REJECTED')", name=op.f("ck_portfolio_documents_status")),
        sa.CheckConstraint("(status = 'REJECTED') = (error_code is not null)", name=op.f("ck_portfolio_documents_error_iff")),
        sa.CheckConstraint("truncated = (truncated_reason is not null)", name=op.f("ck_portfolio_documents_truncated_iff")),
    )
    op.create_table(
        "portfolio_nodes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=True),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("index_text", sa.Text(), nullable=False),
        sa.Column("bigrams", postgresql.JSONB(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("prev_id", sa.String(36), nullable=True),
        sa.Column("next_id", sa.String(36), nullable=True),
        sa.Column("source_block_ids", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embed_model", sa.String(64), nullable=True),
        sa.Column("embed_dim", sa.Integer(), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_nodes")),
        _user_fk("portfolio_nodes"),
        _owner_fk("portfolio_nodes", "project_id", "portfolio_projects", "fk_portfolio_nodes_project"),
        _owner_fk("portfolio_nodes", "item_id", "portfolio_items", "fk_portfolio_nodes_item"),
        _owner_fk("portfolio_nodes", "document_id", "portfolio_documents", "fk_portfolio_nodes_document"),
        sa.CheckConstraint("kind in ('chunk','doc_summary','project_summary')", name=op.f("ck_portfolio_nodes_kind")),
        sa.CheckConstraint("kind = 'project_summary' or item_id is not null", name=op.f("ck_portfolio_nodes_item_ref")),
        sa.CheckConstraint("kind <> 'chunk' or document_id is not null", name=op.f("ck_portfolio_nodes_chunk_doc")),
        sa.CheckConstraint("(embedding is null) = (embed_model is null) and (embedding is null) = (embed_dim is null)",
                           name=op.f("ck_portfolio_nodes_embed_meta")),
    )
    op.create_index("ix_portfolio_nodes_user", "portfolio_nodes", ["user_id", "project_id"])
    op.create_table(
        "portfolio_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(14), nullable=False),
        sa.Column("resume_stage", sa.String(14), nullable=True),
        sa.Column("failed_stage", sa.String(14), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("stage_meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("started_at", nullable=True),
        _ts("finished_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_jobs")),
        _user_fk("portfolio_jobs"),
        _owner_fk("portfolio_jobs", "item_id", "portfolio_items", "fk_portfolio_jobs_item"),
        sa.UniqueConstraint("item_id", name="uq_portfolio_jobs_item"),
        sa.CheckConstraint("state in ('QUEUED','EXTRACTING','DESCRIBING','UNDERSTANDING','EMBEDDING','READY','FAILED')",
                           name=op.f("ck_portfolio_jobs_state")),
        sa.CheckConstraint(f"resume_stage is null or resume_stage in {STAGES}", name=op.f("ck_portfolio_jobs_resume_stage")),
        sa.CheckConstraint(f"failed_stage is null or failed_stage in {STAGES}", name=op.f("ck_portfolio_jobs_failed_stage")),
        sa.CheckConstraint("(state = 'FAILED') = (failed_stage is not null) and (state = 'FAILED') = (error_code is not null)",
                           name=op.f("ck_portfolio_jobs_failed_iff")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_portfolio_jobs_revision")),
    )
    op.create_index("ix_portfolio_jobs_state", "portfolio_jobs", ["state"])


def downgrade() -> None:
    op.drop_table("portfolio_jobs")
    op.drop_table("portfolio_nodes")
    op.drop_table("portfolio_documents")
    op.drop_table("portfolio_items")
    op.drop_table("portfolio_projects")
