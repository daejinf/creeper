"""stage 4 session C: role reassignment requests (leader request + target consent, D-B1 B4)

Revision ID: 0010
Revises: 0009

Only a new table. The STALE columns it relies on already exist (0008: role_assignments.state/stale_reason/stale_at,
team_coverages/role_interviews/role_interview_snapshots.stale_at; 0009: todo_drafts/todo_confirmations.stale_at).
Downgrade drops only this table; assignment revisions made by a reassignment stay (operation REASSIGN).
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_reassignments",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("target_user_id", sa.String(36), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("request_action_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("response_action_id", sa.String(64), nullable=True),
        sa.Column("result_assignment_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_reassignments")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_role_reassignments_group_id_groups")),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], name=op.f("fk_role_reassignments_target_user_id_users")),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_role_reassignments_requested_by_users")),
        sa.ForeignKeyConstraint(
            ["group_id", "source_assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"],
            name=op.f("fk_role_reassignments_group_id_role_assignments"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "result_assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"],
            name=op.f("fk_role_reassignments_result_assignment_revision"),
        ),
        sa.UniqueConstraint("group_id", "requested_by", "request_action_id", name=op.f("uq_role_reassignments_action")),
        sa.CheckConstraint(
            "state in ('REQUESTED','ACCEPTED','DECLINED','SUPERSEDED')", name=op.f("ck_role_reassignments_state")
        ),
        sa.CheckConstraint(
            "(state = 'ACCEPTED') = (result_assignment_revision is not null)",
            name=op.f("ck_role_reassignments_result_iff_accepted"),
        ),
        sa.CheckConstraint(
            "(state = 'REQUESTED') = (decided_at is null)", name=op.f("ck_role_reassignments_decided_unless_requested")
        ),
    )
    op.create_index(
        "uq_role_reassignments_open", "role_reassignments", ["group_id"], unique=True,
        postgresql_where=sa.text("state = 'REQUESTED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_role_reassignments_open", table_name="role_reassignments")
    op.drop_table("role_reassignments")
