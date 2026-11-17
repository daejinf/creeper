"""Static screen contract for the stage-7 completion flow."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kickoff_page_registers_completion_ui():
    html = (ROOT / 'tools' / 'bench' / 'kickoff.html').read_text(encoding='utf-8')
    server = (ROOT / 'tools' / 'bench' / 'server.py').read_text(encoding='utf-8')
    assert 'id="completionSubmissionDialog"' in html
    assert 'id="completionAppealDialog"' in html
    assert '/assets/kickoff-completion.js?v=10' in html
    assert html.index('kickoff-plans.js?v=6') < html.index('kickoff-completion.js?v=10')
    assert '"kickoff-completion.js"' in server
    assert 'id="completionDescription" rows="4" maxlength="5000"' in html


def test_completion_script_wires_only_existing_completion_api():
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion.js').read_text(encoding='utf-8')
    plans = (ROOT / 'tools' / 'bench' / 'kickoff-plans.js').read_text(encoding='utf-8')
    for fragment in (
        "'/completion/tasks'", "'/submissions/'", "'/review'", "'/reviews/'",
        "'/appeals'", "'/appeals/'", "'/votes'", "FormData", "files[]",
        "kickoff:personal-plan-rendered", "addEventListener('change',renderCards)",
        "pendingAction(", "finishAction(",
    ):
        assert fragment in source
    assert "new CustomEvent('kickoff:personal-plan-rendered'" in plans
    assert 'window.kickoffCompletion=controller' in source
    for method in ('loadGroup', 'loadMemberPlan', 'loadMemberTasks', 'validateEvidenceFiles', 'submitEvidence', 'runReview', 'submitAppeal', 'castVote', 'statusLabel'):
        assert method in source


def test_completion_screen_keeps_inputs_and_exposes_required_states():
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    assert "입력과 파일 선택은 유지됩니다." in source
    assert "입력은 유지됩니다." in source
    assert "approve.disabled=reject.disabled=busy||self" in source
    for state in ('SUBMITTED', 'REVIEW_NEEDED', 'NEEDS_INPUT', 'APPEALED', 'COMPLETED', 'REVIEW_FAILED'):
        assert state in source
    assert '.completionPanel' in css and '.completionCriteriaResults' in css


def test_completion_page_shell_keeps_group_context_without_calling_m2():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    server = (ROOT / 'tools' / 'bench' / 'server.py').read_text(encoding='utf-8')
    kickoff = (ROOT / 'tools' / 'bench' / 'kickoff.html').read_text(encoding='utf-8')
    plans = (ROOT / 'tools' / 'bench' / 'kickoff-plans.js').read_text(encoding='utf-8')
    assert '@app.get("/kickoff/completion")' in server
    assert '"kickoff-completion-page.js"' in server
    assert 'class="completionPageLayout"' in html
    assert 'id="completionMissingGroup"' in html
    assert 'id="backToKickoff"' in html
    assert 'new URLSearchParams(location.search)' in source
    assert "params.get('group')" in source
    assert "back.href='/kickoff?group='" in source
    assert 'fetch(' not in source
    assert 'window.kickoffCompletion' in source
    assert html.index('kickoff-completion.js?v=10') < html.index('kickoff-completion-page.js?v=12')
    assert 'id="openCompletionPage"' in kickoff
    assert "return '/kickoff/completion?'+query.toString()" in plans


def test_completion_page_loads_members_independently_with_actionable_errors():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    for fragment in (
        'id="completionPageLoading"', 'id="completionPageIssues"', 'id="completionTaskList"',
        'Promise.allSettled', 'state.epoch!==parentEpoch', 'state.memberEpoch.get',
        'BASELINE_NOT_READY', 'PLAN_VERSION_MISMATCH', 'NO_TASKS', 'TASK_OWNER_MISMATCH',
        'SERVER_CODE_MISMATCH', 'STORAGE_FAILED', '다시 시도', '실행 계획으로 이동',
        '개인 계획 다시 생성', '그룹 선택으로 돌아가기',
    ):
        assert fragment in html or fragment in source
    assert '완료 상태를 불러오지 못했습니다' not in source


def test_completion_page_exposes_progress_filters_sorting_and_detail_selection():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for label in ('전체 과제', '내 과제', '제출 전', 'AI 검토 필요', '자료 보완', '투표 필요', '완료'):
        assert f'>{label}<' in html
    for fragment in (
        'completionProgressValue', 'completed/total*100', 'function sortTasks',
        "task.status==='COMPLETED'", 'dueValue(a.task).localeCompare',
        'selectedTaskKey', 'renderDetail(', 'completionCompletedGroup',
        '담당 · ', 'completion.statusLabel', 'viewerMemberId',
    ):
        assert fragment in html or fragment in source or fragment in css


def test_completion_page_submission_uses_shared_validation_and_preserves_retry_state():
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    controller = (ROOT / 'tools' / 'bench' / 'kickoff-completion.js').read_text(encoding='utf-8')
    for fragment in (
        'description.maxLength=5000', "input.accept='.png,.jpg,.jpeg,.pdf,.docx",
        'completion.validateEvidenceFiles', 'completion.submitEvidence', 'completion.runReview',
        '설명과 파일 선택은 유지됩니다.', '파일을 다시 선택하지 않고 판정을 실행할 수 있습니다.',
        'submissionDraftKey', 'button.disabled=true',
    ):
        assert fragment in source
    assert 'validateEvidenceFiles(files)' in controller


def test_completion_page_renders_review_results_and_separate_failure_state():
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for fragment in (
        "MET:'✓ 충족'", "UNKNOWN:'? 자료 부족'", "NOT_MET:'✕ 미충족'",
        'review.summary', 'criteria_results', 'missing_items', 'review.user_message',
        'review_error_code', 'review_error_message', 'AI 검토 다시 시도', '새 자료 제출',
        "['NEEDS_INPUT','REVIEW_NEEDED']", '소명하기', 'completion.runReview',
    ):
        assert fragment in source
    assert '.completionReviewCriteria' in css
    assert '.completionReviewFailure' in css


def test_completion_page_renders_appeal_vote_and_superseded_states():
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for fragment in (
        'completion.submitAppeal', 'completion.castVote', '소명 내용', '필요한 찬성',
        '현재 찬성', '투표 가능', '현재 투표자 · ', "element('button','찬성'",
        "element('button','인정하지 않음'", '제출자는 자신의 소명에 투표할 수 없습니다.',
        '이미 투표함 · ', '과반 미달 · 투표 진행 중', '과반 도달 · 완료',
        "status:'SUPERSEDED'", '새 제출로 대체됨', 'voteRecordKey', 'supersededAppealKey',
    ):
        assert fragment in source
    assert '.completionAppealDetail' in css
    assert '.completionVoteActions' in css


def test_completion_page_visually_separates_member_ownership_and_vote_roles():
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for fragment in ('memberTaskGroup', 'completionOwnerBadge', "'내 과제'", "'소명 제출자'", "'현재 사용자'", "'내 역할'", "'이름 미설정'"):
        assert fragment in source
    assert 'memberNumber(' not in source
    for fragment in ('.completionMemberTaskGroup', '.completionOwnerBadge', '.completionVoteIdentity'):
        assert fragment in css


def test_completion_page_restores_and_synchronizes_db_state_without_stale_overwrite():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for fragment in (
        'id="refreshCompletionState"', 'id="completionSyncStatus"', '상태 새로고침',
        'async function refreshAllMembers', 'state.syncEpoch!==syncToken',
        'state.memberEpoch.get(member.member_id)!==token', 'state.requestedTaskId=entry.task.task_id',
        "params.set('task',entry.task.task_id)", 'document.hidden', "addEventListener('visibilitychange'",
        'setInterval(', 'AUTO_SYNC_MS=30000', "refreshAllMembers({reason:'manual'})",
        "await retryMember(entry.member.member_id)", "entry.task.status==='COMPLETED'",
    ):
        assert fragment in html or fragment in source
    assert '.completionHeaderActions' in css


def test_completion_ui_is_responsive_and_keyboard_accessible():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    source = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    controller = (ROOT / 'tools' / 'bench' / 'kickoff-completion.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for fragment in (
        'role="progressbar"', 'aria-valuemin="0"', 'aria-valuemax="100"',
        "card.setAttribute('aria-valuenow'", "card.setAttribute('aria-valuetext'",
        'descriptionLabel.htmlFor=descriptionId', 'fileLabel.htmlFor=fileId',
        'label.htmlFor=reasonId', "status.setAttribute('role','status')",
        "failed.setAttribute('role','alert')", "section.setAttribute('aria-live','polite')",
        "matchMedia('(max-width:720px)')", "detail.focus({preventScroll:true})",
        'dialogOpeners', "dialog.addEventListener('close'", 'opener?.isConnected',
    ):
        assert fragment in html or fragment in source or fragment in controller
    for fragment in (
        '@media(min-width:721px) and (max-width:1100px)',
        '@media(max-width:720px)', '.completionMemberList,.completionFilterList',
        'overflow-x:auto', '.completionPageDetail{min-height:100dvh',
        '@media(max-width:600px)', 'height:100dvh', 'overflow-x:clip',
    ):
        assert fragment in css


def test_stage_10_keeps_legacy_actions_while_linking_the_shared_page_state():
    controller = (ROOT / 'tools' / 'bench' / 'kickoff-completion.js').read_text(encoding='utf-8')
    page = (ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js').read_text(encoding='utf-8')
    plans = (ROOT / 'tools' / 'bench' / 'kickoff-plans.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for legacy_action in (
        "button(submitLabel,()=>openSubmission", "button(state.status==='REVIEW_FAILED'?",
        "button('소명하기',()=>openAppeal", "button('완료 인정에 찬성'",
        "button('인정하지 않음'",
    ):
        assert legacy_action in controller
    for fragment in (
        "make('a','전용 페이지에서 보기','completionTaskPageLink')",
        "group:group.group_id", "member:context.memberId", "owner:context.memberId",
        "task:task.task_id", "link.href='/kickoff/completion?'",
    ):
        assert fragment in controller
    assert "query.set('member',memberId)" in plans
    assert "query.set('owner',memberId)" in plans
    assert 'window.kickoffCompletion' in page
    assert 'fetch(' not in page
    assert '.completionTaskPageLink' in css
