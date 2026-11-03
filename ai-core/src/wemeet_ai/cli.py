import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .contracts import AnalysisRequest, CoachRequest, CoachState, SharedContext, DraftRequest, GoalSpec, Member, Message, PlanBundle, PrepareRequest, RevisionRequest, ValidationRequest, HandoffRequest
from .records import JsonlRecorder
from .service import GoalAI
from .contracts import GroupGoalSpec, GroupPrepareRequest, MemberPlanRequest, MemberGoalRequest, MemberGoalDraft, MemberCoachReply
from .contracts import DraftValidationRequest, GoalSetupRequest, GoalSetupOutput, GoalSetupState
from .contracts import CoachReply, CoachProposal, ProposalReviewRequest, ProactiveCoachOutput, BaselineRef
from .baseline import BaselineRequest, ActivityReference, build_baseline


def dump(value, output=None):
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    from .privacy import sanitize
    data=sanitize(data)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else: print(text)


def chat(ai, args):
    members = [Member(member_id=f"member-{i + 1}", nickname=nick.strip()) for i, nick in enumerate(args.members.split(","))]
    owner = members[args.owner - 1] if args.private else None
    history = []
    shared_context, coach_state = SharedContext(), CoachState()
    if args.input:
        saved = CoachRequest.model_validate_json(Path(args.input).read_text(encoding="utf-8"))
        members, history = saved.members, saved.conversation
        owner = next((m for m in members if m.member_id == saved.owner_member_id), None)
        shared_context, coach_state = saved.shared_context, saved.coach_state
    print("목표 코칭 / 종료: /quit, 화자: /speaker 닉네임, 대화 저장: /save 경로")
    active = owner or members[0]
    while True:
        try: text = input(active.nickname + ": ").strip()
        except (EOFError, KeyboardInterrupt): print(); return 0
        if text in ("/quit", "종료"): return 0
        if text.startswith("/speaker "):
            chosen = next((m for m in members if m.nickname == text[9:].strip()), None)
            if chosen and (not owner or chosen == owner): active = chosen
            else: print("해당 대화에서 선택할 수 없는 구성원입니다.")
            continue
        request = CoachRequest(coaching_mode='LEGACY' if owner else 'DIALOGUE',conversation=history or [Message(message_id="placeholder", speaker_id=active.member_id, role="user", text="대화 시작", created_at=datetime.now(timezone.utc))], members=members, scope="PRIVATE" if owner else "GROUP", owner_member_id=owner.member_id if owner else None, reference_date=date.fromisoformat(args.reference_date) if args.reference_date else datetime.now(ZoneInfo(args.timezone)).date(), timezone=args.timezone, shared_context=shared_context, coach_state=coach_state)
        if text.startswith("/save "):
            request.conversation = history
            dump(request, text[6:].strip()); continue
        if not text: continue
        history.append(Message(message_id=f"m-{len(history) + 1}", speaker_id=active.member_id, role="user", text=text, created_at=datetime.now(timezone.utc)))
        request.conversation = history
        result = ai.coach_reply(request)
        if result.status == "OK":
            coach_state = result.data.updated_coach_state
            print("AI: " + result.data.reply)
            for proposal in result.data.proposals:print('추천안: '+proposal.title+' · '+proposal.rationale+' (검토 전)')
            history.append(Message(message_id=f"m-{len(history) + 1}", role="assistant", text=result.data.reply, created_at=datetime.now(timezone.utc)))
        else: print(f"{result.error.code}: {result.error.message}")
        print(f"[{result.meta.run_id} · {result.meta.usage.total_tokens} tokens · {result.meta.latency_ms}ms]")


