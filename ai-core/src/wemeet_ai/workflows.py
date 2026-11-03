from .contracts import AnalysisRequest, DraftRequest, DraftWorkflow, HandoffPackage, Issue, Meta, Result, ValidationReport, GoalSpec
from .privacy import sanitize
from .tracing import active, emit
from .validation import validate_plan
from .draft_validation import normalize_goal_draft
from uuid import uuid5, NAMESPACE_URL


class WorkflowModules:
    def build_goal_draft(self, request):
        def operation(stats):
            from .service import GoalAI
            _, trace_id, parent_run, *_ = active.get()
            child = GoalAI(self.client, self.recorder, trace_sink=self.trace_sink, trace_id=trace_id, parent_run_id=parent_run,prompt_variant=self.prompt_variant,reference_provider=self.reference_provider,cache_backend=self.cache_backend,cache_scope=self.cache_scope,force_refresh=self.force_refresh,execution_mode=self.execution_mode)
            analysis = child.analyze_conversation(AnalysisRequest.model_validate(request.model_dump(exclude={"shared_context", "previous_draft"})))
            workflow = DraftWorkflow(analysis_result=analysis)
            results = [analysis]
            if analysis.status == "OK":
                workflow.draft_result = child.draft_goal_spec(DraftRequest(analysis=analysis.data, shared_context=request.shared_context, previous_draft=request.previous_draft, reference_date=request.reference_date, timezone=request.timezone))
                results.append(workflow.draft_result)
            for result in results:
                for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    setattr(stats.usage, name, getattr(stats.usage, name) + getattr(result.meta.usage, name))
                stats.transport_attempts += result.meta.transport_attempts
                stats.repair_attempts += result.meta.repair_attempts
                stats.actual_model = result.meta.model or stats.actual_model
                stats.request_ids.extend(result.meta.provider_request_ids)
            last = results[-1]
            for result in results:
                self.used_reference_ids.update(result.meta.reference_case_ids)
            return Result(status=last.status, data=workflow, error=last.error, questions=last.questions,
                          meta=Meta(run_id="", task="", prompt_version=""))
        return self._run("workflow", request, operation, uses_ai=False)

    def build_handoff(self, request):
        def operation(_):
            context = active.get()
            trace_id = context[1]
            issues = []
            if request.goal_spec is None or request.group_plan is None:
                issues.append(Issue(code="NOT_READY", field_path="goal_spec/group_plan", message="확인된 명세와 공동 계획이 필요합니다."))
            else:
                issues.extend(validate_plan(request.goal_spec, request.group_plan, request.member_plans, member_profiles=request.member_profiles).issues)
            if request.reviewed_draft:
                members = (request.goal_spec.member_profiles if isinstance(request.goal_spec, GoalSpec) else request.goal_spec.members) if request.goal_spec else None
                for key, field in normalize_goal_draft(request.reviewed_draft, members).fields.items():
                    for error in field.validation_errors:
                        issues.append(Issue(code="DRAFT_FIELD_INVALID", field_path="draft." + key, message=error))
                    if field.value not in (None, [], {}) and field.review_status == "PENDING":
                        issues.append(Issue(code="REVIEW_PENDING", field_path="draft." + key, message="검토되지 않은 AI 초안입니다."))
                    if request.goal_spec and field.value is not None and field.review_status in ("ACCEPTED", "EDITED"):
                        confirmed = request.goal_spec.model_dump(mode="json")
                        if key in confirmed and field.value != confirmed[key]:
                            issues.append(Issue(code="REVIEW_MISMATCH", field_path="draft." + key, message="검토 카드와 명세가 달라 다시 확인해야 합니다."))
            if request.source_fingerprint != request.current_source_fingerprint:
                issues.append(Issue(code="STALE_INPUT", field_path="source_fingerprint", message="입력이 변경되어 이전 명세·계획을 사용할 수 없습니다."))
            for baseline in request.baseline_refs:
                member=next((p.public_plan for p in request.member_plans if p.public_plan.member_id==baseline.owner_member_id),None)
                version=request.group_plan.plan_id if request.group_plan and baseline.scope=='GROUP' else member.plan_id if member else None
                expected_tasks={t.task_id or str(uuid5(NAMESPACE_URL,member.plan_id+':'+str(i))) for i,t in enumerate(member.first_week_todos)} if member else set()
                if not request.group_plan or baseline.group_plan_version_id!=request.group_plan.plan_id or baseline.plan_version_id!=version or (member and set(baseline.task_ids)!=expected_tasks):
                    issues.append(Issue(code='BASELINE_VERSION_MISMATCH',field_path='baseline_refs',message='평가 기준과 현재 계획·과제 버전이 다릅니다.'))
            report = ValidationReport(valid=not issues, issues=issues)
            public_spec = request.goal_spec.model_dump(mode="json", exclude={"member_profiles"}) if request.goal_spec else None
            group = {"schema_version": "1.1", "trace_id": trace_id, "integration_ready": report.valid,
                     "group_payload": {"goal_spec": public_spec, "group_plan": request.group_plan.model_dump(mode="json") if request.group_plan else None,
                                       "member_plans": [p.public_plan.model_dump(mode="json") for p in request.member_plans],
                                       "contribution_goals": [p.contribution_goal.model_dump(mode="json") for p in request.member_plans if p.contribution_goal]},
                     "validation": report.model_dump(mode="json"),
                     "run_manifest": [m.model_dump(mode="json") for m in request.run_manifest],
                     "evaluation_baselines": [b.model_dump(mode='json') for b in request.baseline_refs],
                     "activation": "REQUIRES_SW_APPROVAL"}
            # Even diagnostic group exports may not contain copied private strings.
            profiles = (request.goal_spec.member_profiles if isinstance(request.goal_spec, GoalSpec) else request.member_profiles) if request.goal_spec else []
            secrets = [c for p in profiles for c in p.constraints if len(c.strip()) >= 4]
            def public_only(value):
                if isinstance(value, str):
                    for secret in secrets: value = value.replace(secret, "[REDACTED_PRIVATE]")
                    return value
                if isinstance(value, list): return [public_only(v) for v in value]
                if isinstance(value, dict): return {k: public_only(v) for k, v in value.items()}
                return value
            private = {}
            if request.goal_spec:
                plans = {p.public_plan.member_id: p for p in request.member_plans}
                for profile in profiles:
                    mid = profile.member_id
                    private[mid] = {"schema_version": "1.1", "trace_id": trace_id, "owner_member_id": mid,
                                    "integration_ready": report.valid, "private_payload": {"profile": profile.model_dump(mode="json"),
                                    "intake": request.private_intakes[mid].model_dump(mode="json") if mid in request.private_intakes else None,
                                    "private_notes": plans[mid].private_notes if mid in plans else [],
                                    "personal_draft": request.personal_drafts[mid].model_dump(mode="json") if mid in request.personal_drafts else None,
                                    "conversation": [m.model_dump(mode="json") for m in request.personal_conversations.get(mid, [])]}}
            emit("handoff.validated", report.model_dump(mode="json"))
            return HandoffPackage(group_file=sanitize(public_only(group)), private_files=sanitize(private))
        return self._run("handoff", request, operation, uses_ai=False)
