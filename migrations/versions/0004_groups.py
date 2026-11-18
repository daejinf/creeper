"""groups: invite rooms, members, invite codes, ProjectBrief revisions, activation cohorts, team goal displays

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cohort_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("brief_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_groups_owner_id_users")),
        sa.CheckConstraint("status in ('FORMING','CANCELLED','ACTIVE')", name=op.f("ck_groups_status")),
        sa.CheckConstraint(
            "(status = 'ACTIVE' and cohort_revision >= 1 and brief_revision >= 1 and activated_at is not null)"
            " or (status <> 'ACTIVE' and cohort_revision = 0 and brief_revision = 0)",
            name=op.f("ck_groups_active_consistency"),
        ),
    )
    op.create_index("ix_groups_owner_id", "groups", ["owner_id"])
    op.create_index(
        "uq_groups_owner_forming", "groups", ["owner_id"], unique=True, postgresql_where=sa.text("status = 'FORMING'")
    )

    op.create_table(
        "group_members",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_group_members")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_group_members_group_id_groups")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_group_members_user_id_users")),
        sa.CheckConstraint("role in ('LEADER','MEMBER')", name=op.f("ck_group_members_role")),
    )
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])
    op.create_index(
        "uq_group_members_leader", "group_members", ["group_id"], unique=True, postgresql_where=sa.text("role = 'LEADER'")
    )

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite_codes")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_invite_codes_group_id_groups")),
        sa.UniqueConstraint("code", name=op.f("uq_invite_codes_code")),
        sa.CheckConstraint("code ~ '^[0-9]{6}$'", name=op.f("ck_invite_codes_code")),
        sa.CheckConstraint(
            "revoke_reason is null or revoke_reason in ('REISSUED','ACTIVATED','CANCELLED')",
            name=op.f("ck_invite_codes_revoke_reason"),
        ),
        sa.CheckConstraint("(revoked_at is null) = (revoke_reason is null)", name=op.f("ck_invite_codes_revoked_pair")),
    )
    op.create_index(
        "uq_invite_codes_live", "invite_codes", ["group_id"], unique=True, postgresql_where=sa.text("revoked_at is null")
    )

    op.create_table(
        "project_briefs",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("deliverable_types", postgresql.JSONB(), nullable=False),
        sa.Column("milestones", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "revision", name=op.f("pk_project_briefs")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_project_briefs_group_id_groups")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_project_briefs_created_by_users")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_project_briefs_revision")),
        sa.CheckConstraint("char_length(name) between 1 and 30", name=op.f("ck_project_briefs_name")),
        sa.CheckConstraint("char_length(content) <= 1000", name=op.f("ck_project_briefs_content")),
        sa.CheckConstraint("end_date >= start_date", name=op.f("ck_project_briefs_period")),
    )

    op.create_table(
        "activation_cohorts",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("cohort_revision", sa.Integer(), nullable=False),
        sa.Column("activated_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "cohort_revision", name=op.f("pk_activation_cohorts")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_activation_cohorts_group_id_groups")),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"], name=op.f("fk_activation_cohorts_activated_by_users")),
        sa.CheckConstraint("cohort_revision >= 1", name=op.f("ck_activation_cohorts_revision")),
    )

    op.create_table(
        "cohort_members",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("cohort_revision", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "cohort_revision", "user_id", name=op.f("pk_cohort_members")),
        sa.ForeignKeyConstraint(
            ["group_id", "cohort_revision"],
            ["activation_cohorts.group_id", "activation_cohorts.cohort_revision"],
            name=op.f("fk_cohort_members_group_id_activation_cohorts"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_cohort_members_user_id_users")),
    )

    op.create_table(
        "goal_displays",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("brief_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("text", sa.String(200), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "brief_revision", name=op.f("pk_goal_displays")),
        sa.ForeignKeyConstraint(
            ["group_id", "brief_revision"],
            ["project_briefs.group_id", "project_briefs.revision"],
            name=op.f("fk_goal_displays_group_id_project_briefs"),
        ),
        sa.CheckConstraint(
            "state in ('EMPTY','PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_goal_displays_state")
        ),
        sa.CheckConstraint("(state = 'SUCCEEDED') = (text is not null)", name=op.f("ck_goal_displays_text_iff_succeeded")),
    )


def downgrade() -> None:
    op.drop_table("goal_displays")
    op.drop_table("cohort_members")
    op.drop_table("activation_cohorts")
    op.drop_table("project_briefs")
    op.drop_index("uq_invite_codes_live", table_name="invite_codes")
    op.drop_table("invite_codes")
    op.drop_index("uq_group_members_leader", table_name="group_members")
    op.drop_index("ix_group_members_user_id", table_name="group_members")
    op.drop_table("group_members")
    op.drop_index("uq_groups_owner_forming", table_name="groups")
    op.drop_index("ix_groups_owner_id", table_name="groups")
    op.drop_table("groups")