def main(argv=None):
    parser = argparse.ArgumentParser(description="WE·MEET M0/M1 AI core: no database required")
    parser.add_argument("command", choices=["goal-setup", "scenarios", "scenario-audit", "scenario-eval", "chat", "analyze", "intake-analyze", "draft", "draft-validate", "prepare", "prepare-group", "group-plan", "member-plan", "member-goal-coach", "member-goal-draft", "baseline", "provider-probe", "workspace-save", "workspace-load", "plan", "validate", "revise", "handoff", "example", "schemas", "eval"])
    parser.add_argument('--job-id',help='Resume a persisted scenario evaluation job')
    parser.add_argument('--all-batches',action='store_true',help='Run all pending batches sequentially')
    parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--case-id',action='append')
    parser.add_argument('--fresh',action='store_true',help='Explicitly request a new AI result instead of cache reuse')
    parser.add_argument('--no-cache',action='store_true')
    parser.add_argument("--input")
    parser.add_argument('--workspace-id')
    parser.add_argument('--db',default=os.getenv('WEMEET_DB_PATH','runs/workspace.sqlite3'))
    parser.add_argument("--output")
    parser.add_argument("--resume", help="Retry only failed members from a saved PlanBundle")
    parser.add_argument("--name", default="project", choices=["project", "study", "exercise", "regression", "design"])
    parser.add_argument("--members", default="지민,서준,하린")
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--owner", type=int, default=1)
    parser.add_argument("--log", default=os.getenv("WEMEET_AI_LOG"))
    parser.add_argument("--trace-file", help="Explicitly save sanitized detailed developer traces")
    parser.add_argument("--trace-id", help="Connect this CLI invocation to a workflow trace")
    parser.add_argument("--parent-run-id")
    parser.add_argument("--reference-date")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--debug-anonymous", action="store_true", help="Explicitly permit saving anonymous inputs and outputs")
    parser.add_argument("--live", action="store_true", help="Run real provider evaluation; incurs API usage")
    parser.add_argument("--legacy-root", help="Inspect legacy JSONL fixtures without claiming new-contract accuracy")
    args = parser.parse_args(argv)
    from .tracing import JsonlTraceSink
    ai = GoalAI(recorder=JsonlRecorder(args.log, debug_anonymous=args.debug_anonymous) if args.log else None,
                trace_sink=JsonlTraceSink(args.trace_file) if args.trace_file else None, trace_id=args.trace_id, parent_run_id=args.parent_run_id)
    try:
        if not args.no_cache and (args.workspace_id or args.input):
            from .adapters.result_cache import SQLiteResultCache
            ai.cache_backend=SQLiteResultCache(args.db)
            ai.cache_scope=args.workspace_id or 'cli:'+str(Path(args.input).resolve())
            ai.force_refresh=args.fresh
        if args.command=='scenarios':
            from .scenarios import all_scenarios
            dump([c.model_dump(mode='json') for c in all_scenarios() if not args.case_id or c.case_id in args.case_id],args.output);return 0
        if args.command=='scenario-audit':
            from .scenarios import audit_scenarios
            report=audit_scenarios();dump(report,args.output);return 0 if report['passed'] else 1
        if args.command=='scenario-eval':
            from .adapters.scenario_store import ScenarioStore
            from .adapters.scenario_eval import EvaluationManager
            manager=EvaluationManager(ScenarioStore(args.db),ai.client)
            job=manager.store.job(args.job_id) if args.job_id else manager.create(args.case_id)
            if args.live:
                manager.start(job['id'],automatic=args.all_batches,retry_failed=args.retry_failed)
                for thread in manager.threads.values():thread.join()
                job=manager.store.job(job['id'])
            dump(job,args.output);return 0
        repository=None
        if args.workspace_id or args.command in {'workspace-save','workspace-load'}:
            from .adapters.repository import SQLiteRepository
            repository=SQLiteRepository(args.db)
        if args.workspace_id:
            repository.get(args.workspace_id)
            from .adapters.telemetry import PersistentTraceSink
            ai.trace_sink=PersistentTraceSink(repository,args.workspace_id)
        if args.command=='workspace-load':
            if not args.workspace_id:parser.error('--workspace-id is required')
            dump(repository.get(args.workspace_id),args.output);return 0
        if args.command=='provider-probe':
            result=ai.probe_provider();dump(result,args.output);return 0 if result.status=='OK' else 1
        if args.command == "chat": return chat(ai, args)
        if args.command == "example":
            from .examples import example_input, regression_conversation, design_conversation
            dump(design_conversation() if args.name == "design" else regression_conversation() if args.name == "regression" else example_input(args.name), args.output); return 0
        if args.command == "schemas":
            from .contracts import ConversationAnalysis, GroupPlan, MemberPlanDraft, Result, GoalSpecDraft, PrivateIntakeAnalysis, WorkflowRequest, DraftWorkflow, HandoffPackage
            types = [AnalysisRequest, CoachRequest, PrepareRequest, GroupPrepareRequest, GroupGoalSpec, GoalSpec, GroupPlan, MemberPlanRequest, MemberPlanDraft, ValidationRequest, RevisionRequest, Result, ConversationAnalysis, PlanBundle, DraftRequest, GoalSpecDraft, PrivateIntakeAnalysis, WorkflowRequest, DraftWorkflow, HandoffRequest, HandoffPackage]
            types.extend([MemberGoalRequest, MemberGoalDraft, MemberCoachReply, DraftValidationRequest, BaselineRequest, ActivityReference, CoachReply, CoachProposal, ProposalReviewRequest, ProactiveCoachOutput, BaselineRef, GoalSetupRequest, GoalSetupOutput, GoalSetupState])
            dump({model.__name__: model.model_json_schema() for model in types}, args.output); return 0
        if args.command == "eval":
            from .evaluation import evaluate_examples
            result = evaluate_examples(ai, live=args.live, legacy_root=args.legacy_root, resume_path=args.resume)
            dump(result, args.output); return 0 if result["passed"] else 1
        if not args.input: parser.error("--input is required")
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if args.command=='workspace-save':
            if args.workspace_id:
                saved=repository.get(args.workspace_id);saved=repository.save(args.workspace_id,payload,saved['revision'])
            else:saved=repository.create(payload)
            dump(saved,args.output);return 0
        if isinstance(payload, dict) and payload.get("status") == "OK" and "data" in payload: payload = payload["data"]
        if args.command == "goal-setup": result = ai.advance_goal_setup(GoalSetupRequest.model_validate(payload))
        elif args.command == "analyze": result = ai.analyze_conversation(AnalysisRequest.model_validate(payload))
        elif args.command == "intake-analyze": result = ai.analyze_private_intake(CoachRequest.model_validate(payload))
        elif args.command == "draft": result = ai.draft_goal_spec(DraftRequest.model_validate(payload if "analysis" in payload else {"analysis":payload}))
        elif args.command == "handoff":
            if "member_results" in payload:
                payload = {"goal_spec":payload["goal_spec"], "group_plan":payload["group_plan"], "member_plans":[r["data"] for r in payload["member_results"].values() if r["status"]=="OK"], "run_manifest":[r["meta"] for r in payload["member_results"].values()]}
            result = ai.build_handoff(HandoffRequest.model_validate(payload))
        elif args.command == "prepare": result = ai.prepare_goal_spec(PrepareRequest.model_validate(payload))
        elif args.command == "prepare-group": result = ai.prepare_group_goal_spec(GroupPrepareRequest.model_validate(payload))
        elif args.command == "group-plan": result = ai.generate_group_plan(GroupGoalSpec.model_validate(payload) if "members" in payload else GoalSpec.model_validate(payload))
        elif args.command == "member-plan": result = ai.generate_member_plan(MemberPlanRequest.model_validate(payload))
        elif args.command == "member-goal-coach": result = ai.coach_member_goal(MemberGoalRequest.model_validate(payload))
        elif args.command == "member-goal-draft": result = ai.draft_member_goal(MemberGoalRequest.model_validate(payload))
        elif args.command == "draft-validate": result = ai.validate_goal_draft(DraftValidationRequest.model_validate(payload))
        elif args.command == 'baseline':result=ai._run('baseline',payload,lambda _:build_baseline(BaselineRequest.model_validate(payload)),uses_ai=False)
        elif args.command == "plan":
            existing = PlanBundle.model_validate_json(Path(args.resume).read_text(encoding="utf-8")) if args.resume else None
            result = ai.generate_bundle(GoalSpec.model_validate(payload), existing)
        elif args.command == "validate": result = ai.validate_plan(ValidationRequest.model_validate(payload))
        else: result = ai.revise_plan(RevisionRequest.model_validate(payload))
        if repository and args.workspace_id:
            saved=repository.get(args.workspace_id);snapshot=saved['snapshot'];snapshot['last_cli_result']=result.model_dump(mode='json')
            repository.save(args.workspace_id,snapshot,saved['revision'])
            if args.command=='baseline' and result.status=='OK':repository.save_baseline(args.workspace_id,result.data)
        dump(result, args.output)
        if isinstance(result, PlanBundle): return 0 if result.validation.valid else 1
        return 0 if result.status == "OK" and not (hasattr(result.data, "valid") and not result.data.valid) else 1
    except (OSError, ValueError, KeyError, ValidationError) as exc:
        print(json.dumps({"status": "FAILED", "error": {"code": "INPUT_INVALID", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
