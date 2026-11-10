import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData(schema="wemeet_coach")
S = "wemeet_coach."


def col(n, t=sa.Text, **kw):
    return sa.Column(n, t, **kw)


def stamp():
    return col(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


rooms = sa.Table(
    "rooms",
    metadata,
    col("id", primary_key=True),
    col("title", nullable=False),
    col("revision", sa.Integer, nullable=False, server_default="0"),
    col("membership_revision", sa.Integer, nullable=False, server_default="0"),
    col("approved_proposal_id"),
    col("purpose", nullable=False, server_default="USER"),
    stamp(),
)
rooms.append_column(
    col(
        "reference_date", sa.Date, nullable=False, server_default=sa.func.current_date()
    )
)
rooms.append_column(col("timezone", nullable=False, server_default="Asia/Seoul"))
users = sa.Table("users", metadata, col("external_user_id", primary_key=True), stamp())
members = sa.Table(
    "members",
    metadata,
    col("group_id", primary_key=True),
    col("id", primary_key=True),
    col("nickname", nullable=False),
    sa.ForeignKeyConstraint(["group_id"], [S + "rooms.id"]),
)
members.append_column(
    sa.Column(
        "external_user_id",
        sa.Text,
        sa.ForeignKey(S + "users.external_user_id"),
        nullable=True,
    )
)
members.append_constraint(
    sa.UniqueConstraint("group_id", "external_user_id", name="uq_member_external_user")
)
events = sa.Table(
    "events",
    metadata,
    sa.Column("seq", sa.BigInteger, sa.Identity(), primary_key=True),
    col("id", nullable=False, unique=True),
    col("group_id", nullable=False),
    col("owner"),
    col("author"),
    col("kind", nullable=False),
    col("body", JSONB, nullable=False),
    stamp(),
    sa.ForeignKeyConstraint(["group_id"], [S + "rooms.id"]),
    sa.ForeignKeyConstraint(
        ["group_id", "owner"], [S + "members.group_id", S + "members.id"]
    ),
    sa.ForeignKeyConstraint(
        ["group_id", "author"], [S + "members.group_id", S + "members.id"]
    ),
)
proposals = sa.Table(
    "proposals",
    metadata,
    col("id", primary_key=True),
    col("group_id", nullable=False),
    col("version", sa.Integer, nullable=False),
    col("membership_revision", sa.Integer, nullable=False),
    col("electorate", JSONB, nullable=False),
    col("state", nullable=False),
    col("body", JSONB, nullable=False),
    stamp(),
    sa.UniqueConstraint("group_id", "version"),
    sa.UniqueConstraint("group_id", "id"),
    sa.ForeignKeyConstraint(["group_id"], [S + "rooms.id"]),
    sa.CheckConstraint("state IN ('VOTING','REJECTED','APPROVED','SUPERSEDED')"),
)
votes = sa.Table(
    "votes",
    metadata,
    col("proposal_id", primary_key=True),
    col("member_id", primary_key=True),
    col("agree", sa.Boolean, nullable=False),
    col("issue", nullable=False, server_default=""),
    col("reason", nullable=False, server_default=""),
    stamp(),
    sa.ForeignKeyConstraint(["proposal_id"], [S + "proposals.id"]),
)
vote_audit = sa.Table(
    "vote_audit",
    metadata,
    col("action_id", primary_key=True),
    col("group_id", nullable=False),
    col("proposal_id", nullable=False),
    col("member_id", nullable=False),
    col("body", JSONB, nullable=False),
    stamp(),
    sa.ForeignKeyConstraint(
        ["group_id", "proposal_id"], [S + "proposals.group_id", S + "proposals.id"]
    ),
)
private = sa.Table(
    "private_states",
    metadata,
    col("group_id", primary_key=True),
    col("owner", primary_key=True),
    col("revision", sa.Integer, nullable=False, server_default="0"),
    col("proposal_id"),
    col("state", JSONB, nullable=False),
    sa.ForeignKeyConstraint(
        ["group_id", "owner"], [S + "members.group_id", S + "members.id"]
    ),
)
private.append_constraint(
    sa.ForeignKeyConstraint(
        ["group_id", "proposal_id"],
        [S + "proposals.group_id", S + "proposals.id"],
        name="fk_private_proposal",
    )
)
private.append_column(col("current_plan_id"))
operations = sa.Table(
    "operations",
    metadata,
    col("group_id", primary_key=True),
    col("action_id", primary_key=True),
    col("id", nullable=False, unique=True),
    col("module", nullable=False),
    col("owner"),
    col("input_hash", nullable=False),
    col("params", JSONB, nullable=False),
    col("payload", JSONB, nullable=False),
    col("snapshot", JSONB, nullable=False),
    col("state", nullable=False),
    col("result", JSONB),
    stamp(),
    sa.ForeignKeyConstraint(["group_id"], [S + "rooms.id"]),
    sa.ForeignKeyConstraint(
        ["group_id", "owner"], [S + "members.group_id", S + "members.id"]
    ),
    sa.CheckConstraint(
        "state IN ('RUNNING','COMPLETE','STALE','SAVE_PENDING','UNKNOWN')"
    ),
)
goals = sa.Table(
    "goals",
    metadata,
    col("id", primary_key=True),
    col("group_id", nullable=False),
    col("proposal_id", nullable=False, unique=True),
    col("body", JSONB, nullable=False),
    stamp(),
    sa.UniqueConstraint("group_id", "id"),
    sa.ForeignKeyConstraint(
        ["group_id", "proposal_id"], [S + "proposals.group_id", S + "proposals.id"]
    ),
)
personal_goals = sa.Table(
    "personal_goals",
    metadata,
    col("id", primary_key=True),
    col("group_id", nullable=False),
    col("owner", nullable=False),
    col("goal_id", nullable=False),
    col("body", JSONB, nullable=False),
    col("private_conditions", JSONB, nullable=False),
    stamp(),
    sa.UniqueConstraint("group_id", "id", "owner"),
    sa.ForeignKeyConstraint(
        ["group_id", "goal_id"], [S + "goals.group_id", S + "goals.id"]
    ),
    sa.ForeignKeyConstraint(
        ["group_id", "owner"], [S + "members.group_id", S + "members.id"]
    ),
)
plans = sa.Table(
    "plans",
    metadata,
    col("id", primary_key=True),
    col("group_id", nullable=False),
    col("goal_id", nullable=False),
    col("owner"),
    col("personal_goal_id"),
    col("body", JSONB, nullable=False),
    stamp(),
    sa.UniqueConstraint("group_id", "id"),
    sa.ForeignKeyConstraint(
        ["group_id", "goal_id"], [S + "goals.group_id", S + "goals.id"]
    ),
    sa.ForeignKeyConstraint(
        ["group_id", "personal_goal_id", "owner"],
        [
            S + "personal_goals.group_id",
            S + "personal_goals.id",
            S + "personal_goals.owner",
        ],
    ),
)
plans.append_constraint(
    sa.ForeignKeyConstraint(
        ["group_id", "owner"],
        [S + "members.group_id", S + "members.id"],
        name="fk_plan_owner",
    )
)
plans.append_constraint(
    sa.CheckConstraint(
        "(owner IS NULL AND personal_goal_id IS NULL) OR (owner IS NOT NULL AND personal_goal_id IS NOT NULL)",
        name="ck_plan_scope",
    )
)
plans.append_constraint(
    sa.UniqueConstraint("group_id", "id", "owner", name="uq_owned_plan")
)
private.append_constraint(
    sa.ForeignKeyConstraint(
        ["group_id", "current_plan_id", "owner"],
        [S + "plans.group_id", S + "plans.id", S + "plans.owner"],
        name="fk_current_personal_plan",
        use_alter=True,
    )
)
tasks = sa.Table(
    "tasks",
    metadata,
    col("id", primary_key=True),
    col("group_id", nullable=False),
    col("plan_id", nullable=False),
    col("owner"),
    col("body", JSONB, nullable=False),
    sa.ForeignKeyConstraint(
        ["group_id", "plan_id"], [S + "plans.group_id", S + "plans.id"]
    ),
    sa.ForeignKeyConstraint(
        ["group_id", "owner"], [S + "members.group_id", S + "members.id"]
    ),
)
tasks.append_constraint(
    sa.CheckConstraint(
        "id IS NOT DISTINCT FROM body->>'task_id' AND owner IS NOT DISTINCT FROM body->>'owner_member_id'",
        name="ck_task_body_identity",
    )
)
baselines = sa.Table(
    "baselines",
    metadata,
    col("id", primary_key=True),
    col("group_id", nullable=False),
    col("plan_id", nullable=False, unique=True),
    col("owner"),
    col("scope", nullable=False, server_default="PERSONAL"),
    col("public_summary", JSONB, nullable=False),
    stamp(),
    sa.ForeignKeyConstraint(
        ["group_id", "plan_id"], [S + "plans.group_id", S + "plans.id"]
    ),
    sa.ForeignKeyConstraint(
        ["group_id", "owner"], [S + "members.group_id", S + "members.id"]
    ),
    sa.CheckConstraint(
        "(scope='GROUP' AND owner IS NULL) OR (scope='PERSONAL' AND owner IS NOT NULL)",
        name="ck_baseline_scope",
    ),
)
rooms.append_constraint(
    sa.ForeignKeyConstraint(
        ["id", "approved_proposal_id"],
        [S + "proposals.group_id", S + "proposals.id"],
        name="fk_current_proposal",
        use_alter=True,
        deferrable=True,
        initially="DEFERRED",
    )
)
actions = sa.Table(
    "actions",
    metadata,
    col("group_id", primary_key=True),
    col("action_id", primary_key=True),
    col("input_hash", nullable=False),
    col("result", JSONB, nullable=False),
    sa.ForeignKeyConstraint(["group_id"], [S + "rooms.id"]),
)
sa.Index("ix_events_group", events.c.group_id, events.c.seq)

sa.Index(
    "uq_group_goal_plan",
    plans.c.group_id,
    plans.c.goal_id,
    unique=True,
    postgresql_where=plans.c.owner.is_(None),
)
