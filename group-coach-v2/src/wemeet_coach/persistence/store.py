"""PostgreSQL application adapter. AI functions never import this module."""

import json
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from . import schema as d
from .recovery import EncryptedRecoveryStore
from ..shared.contracts import (
    GroupRequest,
    GroupProposal,
    PersonalRequest,
    PersonalState,
    PersonalPlan,
    PersonalReply,
    Message,
    Member,
    Error,
)
from ..shared.privacy import sanitize
from ..m0.runtime import code_result
from ..group import group_coach
from ..personal import personal_coach
from ..validation import validate_plan, assign_ids, confirmed_weekdays


class Conflict(ValueError):
    pass


class NotFound(LookupError):
    pass


def uid():
    return str(uuid4())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


class Store:
    def __init__(self, url, recovery_path, recovery_key):
        if not url:
            raise ValueError("WEMEET_COACH_DATABASE_URL required; no old DB fallback")
        from sqlalchemy.engine import make_url

        try:
            u = make_url(url)
        except sa.exc.ArgumentError:
            raise ValueError("PostgreSQL 접속 주소 형식을 확인해주세요.") from None
        if u.drivername.split("+")[0] != "postgresql" or not u.database:
            raise ValueError("DB 이름을 포함한 명시적인 PostgreSQL 주소가 필요합니다.")
        self.engine = sa.create_engine(
            url,
            hide_parameters=True,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 4},
        )
        self.recovery = EncryptedRecoveryStore(recovery_path, recovery_key)

    @contextmanager
    def tx(self, gid=None, revision=None):
        with self.engine.begin() as c:
            row = None
            if gid:
                row = (
                    c.execute(
                        sa.select(d.rooms).where(d.rooms.c.id == gid).with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if not row:
                    raise NotFound("모임을 찾지 못했습니다.")
                if revision is not None and revision != row["revision"]:
                    raise Conflict(
                        "모임 정보가 갱신됐습니다. 최신 화면에서 다시 시도해주세요."
                    )
            yield c, row

    @contextmanager
    def read(self, gid=None):
        """One non-blocking, consistent snapshot. No writes or row locks."""
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as c:
            with c.begin():
                c.exec_driver_sql("SET TRANSACTION READ ONLY")
                row = None
                if gid is not None:
                    row = (
                        c.execute(sa.select(d.rooms).where(d.rooms.c.id == gid))
                        .mappings()
                        .first()
                    )
                    if row is None:
                        raise NotFound("모임을 찾지 못했습니다.")
                yield c, row

    def touch(self, c, gid):
        c.execute(
            d.rooms.update()
            .where(d.rooms.c.id == gid)
            .values(revision=d.rooms.c.revision + 1)
        )

    def member(self, c, gid, owner):
        if not c.scalar(
            sa.select(d.members.c.id).where(
                d.members.c.group_id == gid, d.members.c.id == owner
            )
        ):
            raise ValueError("해당 모임의 구성원이 아닙니다.")

    def members(self, c, gid):
        return [
            dict(r)
            for r in c.execute(
                sa.select(d.members)
                .where(d.members.c.group_id == gid)
                .order_by(d.members.c.id)
            ).mappings()
        ]

    def event(self, c, gid, kind, body, *, owner=None, author=None, event_id=None):
        if owner:
            self.member(c, gid, owner)
        if author:
            self.member(c, gid, author)
        eid = event_id or uid()
        body = sanitize(body)
        old = (
            c.execute(sa.select(d.events).where(d.events.c.id == eid))
            .mappings()
            .first()
        )
        if old:
            if (
                old["group_id"] != gid
                or old["owner"] != owner
                or old["author"] != author
                or old["kind"] != kind
                or old["body"] != body
            ):
                raise Conflict("같은 메시지 ID의 내용이 다릅니다.")
            return False
        c.execute(
            d.events.insert().values(
                id=eid, group_id=gid, kind=kind, body=body, owner=owner, author=author
            )
        )
        self.touch(c, gid)
        return True

    def create(
        self,
        title,
        members,
        *,
        purpose="USER",
        reference_date=None,
        timezone_name="Asia/Seoul",
    ):
        members = [Member.model_validate(m).model_dump() for m in members]
        if not members or len({m["member_id"] for m in members}) != len(members):
            raise ValueError("한 명 이상의 서로 다른 구성원이 필요합니다.")
        external = [m["external_user_id"] for m in members if m.get("external_user_id")]
        if len(external) != len(set(external)):
            raise ValueError(
                "한 사용자를 같은 모임의 여러 구성원에 연결할 수 없습니다."
            )
        gid = uid()
        with self.tx() as (c, _):
            from datetime import date

            c.execute(
                d.rooms.insert().values(
                    id=gid,
                    title=title,
                    purpose=purpose,
                    reference_date=(
                        date.fromisoformat(reference_date)
                        if reference_date
                        else date.today()
                    ),
                    timezone=timezone_name,
                )
            )
            for m in members:
                if m.get("external_user_id"):
                    c.execute(
                        insert(d.users)
                        .values(external_user_id=m["external_user_id"])
                        .on_conflict_do_nothing()
                    )
                c.execute(
                    d.members.insert().values(
                        group_id=gid,
                        id=m["member_id"],
                        nickname=m["nickname"],
                        external_user_id=m.get("external_user_id"),
                    )
                )
            self.event(
                c,
                gid,
                "NOTICE",
                {
                    "text": "모임에서 함께 이루고 싶은 목표를 이야기해주세요. AI는 요청할 때 실제 API로 코칭합니다."
                },
            )
        return self.view(gid)

    def list(self):
        with self.tx() as (c, _):
            return [
                dict(r)
                for r in c.execute(
                    sa.select(
                        d.rooms.c.id,
                        d.rooms.c.title,
                        d.rooms.c.purpose,
                        d.rooms.c.created_at,
                    ).order_by(d.rooms.c.created_at.desc())
                ).mappings()
            ]

    def add_member(self, gid, member, action_id):
        member = Member.model_validate(member)

        def run(c, row):
            old = (
                c.execute(
                    sa.select(d.members).where(
                        d.members.c.group_id == gid, d.members.c.id == member.member_id
                    )
                )
                .mappings()
                .first()
            )
            if old:
                raise Conflict("같은 구성원 ID가 이미 있습니다.")
            if member.external_user_id and c.scalar(
                sa.select(d.members.c.id).where(
                    d.members.c.group_id == gid,
                    d.members.c.external_user_id == member.external_user_id,
                )
            ):
                raise Conflict("이미 이 모임에 연결된 사용자입니다.")
            if member.external_user_id:
                c.execute(
                    insert(d.users)
                    .values(external_user_id=member.external_user_id)
                    .on_conflict_do_nothing()
                )
            c.execute(
                d.members.insert().values(
                    group_id=gid,
                    id=member.member_id,
                    nickname=member.nickname,
                    external_user_id=member.external_user_id,
                )
            )
            self.close_votes(c, gid, supersede=True)
            c.execute(
                d.rooms.update()
                .where(d.rooms.c.id == gid)
                .values(
                    membership_revision=row["membership_revision"] + 1,
                    approved_proposal_id=None,
                )
            )
            self.event(
                c,
                gid,
                "MEMBER_ADDED",
                {
                    "text": member.nickname
                    + "님이 참여했습니다. 현재 구성원으로 공동 계획을 다시 제안·합의해주세요."
                },
            )
            return {"saved": True}

        return self.action(gid, action_id, {"add_member": member.model_dump()}, run)

    def message(self, gid, owner, text, message_id, *, private=False):
        with self.tx(gid) as (c, row):
            self.member(c, gid, owner)
            created = self.event(
                c,
                gid,
                "MESSAGE",
                {"text": text},
                owner=owner if private else None,
                author=owner,
                event_id=message_id,
            )
            if private and created:
                self.private_row(c, gid, owner)
                c.execute(
                    d.private.update()
                    .where(d.private.c.group_id == gid, d.private.c.owner == owner)
                    .values(revision=d.private.c.revision + 1)
                )
        return self.personal_view(gid, owner) if private else self.view(gid)

    def action(self, gid, action_id, params, runner):
        if not action_id:
            raise ValueError("action_id required")
        signature = digest(params)
        with self.tx(gid) as (c, row):
            old = (
                c.execute(
                    sa.select(d.actions).where(
                        d.actions.c.group_id == gid, d.actions.c.action_id == action_id
                    )
                )
                .mappings()
                .first()
            )
            if old:
                if old["input_hash"] != signature:
                    raise Conflict("같은 동작 ID의 입력이 다릅니다.")
                return old["result"]
            result = runner(c, row)
            c.execute(
                d.actions.insert().values(
                    group_id=gid,
                    action_id=action_id,
                    input_hash=signature,
                    result=result,
                )
            )
            return result

    def proposal(self, c, gid, pid=None):
        q = sa.select(d.proposals).where(d.proposals.c.group_id == gid)
        return (
            c.execute(
                q.where(d.proposals.c.id == pid)
                if pid
                else q.order_by(d.proposals.c.version.desc()).limit(1)
            )
            .mappings()
            .first()
        )

    def proposal_view(self, c, p):
        if not p:
            return None
        votes = [
            dict(v)
            for v in c.execute(
                sa.select(d.votes).where(d.votes.c.proposal_id == p["id"])
            ).mappings()
        ]
        result = {
            "id": p["id"],
            "version": p["version"],
            "state": p["state"],
            "body": p["body"],
            "electorate": p["electorate"],
            "participation": len(votes),
            "total": len(p["electorate"]),
        }
        if p["state"] == "VOTING":
            issues = []
            for v in votes:
                if v["agree"]:
                    continue
                reason = v["reason"]
                for m in p["electorate"]:
                    reason = reason.replace(m["nickname"], "구성원").replace(
                        m["member_id"], "구성원"
                    )
                issues.append({"issue": v["issue"], "reason": reason})
            result["issues"] = issues
        else:
            names = {m["member_id"]: m["nickname"] for m in p["electorate"]}
            result["votes"] = [
                {
                    "nickname": names[v["member_id"]],
                    "agree": v["agree"],
                    "issue": v["issue"],
                    "reason": v["reason"],
                }
                for v in votes
            ]
        return result

    def close_votes(self, c, gid, *, supersede=False):
        p = self.proposal(c, gid)
        if p and p["state"] == "VOTING":
            state = "SUPERSEDED" if supersede else "REJECTED"
            c.execute(
                d.proposals.update()
                .where(d.proposals.c.id == p["id"])
                .values(state=state)
            )
            p = dict(p)
            p["state"] = state
            self.event(
                c,
                gid,
                "VOTE_RESULT",
                {
                    "proposal": self.proposal_view(c, p),
                    "text": "이 버전의 투표를 종료했습니다. 이전 찬성은 수정안에 승계되지 않습니다.",
                },
            )

    def publish_proposal(self, c, row, body):
        proposal = GroupProposal.model_validate(body)
        if any(
            not t.task_id
            or c.scalar(sa.select(d.tasks.c.id).where(d.tasks.c.id == t.task_id))
            for t in proposal.tasks
        ):
            assign_ids(proposal)
        mids = {m["id"] for m in self.members(c, row["id"])}
        if set(proposal.goal.roles) != mids:
            raise ValueError("현재 구성원의 역할이 모두 필요합니다.")
        check = validate_plan(proposal, proposal.goal, members=mids)
        if not check["valid"]:
            raise ValueError("; ".join(check["issues"]))
        self.close_votes(c, row["id"], supersede=True)
        last = self.proposal(c, row["id"])
        pid = uid()
        version = last["version"] + 1 if last else 1
        electorate = [
            {"member_id": m["id"], "nickname": m["nickname"]}
            for m in self.members(c, row["id"])
        ]
        c.execute(
            d.proposals.insert().values(
                id=pid,
                group_id=row["id"],
                version=version,
                membership_revision=row["membership_revision"],
                electorate=electorate,
                state="VOTING",
                body=proposal.model_dump(mode="json"),
            )
        )
        self.event(
            c,
            row["id"],
            "GROUP_PROPOSAL",
            {
                "proposal_id": pid,
                "version": version,
                "proposal": proposal.model_dump(mode="json"),
                "text": "공동 계획 제안입니다. 현재 구성원 전원의 찬성이 필요합니다.",
            },
        )
        return pid

    def edit_proposal(self, gid, action_id, actor, body, expected_revision):
        def run(c, row):
            if row["revision"] != expected_revision:
                raise Conflict("최신 제안에서 수정해주세요.")
            self.member(c, gid, actor)
            pid = self.publish_proposal(c, row, body)
            return {"proposal_id": pid}

        return self.action(gid, action_id, {"actor": actor, "body": body}, run)

    def vote(self, gid, pid, actor, agree, issue, reason, action_id):
        if not isinstance(issue, str) or not isinstance(reason, str):
            raise ValueError("반대 항목과 이유는 문자열이어야 합니다.")
        if type(agree) != bool:
            raise ValueError("agree는 bool(True/False)이어야 합니다.")
        params = {
            "pid": pid,
            "actor": actor,
            "agree": agree,
            "issue": issue,
            "reason": reason,
        }

        def run(c, row):
            p = self.proposal(c, gid, pid)
            if (
                not p
                or p["state"] != "VOTING"
                or p["membership_revision"] != row["membership_revision"]
            ):
                raise Conflict("현재 투표할 수 있는 제안이 아닙니다.")
            if actor not in {m["member_id"] for m in p["electorate"]}:
                raise ValueError("투표 대상이 아닙니다.")
            if not agree and not reason.strip():
                raise ValueError(
                    "반대 이유를 알려주세요. 개인 사유는 개인 상담에서 다뤄주세요."
                )
            c.execute(
                insert(d.votes)
                .values(
                    proposal_id=pid,
                    member_id=actor,
                    agree=agree,
                    issue=issue,
                    reason=sanitize(reason),
                )
                .on_conflict_do_update(
                    index_elements=["proposal_id", "member_id"],
                    set_={"agree": agree, "issue": issue, "reason": sanitize(reason)},
                )
            )
            c.execute(
                d.vote_audit.insert().values(
                    action_id=gid + ":" + action_id,
                    group_id=gid,
                    proposal_id=pid,
                    member_id=actor,
                    body=sanitize(params),
                )
            )
            all_votes = [
                v.agree
                for v in c.execute(
                    sa.select(d.votes).where(d.votes.c.proposal_id == pid)
                )
            ]
            if len(all_votes) == len(p["electorate"]):
                state = "APPROVED" if all(all_votes) else "REJECTED"
                c.execute(
                    d.proposals.update()
                    .where(d.proposals.c.id == pid)
                    .values(state=state)
                )
                p = dict(p)
                p["state"] = state
                if state == "APPROVED":
                    self.commit_group(c, row, p)
                self.event(
                    c,
                    gid,
                    "VOTE_RESULT",
                    {
                        "proposal": self.proposal_view(c, p),
                        "text": (
                            "전원 찬성으로 공동 계획을 확정했습니다."
                            if state == "APPROVED"
                            else "반대 의견을 반영한 새 제안이 필요합니다."
                        ),
                    },
                )
            else:
                self.event(
                    c,
                    gid,
                    "VOTE_PROGRESS",
                    {
                        "proposal_id": pid,
                        "participation": len(all_votes),
                        "total": len(p["electorate"]),
                        "text": "투표를 저장했습니다. 종료 전에는 작성자별 선택을 공개하지 않습니다.",
                    },
                )
            return {"saved": True, "state": p["state"]}

        return self.action(gid, action_id, params, run)

    def commit_group(self, c, row, p):
        goal_id = uid()
        plan_id = uid()
        body = p["body"]
        c.execute(
            d.goals.insert().values(
                id=goal_id, group_id=row["id"], proposal_id=p["id"], body=body["goal"]
            )
        )
        c.execute(
            d.plans.insert().values(
                id=plan_id, group_id=row["id"], goal_id=goal_id, body=body
            )
        )
        for task in body["tasks"]:
            c.execute(
                d.tasks.insert().values(
                    id=task["task_id"],
                    group_id=row["id"],
                    plan_id=plan_id,
                    owner=task["owner_member_id"],
                    body=task,
                )
            )
        c.execute(
            d.rooms.update()
            .where(d.rooms.c.id == row["id"])
            .values(approved_proposal_id=p["id"])
        )
        c.execute(
            d.baselines.insert().values(
                id=uid(),
                group_id=row["id"],
                plan_id=plan_id,
                owner=None,
                scope="GROUP",
                public_summary=body,
            )
        )

    def link_user(self, gid, member_id, external_user_id, action_id):
        """Trusted SW provisioning only; never infer identity from a nickname."""
        if not isinstance(external_user_id, str) or not external_user_id.strip():
            raise ValueError("고정 사용자 ID가 필요합니다.")

        def run(c, row):
            member = (
                c.execute(
                    sa.select(d.members).where(
                        d.members.c.group_id == gid, d.members.c.id == member_id
                    )
                )
                .mappings()
                .first()
            )
            if not member:
                raise NotFound("구성원이 없습니다.")
            if (
                member["external_user_id"]
                and member["external_user_id"] != external_user_id
            ):
                raise Conflict("기존 사용자 연결은 자동으로 덮어쓰지 않습니다.")
            existing = c.scalar(
                sa.select(d.members.c.id).where(
                    d.members.c.group_id == gid,
                    d.members.c.external_user_id == external_user_id,
                )
            )
            if existing and existing != member_id:
                raise Conflict(
                    "이 사용자는 이미 이 모임의 다른 구성원에 연결돼 있습니다."
                )
            c.execute(
                insert(d.users)
                .values(external_user_id=external_user_id)
                .on_conflict_do_nothing()
            )
            c.execute(
                d.members.update()
                .where(d.members.c.group_id == gid, d.members.c.id == member_id)
                .values(external_user_id=external_user_id)
            )
            return {"linked": True}

        return self.action(
            gid,
            action_id,
            {"link_member": member_id, "external_user_id": external_user_id},
            run,
        )

    def reader(self, actor):
        from .reader import GoalReader

        return GoalReader(self, actor)

    def approved(self, c, row):
        if not row["approved_proposal_id"]:
            raise ValueError("먼저 공동 계획에 전원 찬성해주세요.")
        p = self.proposal(c, row["id"], row["approved_proposal_id"])
        if (
            not p
            or p["state"] != "APPROVED"
            or p["membership_revision"] != row["membership_revision"]
        ):
            raise Conflict("현재 구성원으로 공동 계획을 재확인해주세요.")
        return p

    def private_row(self, c, gid, owner):
        self.member(c, gid, owner)
        c.execute(
            insert(d.private)
            .values(
                group_id=gid, owner=owner, state=PersonalState().model_dump(mode="json")
            )
            .on_conflict_do_nothing()
        )
        return (
            c.execute(
                sa.select(d.private).where(
                    d.private.c.group_id == gid, d.private.c.owner == owner
                )
            )
            .mappings()
            .one()
        )

    def dialogue(self, c, gid, owner=None):
        q = (
            sa.select(d.events).where(
                d.events.c.group_id == gid, d.events.c.owner == owner
            )
            if owner
            else sa.select(d.events).where(
                d.events.c.group_id == gid, d.events.c.owner.is_(None)
            )
        )
        messages = []
        for r in c.execute(q.order_by(d.events.c.seq)).mappings():
            if r["kind"] in {"MESSAGE", "AI_REPLY"}:
                messages.append(
                    Message(
                        message_id=r["id"],
                        speaker_id=r["author"],
                        role="user" if r["kind"] == "MESSAGE" else "assistant",
                        text=r["body"]["text"],
                        created_at=r["created_at"],
                    ).model_dump(mode="json")
                )
        return messages

    def view(self, gid):
        with self.tx(gid) as (c, row):
            events = [
                {**dict(r), "created_at": r["created_at"].isoformat()}
                for r in c.execute(
                    sa.select(d.events)
                    .where(d.events.c.group_id == gid, d.events.c.owner.is_(None))
                    .order_by(d.events.c.seq)
                ).mappings()
            ]
            run = (
                c.execute(
                    sa.select(d.operations)
                    .where(
                        d.operations.c.group_id == gid,
                        d.operations.c.owner.is_(None),
                        d.operations.c.result.is_not(None),
                    )
                    .order_by(d.operations.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            return {
                "group_id": gid,
                "title": row["title"],
                "revision": row["revision"],
                "members": [
                    {"member_id": m["id"], "nickname": m["nickname"]}
                    for m in self.members(c, gid)
                ],
                "events": events,
                "proposal": self.proposal_view(c, self.proposal(c, gid)),
                "approved_proposal_id": row["approved_proposal_id"],
                "last_run": (
                    {"request": run["payload"], "response": run["result"]}
                    if run
                    else None
                ),
            }

    def personal_view(self, gid, owner, *, initialize=True):
        transaction = self.tx if initialize else self.read
        with transaction(gid) as (c, row):
            return self._personal_view(c, row, gid, owner, initialize=initialize)

    def _personal_view(self, c, row, gid, owner, *, initialize=False):
        self.member(c, gid, owner)
        state = (
            self.private_row(c, gid, owner)
            if initialize
            else c.execute(
                sa.select(d.private).where(
                    d.private.c.group_id == gid, d.private.c.owner == owner
                )
            )
            .mappings()
            .first()
        )
        state = state or {
            "state": PersonalState().model_dump(mode="json"),
            "revision": 0,
            "current_plan_id": None,
        }
        p = (
            self.proposal(c, gid, row["approved_proposal_id"])
            if row["approved_proposal_id"]
            else None
        )
        if (
            initialize
            and p
            and not state["state"].get("fields")
            and not state["state"].get("question")
        ):
            from ..personal import opening_question

            initial = PersonalState.model_validate(state["state"])
            initial.question = opening_question(p["body"]["goal"]["roles"][owner])
            c.execute(
                d.private.update()
                .where(d.private.c.group_id == gid, d.private.c.owner == owner)
                .values(state=initial.model_dump(mode="json"), proposal_id=p["id"])
            )
            state = self.private_row(c, gid, owner)
        plans = [
            dict(r)
            for r in c.execute(
                sa.select(d.plans)
                .where(d.plans.c.group_id == gid, d.plans.c.owner == owner)
                .order_by(d.plans.c.created_at.desc())
            ).mappings()
        ]
        events = [
            {**dict(r), "created_at": r["created_at"].isoformat()}
            for r in c.execute(
                sa.select(d.events)
                .where(d.events.c.group_id == gid, d.events.c.owner == owner)
                .order_by(d.events.c.seq)
            ).mappings()
        ]

        def stale(plan):
            conditions = (
                c.scalar(
                    sa.select(d.personal_goals.c.private_conditions).where(
                        d.personal_goals.c.id == plan["personal_goal_id"]
                    )
                )
                or {}
            )
            return (
                plan["id"] != state.get("current_plan_id")
                or not row["approved_proposal_id"]
                or plan["goal_id"]
                != c.scalar(
                    sa.select(d.goals.c.id).where(
                        d.goals.c.proposal_id == row["approved_proposal_id"]
                    )
                )
                or conditions.get("_source_private_revision") != state["revision"]
            )

        run = (
            c.execute(
                sa.select(d.operations)
                .where(
                    d.operations.c.group_id == gid,
                    d.operations.c.owner == owner,
                    d.operations.c.result.is_not(None),
                )
                .order_by(d.operations.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return {
            "group_id": gid,
            "owner": owner,
            "revision": row["revision"],
            "current_plan_id": state.get("current_plan_id"),
            "state": state["state"],
            "goal": p["body"]["goal"] if p else None,
            "proposal_id": p["id"] if p else None,
            "events": events,
            "plans": [
                {
                    **plan,
                    "created_at": plan["created_at"].isoformat(),
                    "stale": stale(plan),
                }
                for plan in plans
            ],
            "last_run": (
                {"request": run["payload"], "response": run["result"]} if run else None
            ),
        }

    @staticmethod
    def public_dependencies(tasks, owner):
        by_id = {task["task_id"]: task for task in tasks}
        needed = {
            dependency
            for task in tasks
            if task.get("owner_member_id") == owner
            for dependency in task.get("dependencies", [])
        }
        visited = set()
        while needed - visited:
            key = next(iter(needed - visited))
            visited.add(key)
            if key in by_id:
                needed.update(by_id[key].get("dependencies", []))
        return [
            task
            for task in tasks
            if task["task_id"] in visited and task.get("owner_member_id") != owner
        ]

    def execute(
        self,
        gid,
        action_id,
        module,
        params,
        expected_revision,
        *,
        owner=None,
        runtime=None,
        runner=None,
    ):
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("비어 있지 않은 action_id가 필요합니다.")
        if not isinstance(params, dict):
            raise ValueError("params는 객체여야 합니다.")
        if not isinstance(params.get("text", ""), str):
            raise ValueError("답변 text는 문자열이어야 합니다.")
        if params.get("answer") is not None and not isinstance(params["answer"], dict):
            raise ValueError("answer는 현재 질문의 선택 객체여야 합니다.")
        if module not in {
            "group.COACH",
            "group.PROPOSE",
            "group.REVISE",
            "personal.TALK",
            "personal.PLAN",
        }:
            raise ValueError("알 수 없는 작업")
        if module.startswith("personal.") != bool(owner):
            raise ValueError("공개/개인 범위가 다릅니다.")
        signature = digest([module, owner, sanitize(params)])
        claimed = False
        with self.tx(gid) as (c, row):
            old = (
                c.execute(
                    sa.select(d.operations).where(
                        d.operations.c.group_id == gid,
                        d.operations.c.action_id == action_id,
                    )
                )
                .mappings()
                .first()
            )
            if old:
                if old["input_hash"] != signature:
                    raise Conflict("같은 동작 ID의 입력이 다릅니다.")
                op = dict(old)
                if op["result"] and op["state"] in {"COMPLETE", "STALE"}:
                    return {**op["result"], "reused": True, "new_http": 0}
            else:
                if expected_revision != row["revision"]:
                    raise Conflict("최신 기록으로 실행해주세요.")
                if owner:
                    p = self.approved(c, row)
                    private = self.private_row(c, gid, owner)
                    if private["proposal_id"] != p["id"]:
                        state = PersonalState.model_validate(private["state"])
                        state.question = None
                        state.fields.pop("scope", None)
                        c.execute(
                            d.private.update()
                            .where(
                                d.private.c.group_id == gid, d.private.c.owner == owner
                            )
                            .values(
                                state=state.model_dump(mode="json"),
                                proposal_id=p["id"],
                                revision=private["revision"] + 1,
                            )
                        )
                        private = self.private_row(c, gid, owner)
                    answer = params.get("answer")
                    text = params.get("text", "").strip()
                    message_id = params.get("message_id")
                    if answer:
                        state = PersonalState.model_validate(private["state"])
                        q = state.question
                        if not q or answer.get("question_id") != q.question_id:
                            raise Conflict("이전 질문은 이미 답했거나 바뀌었습니다.")
                        option = next(
                            (
                                o
                                for o in q.options
                                if o.option_id == answer.get("option_id")
                            ),
                            None,
                        )
                        if not option:
                            raise ValueError("잘못된 선택지")
                        text = option.label
                    if text:
                        if not message_id:
                            raise ValueError("message_id required")
                        inserted = self.event(
                            c,
                            gid,
                            "MESSAGE",
                            {"text": text},
                            owner=owner,
                            author=owner,
                            event_id=message_id,
                        )
                        if inserted:
                            c.execute(
                                d.private.update()
                                .where(
                                    d.private.c.group_id == gid,
                                    d.private.c.owner == owner,
                                )
                                .values(revision=d.private.c.revision + 1)
                            )
                        private = self.private_row(c, gid, owner)
                    payload = {
                        "action": module.split(".")[1],
                        "goal": p["body"]["goal"],
                        "owner_member_id": owner,
                        "assigned_tasks": [
                            t
                            for t in p["body"].get("tasks", [])
                            if t.get("owner_member_id") == owner
                        ],
                        "public_dependencies": self.public_dependencies(
                            p["body"].get("tasks", []), owner
                        ),
                        "conversation": self.dialogue(c, gid, owner),
                        "state": private["state"],
                        "answer": (
                            {**answer, "message_id": message_id} if answer else None
                        ),
                        "reference_date": row["reference_date"].isoformat(),
                    }
                    if answer:
                        # This is the person's explicit selection, not AI inference.
                        # Commit it before the network call so failures cannot erase it.
                        from ..personal import apply_answer

                        accepted = apply_answer(PersonalRequest.model_validate(payload))
                        payload["state"] = accepted.model_dump(mode="json")
                        payload["answer"] = None
                        c.execute(
                            d.private.update()
                            .where(
                                d.private.c.group_id == gid, d.private.c.owner == owner
                            )
                            .values(state=payload["state"])
                        )
                    snapshot = {
                        "approved": p["id"],
                        "private_revision": private["revision"],
                        "membership": row["membership_revision"],
                    }
                else:
                    p = self.proposal(c, gid)
                    if module == "group.REVISE":
                        if not p:
                            raise ValueError("수정할 제안이 없습니다.")
                        self.close_votes(c, gid)
                    objections = (
                        [
                            v.reason
                            for v in c.execute(
                                sa.select(d.votes).where(
                                    d.votes.c.proposal_id == p["id"],
                                    d.votes.c.agree.is_(False),
                                )
                            )
                        ]
                        if p
                        else []
                    )
                    if p and p["state"] == "VOTING" and module != "group.REVISE":
                        objections = [
                            v["reason"]
                            for v in self.proposal_view(c, p).get("issues", [])
                        ]
                    payload = {
                        "action": module.split(".")[1],
                        "conversation": self.dialogue(c, gid),
                        "members": [
                            {"member_id": m["id"], "nickname": m["nickname"]}
                            for m in self.members(c, gid)
                        ],
                        "previous": p["body"] if p else None,
                        "approval_status": p["state"] if p else "UNCONFIRMED",
                        "objections": objections,
                        "reference_date": row["reference_date"].isoformat(),
                        "timezone": row["timezone"],
                    }
                    # revision can change when this request explicitly closes voting.
                    revision = c.scalar(
                        sa.select(d.rooms.c.revision).where(d.rooms.c.id == gid)
                    )
                    public_event = (
                        c.scalar(
                            sa.select(sa.func.max(d.events.c.seq)).where(
                                d.events.c.group_id == gid, d.events.c.owner.is_(None)
                            )
                        )
                        or 0
                    )
                    snapshot = {
                        "revision": revision,
                        "membership": row["membership_revision"],
                        "public_event": public_event,
                    }
                op = {
                    "group_id": gid,
                    "action_id": action_id,
                    "id": uid(),
                    "module": module,
                    "owner": owner,
                    "input_hash": signature,
                    "params": sanitize(params),
                    "payload": sanitize(payload),
                    "snapshot": snapshot,
                    "state": "RUNNING",
                }
                c.execute(d.operations.insert().values(**op))
                claimed = True
        packet = self.recovery.get(op["id"])
        if packet:
            return self.finish(packet)
        if not claimed:
            state = (
                "UNKNOWN"
                if op["state"] in {"UNKNOWN", "SAVE_PENDING"}
                or op.get("created_at", datetime.now(timezone.utc))
                < datetime.now(timezone.utc) - timedelta(minutes=10)
                else "RUNNING"
            )
            return {
                "status": "NEEDS_INPUT",
                "needed_inputs": [
                    (
                        "실행 중입니다. 같은 동작의 결과를 다시 확인해주세요."
                        if state == "RUNNING"
                        else "수신 여부 확인이 필요합니다. 자동으로 AI를 재호출하지 않습니다."
                    )
                ],
                "persistence": {"operation_id": op["id"], "state": state},
                "new_http": 0,
            }
        try:
            if runner:
                result = runner(op["payload"])
            elif owner:
                result = personal_coach(
                    PersonalRequest.model_validate(op["payload"]), runtime=runtime
                )
            else:
                result = group_coach(
                    GroupRequest.model_validate(op["payload"]), runtime=runtime
                )
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
            result = self.checked_result(op, result)
            packet = {"operation": op, "result": sanitize(result)}
            self.recovery.put(op["id"], packet)
        except Exception:
            with self.tx(gid) as (c, _):
                c.execute(
                    d.operations.update()
                    .where(d.operations.c.id == op["id"])
                    .values(state="UNKNOWN")
                )
            return {
                "status": "FAILED",
                "error": {
                    "code": "UNKNOWN",
                    "message": "실행 결과 확인이 필요합니다. 자동 재호출하지 않습니다.",
                },
                "persistence": {"operation_id": op["id"], "state": "UNKNOWN"},
            }
        return self.finish(packet)

    def finish(self, packet):
        op = packet["operation"]
        gid = op["group_id"]
        result = self.checked_result(op, dict(packet["result"]))
        try:
            with self.tx(gid) as (c, row):
                current = (
                    c.execute(
                        sa.select(d.operations).where(d.operations.c.id == op["id"])
                    )
                    .mappings()
                    .one()
                )
                immutable_keys = (
                    "id",
                    "group_id",
                    "action_id",
                    "module",
                    "owner",
                    "input_hash",
                    "params",
                    "payload",
                    "snapshot",
                )
                if any(current[key] != op[key] for key in immutable_keys):
                    raise Conflict(
                        "복구 입력 불일치: 원래 저장한 실행만 재개할 수 있습니다."
                    )
                if current["result"] and current["state"] in {"COMPLETE", "STALE"}:
                    result = current["result"]
                else:
                    if op["owner"]:
                        private = self.private_row(c, gid, op["owner"])
                        stale = (
                            private["revision"] != op["snapshot"]["private_revision"]
                            or row["approved_proposal_id"] != op["snapshot"]["approved"]
                        )
                    else:
                        public_event = (
                            c.scalar(
                                sa.select(sa.func.max(d.events.c.seq)).where(
                                    d.events.c.group_id == gid,
                                    d.events.c.owner.is_(None),
                                )
                            )
                            or 0
                        )
                        changed = (
                            public_event != op["snapshot"]["public_event"]
                            if "public_event" in op["snapshot"]
                            else row["revision"] != op["snapshot"]["revision"]
                        )
                        stale = (
                            changed
                            or row["membership_revision"]
                            != op["snapshot"]["membership"]
                        )
                    data = result.get("data") or {}
                    data = (
                        data.model_dump(mode="json")
                        if hasattr(data, "model_dump")
                        else data
                    )
                    if not stale and result["status"] in {"OK", "NEEDS_INPUT"}:
                        if op["owner"]:
                            reply = PersonalReply.model_validate(data)
                            c.execute(
                                d.private.update()
                                .where(
                                    d.private.c.group_id == gid,
                                    d.private.c.owner == op["owner"],
                                )
                                .values(
                                    state=reply.state.model_dump(mode="json"),
                                    revision=private["revision"] + 1,
                                )
                            )
                            if reply.plan:
                                goal_id = c.scalar(
                                    sa.select(d.goals.c.id).where(
                                        d.goals.c.proposal_id
                                        == row["approved_proposal_id"]
                                    )
                                )
                                plan = reply.plan
                                mg = uid()
                                c.execute(
                                    d.personal_goals.insert().values(
                                        id=mg,
                                        group_id=gid,
                                        owner=op["owner"],
                                        goal_id=goal_id,
                                        body={
                                            "contribution": plan.contribution,
                                            "deliverable": plan.deliverable,
                                            "completion_criteria": plan.completion_criteria,
                                        },
                                        private_conditions={
                                            **reply.state.model_dump(mode="json"),
                                            "_source_private_revision": private[
                                                "revision"
                                            ]
                                            + 1,
                                        },
                                    )
                                )
                                c.execute(
                                    d.plans.insert().values(
                                        id=plan.plan_version_id,
                                        group_id=gid,
                                        goal_id=goal_id,
                                        owner=op["owner"],
                                        personal_goal_id=mg,
                                        body=plan.model_dump(mode="json"),
                                    )
                                )
                                for task in plan.tasks:
                                    c.execute(
                                        d.tasks.insert().values(
                                            id=task.task_id,
                                            group_id=gid,
                                            plan_id=plan.plan_version_id,
                                            owner=op["owner"],
                                            body=task.model_dump(mode="json"),
                                        )
                                    )
                                c.execute(
                                    d.private.update()
                                    .where(
                                        d.private.c.group_id == gid,
                                        d.private.c.owner == op["owner"],
                                    )
                                    .values(current_plan_id=plan.plan_version_id)
                                )
                        elif data.get("proposal"):
                            result["proposal_id"] = self.publish_proposal(
                                c, row, data["proposal"]
                            )
                    if data.get("reply"):
                        self.event(
                            c,
                            gid,
                            "AI_REPLY",
                            {
                                "text": data["reply"],
                                "summary": data.get("summary"),
                                "details": data.get("details", ""),
                                "stale": stale,
                                "run_id": result.get("meta", {}).get("run_id"),
                            },
                            owner=op["owner"],
                        )
                    if result.get("error"):
                        self.event(
                            c,
                            gid,
                            "AI_ERROR",
                            {
                                "text": result["error"]["message"],
                                "code": result["error"]["code"],
                            },
                            owner=op["owner"],
                        )
                    state = "STALE" if stale else "COMPLETE"
                    result["persistence"] = {
                        "operation_id": op["id"],
                        "state": state,
                        "record_revision": c.scalar(
                            sa.select(d.rooms.c.revision).where(d.rooms.c.id == gid)
                        ),
                        "retry_action": "REVIEW" if stale else "NONE",
                    }
                    c.execute(
                        d.operations.update()
                        .where(d.operations.c.id == op["id"])
                        .values(state=state, result=result)
                    )
        except Conflict:
            raise
        except Exception:
            try:
                with self.tx(gid) as (c, _):
                    c.execute(
                        d.operations.update()
                        .where(
                            d.operations.c.id == op["id"],
                            d.operations.c.state.not_in(["COMPLETE", "STALE"]),
                        )
                        .values(state="SAVE_PENDING")
                    )
            except Exception:
                pass
            return {
                "status": "FAILED",
                "error": {
                    "code": "STORAGE_FAILED",
                    "message": "응답을 암호화 보관했습니다. 같은 동작으로 저장만 재시도해주세요.",
                },
                "persistence": {
                    "operation_id": op["id"],
                    "state": "SAVE_PENDING",
                    "retry_action": "SAVE_ONLY",
                },
                "meta": result.get("meta"),
            }
        try:
            self.recovery.delete(op["id"])
        except OSError:
            pass
        return result

    def retry_save(self, gid, action_id, *, owner=None):
        """Never has a model runner: replay a durable response, not the AI call."""
        with self.read(gid) as (c, _):
            if owner is not None:
                self.member(c, gid, owner)
            op = (
                c.execute(
                    sa.select(d.operations).where(
                        d.operations.c.group_id == gid,
                        d.operations.c.action_id == action_id,
                        d.operations.c.owner == owner,
                    )
                )
                .mappings()
                .first()
            )
            if op is None:
                raise NotFound("해당 범위에서 실행을 찾지 못했습니다.")
            op = dict(op)
        if op["result"] is not None and op["state"] in {"COMPLETE", "STALE"}:
            return {**op["result"], "reused": True, "new_http": 0}
        packet = self.recovery.get(op["id"])
        if packet is not None:
            result = self.finish(packet)
            result["new_http"] = 0
            return result
        return {
            "status": "NEEDS_INPUT",
            "needed_inputs": [
                "응답 수신 여부 확인이 필요합니다. 이 함수는 AI를 재호출하지 않습니다."
            ],
            "persistence": {"operation_id": op["id"], "state": "UNKNOWN"},
            "new_http": 0,
        }

    def checked_result(self, op, result):
        if result.get("status") != "OK":
            return result
        try:
            if op["owner"]:
                reply = PersonalReply.model_validate(result["data"])
                if reply.plan:
                    from ..shared.contracts import Goal

                    required = ["level", "weekly_minutes", "scope"]
                    if any(
                        k not in reply.state.fields
                        or reply.state.fields[k].status != "KNOWN"
                        for k in required
                    ):
                        raise ValueError("개인 필수 조건 미확인")
                    pref = reply.state.fields.get("available_weekdays")
                    days = (
                        confirmed_weekdays(pref.value)
                        if pref and pref.status == "KNOWN"
                        else None
                    )
                    check = validate_plan(
                        reply.plan,
                        Goal.model_validate(op["payload"]["goal"]),
                        owner=op["owner"],
                        weekly_minutes=reply.state.fields["weekly_minutes"].value,
                        allowed_weekdays=days,
                    )
                    if not check["valid"]:
                        raise ValueError("잘못된 개인 계획")
            elif result.get("data", {}).get("proposal"):
                p = GroupProposal.model_validate(result["data"]["proposal"])
                ids = {m["member_id"] for m in op["payload"]["members"]}
                if (
                    set(p.goal.roles) != ids
                    or not validate_plan(p, p.goal, members=ids)["valid"]
                ):
                    raise ValueError("공동 제안 검증 실패")
        except (ValueError, KeyError, TypeError):
            bad = code_result(
                op["module"],
                error=Error(
                    code="RESULT_VALIDATION_FAILED",
                    message="결과의 담당·기간·시간 예산·확인 조건이 맞지 않습니다. 해당 조건을 수정한 새 요청이 필요합니다.",
                ),
            ).model_dump(mode="json")
            bad["meta"] = result.get("meta", bad["meta"])
            return bad
        return result

    def publish_personal(self, gid, owner, plan_id, action_id):
        def run(c, row):
            p = self.approved(c, row)
            self.member(c, gid, owner)
            plan = (
                c.execute(
                    sa.select(d.plans).where(
                        d.plans.c.group_id == gid,
                        d.plans.c.id == plan_id,
                        d.plans.c.owner == owner,
                    )
                )
                .mappings()
                .first()
            )
            goal_id = c.scalar(
                sa.select(d.goals.c.id).where(d.goals.c.proposal_id == p["id"])
            )
            if not plan or plan["goal_id"] != goal_id:
                raise Conflict("현재 공동 조건에 맞는 본인 계획을 확인해주세요.")
            conditions = c.scalar(
                sa.select(d.personal_goals.c.private_conditions).where(
                    d.personal_goals.c.id == plan["personal_goal_id"]
                )
            )
            if (
                conditions.get("_source_private_revision")
                != self.private_row(c, gid, owner)["revision"]
            ):
                raise Conflict(
                    "개인 대화·조건이 바뀌었습니다. 최신 조건으로 계획을 다시 정리해주세요."
                )
            latest = self.private_row(c, gid, owner)["current_plan_id"]
            if latest != plan_id:
                raise Conflict("이전 개인 계획입니다. 최신 계획을 검토해주세요.")
            old = (
                c.execute(
                    sa.select(d.baselines).where(d.baselines.c.plan_id == plan_id)
                )
                .mappings()
                .first()
            )
            if old:
                return {"baseline_id": old["id"], "plan_version_id": plan_id}
            data = PersonalPlan.model_validate(plan["body"])
            profile = c.scalar(
                sa.select(d.personal_goals.c.private_conditions).where(
                    d.personal_goals.c.id == plan["personal_goal_id"]
                )
            )
            fields = profile.get("fields", {})
            if any(
                fields.get(k, {}).get("status") != "KNOWN"
                for k in ["level", "weekly_minutes", "scope"]
            ):
                raise ValueError("개인 계획의 필수 조건을 먼저 확인해주세요.")
            from ..shared.contracts import Goal

            pref = fields.get("available_weekdays", {})
            days = (
                confirmed_weekdays(pref.get("value"))
                if pref.get("status") == "KNOWN"
                else None
            )
            check = validate_plan(
                data,
                Goal.model_validate(p["body"]["goal"]),
                owner=owner,
                weekly_minutes=fields["weekly_minutes"]["value"],
                allowed_weekdays=days,
            )
            if not check["valid"]:
                raise ValueError(
                    "공유 전 계획 조정이 필요합니다: " + "; ".join(check["issues"])
                )
            summary = {
                "owner_member_id": owner,
                "nickname": next(
                    m["nickname"] for m in self.members(c, gid) if m["id"] == owner
                ),
                "contribution": data.contribution,
                "deliverable": data.deliverable,
                "completion_criteria": data.completion_criteria,
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "deliverable": t.deliverable,
                        "due_date": t.due_date.isoformat(),
                        "completion_criteria": t.completion_criteria,
                    }
                    for t in data.tasks
                ],
                "plan_version_id": plan_id,
            }
            text = json.dumps(summary, ensure_ascii=False)
            secrets = [
                s
                for key in ["constraints", "support", "level"]
                for s in (
                    [profile["fields"][key]["value"]]
                    if isinstance(
                        profile.get("fields", {}).get(key, {}).get("value"), str
                    )
                    else profile.get("fields", {}).get(key, {}).get("value") or []
                )
                if isinstance(s, str) and len(s) >= 4
            ]
            if any(s in text for s in secrets):
                raise ValueError(
                    "공개 요약에 개인 조건이 포함되어 있습니다. 개인 계획을 수정해주세요."
                )
            baseline_id = uid()
            summary["baseline_id"] = baseline_id
            c.execute(
                d.baselines.insert().values(
                    id=baseline_id,
                    group_id=gid,
                    plan_id=plan_id,
                    owner=owner,
                    public_summary=summary,
                )
            )
            self.event(
                c,
                gid,
                "PERSONAL_PLAN_SHARED",
                {
                    "text": summary["nickname"]
                    + "님이 기여 계획을 확인하고 공유했습니다.",
                    "summary": summary,
                },
            )
            return {"baseline_id": baseline_id, "plan_version_id": plan_id}

        return self.action(gid, action_id, {"owner": owner, "plan_id": plan_id}, run)

    def evaluation_context(self, gid, owner, baseline_id, plan_id, task_id):
        with self.tx(gid) as (c, row):
            self.member(c, gid, owner)
            b = (
                c.execute(
                    sa.select(d.baselines).where(
                        d.baselines.c.id == baseline_id,
                        d.baselines.c.group_id == gid,
                        d.baselines.c.owner == owner,
                        d.baselines.c.plan_id == plan_id,
                    )
                )
                .mappings()
                .first()
            )
            t = (
                c.execute(
                    sa.select(d.tasks).where(
                        d.tasks.c.id == task_id,
                        d.tasks.c.plan_id == plan_id,
                        d.tasks.c.group_id == gid,
                        d.tasks.c.owner == owner,
                    )
                )
                .mappings()
                .first()
            )
            if not b or not t:
                raise ValueError("본인 기준·계획·과제 ID 연결을 확인해주세요.")
            return {
                "baseline_id": baseline_id,
                "plan_version_id": plan_id,
                "task_id": task_id,
                "task": t["body"],
            }

    def review_conditions(self, gid, owner, updates, action_id):
        from ..personal import normalize, FIELDS
        from ..shared.contracts import PersonalField

        def run(c, row):
            approved = self.approved(c, row)
            private = self.private_row(c, gid, owner)
            state = PersonalState.model_validate(private["state"])
            message_id = uid()
            values = {}
            for key, raw in updates.items():
                if key not in FIELDS:
                    raise ValueError("지원하지 않는 개인 항목")
                f = PersonalField.model_validate(raw)
                if f.status == "KNOWN":
                    f.value = normalize(key, f.value)
                elif f.status in {"UNKNOWN", "SKIPPED"}:
                    f.value = None
                f.evidence_refs = [message_id]
                state.fields[key] = f
                values[key] = f.model_dump(mode="json")
            state.question = None
            self.event(
                c,
                gid,
                "MESSAGE",
                {
                    "text": "본인이 확인·수정한 개인 조건: "
                    + json.dumps(values, ensure_ascii=False)
                },
                owner=owner,
                author=owner,
                event_id=message_id,
            )
            c.execute(
                d.private.update()
                .where(d.private.c.group_id == gid, d.private.c.owner == owner)
                .values(
                    state=state.model_dump(mode="json"),
                    revision=private["revision"] + 1,
                    proposal_id=approved["id"],
                )
            )
            return {"saved": True}

        return self.action(gid, action_id, {"owner": owner, "updates": updates}, run)

    def edit_personal(self, gid, owner, plan_id, body, action_id):
        def run(c, row):
            approved = self.approved(c, row)
            self.member(c, gid, owner)
            old = (
                c.execute(
                    sa.select(d.plans).where(
                        d.plans.c.id == plan_id,
                        d.plans.c.group_id == gid,
                        d.plans.c.owner == owner,
                    )
                )
                .mappings()
                .first()
            )
            goal_id = c.scalar(
                sa.select(d.goals.c.id).where(d.goals.c.proposal_id == approved["id"])
            )
            if not old or old["goal_id"] != goal_id:
                raise Conflict("현재 공동 조건의 본인 계획이 필요합니다.")
            plan = PersonalPlan.model_validate(body)
            private = self.private_row(c, gid, owner)
            state = PersonalState.model_validate(private["state"])
            if any(
                k not in state.fields or state.fields[k].status != "KNOWN"
                for k in ["level", "weekly_minutes", "scope"]
            ):
                raise ValueError(
                    "개인 계획의 필수 수준·시간·기여 범위 확인이 필요합니다."
                )
            budget = state.fields.get("weekly_minutes")
            if not budget or budget.status != "KNOWN":
                raise ValueError("본인이 확인한 가능 시간이 필요합니다.")
            from ..shared.contracts import Goal

            pref = state.fields.get("available_weekdays")
            days = (
                confirmed_weekdays(pref.value)
                if pref and pref.status == "KNOWN"
                else None
            )
            check = validate_plan(
                plan,
                Goal.model_validate(approved["body"]["goal"]),
                owner=owner,
                weekly_minutes=budget.value,
                allowed_weekdays=days,
            )
            if not check["valid"]:
                raise ValueError("; ".join(check["issues"]))
            assign_ids(plan)
            mg = uid()
            c.execute(
                d.personal_goals.insert().values(
                    id=mg,
                    group_id=gid,
                    owner=owner,
                    goal_id=goal_id,
                    body={
                        "contribution": plan.contribution,
                        "deliverable": plan.deliverable,
                        "completion_criteria": plan.completion_criteria,
                    },
                    private_conditions={
                        **state.model_dump(mode="json"),
                        "_source_private_revision": private["revision"],
                    },
                )
            )
            c.execute(
                d.plans.insert().values(
                    id=plan.plan_version_id,
                    group_id=gid,
                    goal_id=goal_id,
                    owner=owner,
                    personal_goal_id=mg,
                    body=plan.model_dump(mode="json"),
                )
            )
            for task in plan.tasks:
                c.execute(
                    d.tasks.insert().values(
                        id=task.task_id,
                        group_id=gid,
                        plan_id=plan.plan_version_id,
                        owner=owner,
                        body=task.model_dump(mode="json"),
                    )
                )
            c.execute(
                d.private.update()
                .where(d.private.c.group_id == gid, d.private.c.owner == owner)
                .values(current_plan_id=plan.plan_version_id)
            )
            self.event(
                c,
                gid,
                "PLAN_EDITED",
                {
                    "text": "본인이 개인 계획을 수정했습니다. 공개 요약을 확인한 후 공유해주세요.",
                    "plan_version_id": plan.plan_version_id,
                },
                owner=owner,
            )
            return {"plan_version_id": plan.plan_version_id}

        return self.action(
            gid, action_id, {"owner": owner, "plan_id": plan_id, "body": body}, run
        )
