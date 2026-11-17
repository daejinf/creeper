"""Completion submissions, AI reviews, appeals, votes, and mutable progress."""
from alembic import op

revision = "0005_completion_review"
down_revision = "0004_private_owner_fk"
branch_labels = None
depends_on = None

DDL = [
    "ALTER TABLE wemeet_goal.tasks ADD CONSTRAINT uq_tasks_completion_owner UNIQUE (plan_version_id, task_id, owner_member_id)",
    """CREATE TABLE wemeet_goal.task_submissions (
        submission_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, plan_version_id TEXT NOT NULL,
        task_id TEXT NOT NULL, member_id TEXT NOT NULL, action_id TEXT NOT NULL,
        description TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (group_id, member_id) REFERENCES wemeet_goal.members(group_id, member_id),
        FOREIGN KEY (group_id, plan_version_id) REFERENCES wemeet_goal.plan_versions(group_id, plan_version_id),
        FOREIGN KEY (plan_version_id, task_id, member_id) REFERENCES wemeet_goal.tasks(plan_version_id, task_id, owner_member_id),
        UNIQUE (group_id, action_id), CHECK (length(btrim(description)) > 0)
    )""",
    """CREATE TABLE wemeet_goal.submission_evidence (
        submission_id TEXT NOT NULL, evidence_id TEXT NOT NULL, original_name TEXT NOT NULL,
        storage_key TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL, size_bytes BIGINT NOT NULL,
        sha256 TEXT NOT NULL, content_kind TEXT NOT NULL, page_count INTEGER,
        extracted_character_count INTEGER NOT NULL DEFAULT 0, image_count INTEGER NOT NULL DEFAULT 0,
        extraction_status TEXT NOT NULL DEFAULT 'READY', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (submission_id, evidence_id),
        FOREIGN KEY (submission_id) REFERENCES wemeet_goal.task_submissions(submission_id) ON DELETE CASCADE,
        CHECK (content_kind IN ('IMAGE','PDF','DOCX')), CHECK (extraction_status IN ('READY','FAILED')),
        CHECK (size_bytes >= 0 AND extracted_character_count >= 0 AND image_count >= 0 AND (page_count IS NULL OR page_count > 0))
    )""",
    """CREATE TABLE wemeet_goal.completion_reviews (
        review_id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
        decision TEXT NOT NULL, summary TEXT NOT NULL, criteria_results JSONB NOT NULL,
        missing_items JSONB NOT NULL, user_message TEXT NOT NULL, model TEXT, prompt_version TEXT,
        run_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (submission_id) REFERENCES wemeet_goal.task_submissions(submission_id),
        CHECK (decision IN ('PASS','REVIEW_NEEDED','NEEDS_INPUT'))
    )""",
    """CREATE TABLE wemeet_goal.completion_appeals (
        appeal_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, submission_id TEXT NOT NULL,
        group_id TEXT NOT NULL, member_id TEXT NOT NULL, action_id TEXT NOT NULL, input_hash TEXT NOT NULL,
        reason TEXT NOT NULL, required_votes INTEGER NOT NULL, eligible_voters INTEGER NOT NULL,
        status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ,
        FOREIGN KEY (review_id) REFERENCES wemeet_goal.completion_reviews(review_id),
        FOREIGN KEY (submission_id) REFERENCES wemeet_goal.task_submissions(submission_id),
        FOREIGN KEY (group_id, member_id) REFERENCES wemeet_goal.members(group_id, member_id),
        UNIQUE (appeal_id, group_id), UNIQUE (group_id, action_id),
        CHECK (status IN ('OPEN','APPROVED','SUPERSEDED')),
        CHECK (required_votes > 0 AND eligible_voters >= 0), CHECK (length(btrim(reason)) > 0)
    )""",
    """CREATE TABLE wemeet_goal.completion_votes (
        appeal_id TEXT NOT NULL, group_id TEXT NOT NULL, voter_member_id TEXT NOT NULL,
        action_id TEXT NOT NULL, input_hash TEXT NOT NULL, approve BOOLEAN NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (appeal_id, voter_member_id),
        FOREIGN KEY (appeal_id, group_id) REFERENCES wemeet_goal.completion_appeals(appeal_id, group_id),
        FOREIGN KEY (group_id, voter_member_id) REFERENCES wemeet_goal.members(group_id, member_id),
        UNIQUE (group_id, action_id)
    )""",
    """CREATE TABLE wemeet_goal.task_progress (
        group_id TEXT NOT NULL, plan_version_id TEXT NOT NULL, task_id TEXT NOT NULL, member_id TEXT NOT NULL,
        status TEXT NOT NULL, latest_submission_id TEXT, latest_review_id TEXT, latest_appeal_id TEXT,
        latest_review_operation_id TEXT, review_input_hash TEXT, review_error_code TEXT, review_error_message TEXT,
        completed_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), revision INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (group_id, plan_version_id, task_id, member_id),
        FOREIGN KEY (group_id, member_id) REFERENCES wemeet_goal.members(group_id, member_id),
        FOREIGN KEY (group_id, plan_version_id) REFERENCES wemeet_goal.plan_versions(group_id, plan_version_id),
        FOREIGN KEY (plan_version_id, task_id, member_id) REFERENCES wemeet_goal.tasks(plan_version_id, task_id, owner_member_id),
        FOREIGN KEY (latest_submission_id) REFERENCES wemeet_goal.task_submissions(submission_id),
        FOREIGN KEY (latest_review_id) REFERENCES wemeet_goal.completion_reviews(review_id),
        FOREIGN KEY (latest_appeal_id) REFERENCES wemeet_goal.completion_appeals(appeal_id),
        CHECK (status IN ('TODO','SUBMITTED','REVIEWING','REVIEW_NEEDED','NEEDS_INPUT','APPEALED','COMPLETED','REVIEW_FAILED')),
        CHECK (revision >= 0)
    )""",
    "CREATE INDEX ix_submission_task ON wemeet_goal.task_submissions(group_id, plan_version_id, task_id, member_id)",
    "CREATE INDEX ix_review_submission ON wemeet_goal.completion_reviews(submission_id, created_at)",
    "CREATE INDEX ix_appeal_submission ON wemeet_goal.completion_appeals(submission_id, created_at)",
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='wemeet_app') THEN
            EXECUTE 'GRANT SELECT, INSERT ON wemeet_goal.task_submissions, wemeet_goal.submission_evidence, wemeet_goal.completion_reviews, wemeet_goal.completion_appeals, wemeet_goal.completion_votes, wemeet_goal.task_progress TO wemeet_app';
            EXECUTE 'GRANT UPDATE ON wemeet_goal.completion_appeals, wemeet_goal.task_progress TO wemeet_app';
        END IF;
    END $$""",
]


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    for table in ('task_progress','completion_votes','completion_appeals','completion_reviews','submission_evidence','task_submissions'):
        op.execute(f'DROP TABLE wemeet_goal.{table}')
    op.execute('ALTER TABLE wemeet_goal.tasks DROP CONSTRAINT uq_tasks_completion_owner')
