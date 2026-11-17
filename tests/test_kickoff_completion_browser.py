"""Real-browser completion flow on the existing kickoff task-card UI."""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


SHELL = """
<!doctype html><html lang="ko"><body>
<button data-assistant="plans" aria-pressed="true">실행 계획</button>
<select id="speaker"><option value="owner">제출자</option><option value="peer">동료</option></select>
<main id="cards"></main><pre id="requestJson"></pre><pre id="responseJson"></pre>
<h3 id="modulePythonTitle"></h3><pre id="modulePython"></pre>
<dialog id="completionSubmissionDialog"><form id="completionSubmissionForm">
  <div id="completionSubmissionContext"></div>
  <textarea id="completionDescription" maxlength="5000" required></textarea>
  <input id="completionFiles" type="file" multiple><ul id="completionFileList"></ul>
  <p id="completionSubmissionStatus"></p><button id="closeCompletionSubmission" type="button">닫기</button>
  <button id="cancelCompletionSubmission" type="button">취소</button><button id="submitCompletionEvidence" type="submit">제출 저장</button>
</form></dialog>
<dialog id="completionAppealDialog"><form id="completionAppealForm">
  <div id="completionAppealReview"></div><p id="completionAppealVotes"></p>
  <textarea id="completionAppealReason" maxlength="5000" required></textarea>
  <p id="completionAppealStatus"></p><button id="closeCompletionAppeal" type="button">닫기</button>
  <button id="cancelCompletionAppeal" type="button">취소</button><button id="submitCompletionAppeal" type="submit">소명 제출</button>
</form></dialog>
</body></html>
"""


MOCK_RUNTIME = r"""
const node=(tag,text)=>{const value=document.createElement(tag);if(text!==undefined)value.textContent=text;return value;};
let group={group_id:'group-1',members:[{member_id:'owner',nickname:'제출자'},{member_id:'peer',nickname:'동료'}]};
const endpoint=suffix=>'/api/kickoff/groups/'+group.group_id+(suffix||'');
const storageKey=name=>'kickoff:'+group.group_id+':'+name;
window.notifications=[];const notify=(text,error=false)=>window.notifications.push({text,error});
window.fixture={calls:[],submissionAttempts:0,appealAttempts:0,state:{
  group_id:'group-1',plan_version_id:'plan-1',baseline_id:'baseline-1',task_id:'task-1',member_id:'owner',
  title:'결과 정리',deliverable:'결과 화면',completion_criteria:['결과 화면을 제출한다'],due_date:'2026-11-20',
  status:'TODO',revision:0,latest_submission:null,latest_review:null,latest_appeal:null
}};
window.fetch=async(path,options={})=>{
  const method=options.method||'GET',body=options.body instanceof FormData
    ? Object.fromEntries([...options.body.entries()].filter(([key])=>key!=='files[]'))
    : options.body?JSON.parse(options.body):null;
  window.fixture.calls.push({path:String(path),method,body});
  const state=window.fixture.state,reply=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
  if(method==='GET'&&String(path).endsWith('/completion/tasks'))return reply({group_id:'group-1',member_id:'owner',tasks:[state]});
  if(String(path).endsWith('/submissions')){
    window.fixture.submissionAttempts+=1;state.status='SUBMITTED';state.revision+=1;
    state.latest_submission={submission_id:'submission-1',description:body.description,evidence:[]};
    if(window.fixture.submissionAttempts===1)throw new TypeError('response lost after commit');
    return reply({submission_id:'submission-1',status:'SUBMITTED',evidence:[],reused:true});
  }
  if(String(path).endsWith('/review')){
    state.status='NEEDS_INPUT';state.revision+=1;state.latest_review={review_id:'review-1',decision:'NEEDS_INPUT',
      summary:'결과 화면을 더 확인해야 합니다.',criteria_results:[{criterion_index:0,criterion:'결과 화면을 제출한다',status:'UNKNOWN',reason:'화면 자료가 부족합니다.',evidence_refs:[]}],
      missing_items:['최종 결과 화면'],user_message:'자료를 보완하거나 소명해주세요.'};
    return reply({status:'NEEDS_INPUT',review:state.latest_review,reused:false});
  }
  if(String(path).endsWith('/appeals')){
    window.fixture.appealAttempts+=1;state.status='APPEALED';state.revision+=1;
    state.latest_appeal={appeal_id:'appeal-1',status:'OPEN',reason:body.reason,required_votes:1,eligible_voters:1,approve_votes:0};
    if(window.fixture.appealAttempts===1)throw new TypeError('response lost after commit');
    return reply({status:'APPEALED',appeal:state.latest_appeal});
  }
  if(String(path).endsWith('/votes')){
    state.status='COMPLETED';state.revision+=1;state.latest_appeal={...state.latest_appeal,status:'APPROVED',approve_votes:1};
    return reply({status:'COMPLETED',appeal:state.latest_appeal});
  }
  return new Response(JSON.stringify({error:{message:'unexpected request'}}),{status:500,headers:{'Content-Type':'application/json'}});
};
"""


def test_submit_review_appeal_peer_vote_complete_and_restore():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page_errors = []
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        page.set_default_timeout(5_000)
        page.route('http://wemeet.test/', lambda route: route.fulfill(body=SHELL, content_type='text/html; charset=utf-8'))
        page.goto('http://wemeet.test/')
        page.add_script_tag(content=MOCK_RUNTIME)
        page.add_script_tag(path=str(ROOT / 'tools' / 'bench' / 'kickoff-completion.js'))
        page.evaluate("""
          const card=document.createElement('article');card.className='taskCard';document.querySelector('#cards').append(card);
          const task={task_id:'task-1',owner_member_id:'owner',title:'결과 정리',deliverable:'결과 화면',completion_criteria:['결과 화면을 제출한다'],due_date:'2026-11-20'};
          window.dispatchEvent(new CustomEvent('kickoff:personal-plan-rendered',{detail:{memberId:'owner',plan:{plan_version_id:'plan-1'},taskCards:[{card,task}]}}));
        """)

        badge = page.locator('.completionBadge')
        badge.wait_for()
        assert badge.inner_text() == '제출 전'
        assert page.locator('#completionDescription').get_attribute('maxlength') == '5000'
        task_page_link = page.locator('.completionPanel').get_by_role('link', name='결과 정리 과제를 전용 완료 판정 페이지에서 보기')
        assert task_page_link.get_attribute('href') == '/kickoff/completion?group=group-1&member=owner&owner=owner&task=task-1'

        submit_open = page.locator('.completionPanel').get_by_role('button', name='결과 제출')
        assert submit_open.count() == 1 and submit_open.is_enabled()
        submit_open.focus()
        page.keyboard.press('Enter')
        assert not page_errors, page_errors
        assert page.locator('#completionSubmissionDialog').evaluate("element=>({open:element.open,display:getComputedStyle(element).display})") == {'open': True, 'display': 'block'}
        assert page.evaluate('document.activeElement.id') == 'completionDescription'
        page.locator('#cancelCompletionSubmission').focus()
        page.keyboard.press('Enter')
        assert page.evaluate("document.activeElement?.textContent") == '결과 제출'
        page.keyboard.press('Enter')
        page.locator('#completionDescription').fill('완료한 결과 화면입니다.')
        page.locator('#submitCompletionEvidence').focus()
        page.keyboard.press('Enter')
        page.locator('#completionSubmissionStatus').filter(has_text='입력과 파일 선택은 유지됩니다.').wait_for()
        page.wait_for_function("!document.getElementById('submitCompletionEvidence').disabled")
        page.locator('#submitCompletionEvidence').focus()
        page.keyboard.press('Enter')
        page.locator('#completionSubmissionDialog').wait_for(state='hidden')
        assert badge.inner_text() == '제출 저장됨'

        page.get_by_role('button', name='AI 완료 여부 검토').focus()
        page.keyboard.press('Enter')
        page.get_by_role('button', name='소명하기').wait_for()
        assert badge.inner_text() == '자료 보완 필요'
        page.get_by_role('button', name='판정 기록 보기').click()
        assert page.get_by_text('부족한 자료').is_visible()

        appeal_open = page.get_by_role('button', name='소명하기')
        appeal_open.focus()
        page.keyboard.press('Enter')
        assert page.evaluate('document.activeElement.id') == 'completionAppealReason'
        page.locator('#cancelCompletionAppeal').focus()
        page.keyboard.press('Enter')
        page.wait_for_function("document.activeElement?.textContent === '소명하기'")
        page.keyboard.press('Enter')
        page.locator('#completionAppealReason').fill('제출 설명과 결과를 다시 확인해주세요.')
        page.locator('#submitCompletionAppeal').focus()
        page.keyboard.press('Enter')
        page.locator('#completionAppealStatus').filter(has_text='입력은 유지됩니다.').wait_for()
        page.wait_for_function("!document.getElementById('submitCompletionAppeal').disabled")
        page.locator('#submitCompletionAppeal').focus()
        page.keyboard.press('Enter')
        page.locator('#completionAppealDialog').wait_for(state='hidden')
        assert badge.inner_text() == '동료 투표 중'
        assert page.get_by_role('button', name='완료 인정에 찬성').is_disabled()
        assert page.get_by_text('제출자는 자신의 소명에 투표할 수 없습니다.').is_visible()

        page.locator('#speaker').focus()
        page.keyboard.press('ArrowDown')
        page.get_by_role('button', name='완료 인정에 찬성').focus()
        page.keyboard.press('Enter')
        page.get_by_text('동료 확인 완료').wait_for()
        assert badge.inner_text() == '완료'
        assert page.get_by_role('button', name='결과 제출').is_disabled()
        assert task_page_link.is_visible()

        actions = page.evaluate("window.fixture.calls.filter(call=>call.body?.action_id).map(call=>call.body.action_id)")
        assert actions[0] == actions[1]
        assert actions[3] == actions[4]
        assert len({actions[0], actions[2], actions[3], actions[5]}) == 4

        page.evaluate("""
          const card=document.createElement('article');card.className='taskCard';document.querySelector('#cards').replaceChildren(card);
          const task={task_id:'task-1',owner_member_id:'owner',title:'결과 정리',deliverable:'결과 화면',completion_criteria:['결과 화면을 제출한다'],due_date:'2026-11-20'};
          window.dispatchEvent(new CustomEvent('kickoff:personal-plan-rendered',{detail:{memberId:'owner',plan:{plan_version_id:'plan-1'},taskCards:[{card,task}]}}));
        """)
        page.get_by_text('동료 확인 완료').wait_for()
        assert page.locator('.completionBadge').inner_text() == '완료'
        browser.close()


def test_dedicated_page_loads_the_shared_controller_without_card_dom():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    calls = []
    peer_attempts = 0
    submission_attempts = 0
    submission_action_ids = []
    owner_submitted = False
    owner_review_failed = False
    review_attempts = 0
    review_action_ids = []
    owner_review = None
    appeal_attempts = 0
    appeal_action_ids = []
    vote_attempts = 0
    vote_action_ids = []
    owner_appeal = None
    review_task_completed = False
    input_appeal = None
    input_submitted_new = False

    def api(route):
        nonlocal peer_attempts, submission_attempts, owner_submitted, owner_review_failed, review_attempts, owner_review
        nonlocal appeal_attempts, vote_attempts, owner_appeal, review_task_completed, input_appeal, input_submitted_new
        path = route.request.url.split('wemeet.test', 1)[-1]
        calls.append(path)
        if route.request.method == 'POST' and path.endswith('/plans/plan-1/tasks/ready-task/submissions'):
            submission_attempts += 1
            body = route.request.post_data or ''
            match = re.search(r'name="action_id"\r\n\r\n([^\r]+)', body)
            submission_action_ids.append(match.group(1) if match else '')
            owner_submitted = True
            if submission_attempts == 1:
                route.abort()
                return
            route.fulfill(body=json.dumps({'submission_id': 'submission-page-1', 'status': 'SUBMITTED',
                                           'evidence': [], 'reused': True}), content_type='application/json')
            return
        if route.request.method == 'POST' and path.endswith('/submissions/submission-page-1/review'):
            review_attempts += 1
            review_action_ids.append(json.loads(route.request.post_data)['action_id'])
            if review_attempts == 1:
                owner_review_failed = True
                route.fulfill(status=502, body=json.dumps({'status': 'REVIEW_FAILED', 'error': {
                    'code': 'AI_REVIEW_FAILED', 'message': '결정적 판정 fixture 실패',
                }}), content_type='application/json')
                return
            owner_review_failed = False
            owner_review = {'review_id': 'review-pass', 'decision': 'PASS', 'summary': '모든 기준을 충족했습니다.',
                            'criteria_results': [{'criterion_index': 0, 'criterion': '결과 화면을 제출한다',
                                                  'status': 'MET', 'reason': '제출 화면에서 확인했습니다.'}],
                            'missing_items': [], 'user_message': '과제가 완료됐습니다.'}
            route.fulfill(body=json.dumps({'status': 'COMPLETED', 'review': owner_review, 'reused': False}),
                          content_type='application/json')
            return
        if route.request.method == 'POST' and path.endswith('/reviews/review-needed/appeals'):
            appeal_attempts += 1
            body = json.loads(route.request.post_data)
            appeal_action_ids.append(body['action_id'])
            owner_appeal = {'appeal_id': 'appeal-needed', 'status': 'OPEN', 'reason': body['reason'],
                            'required_votes': 1, 'eligible_voters': 1, 'approve_votes': 0}
            if appeal_attempts == 1:
                route.abort()
                return
            route.fulfill(body=json.dumps({'status': 'APPEALED', 'appeal': owner_appeal, 'reused': True}),
                          content_type='application/json')
            return
        if route.request.method == 'POST' and path.endswith('/reviews/review-input/appeals'):
            body = json.loads(route.request.post_data)
            input_appeal = {'appeal_id': 'appeal-input', 'status': 'OPEN', 'reason': body['reason'],
                            'required_votes': 1, 'eligible_voters': 1, 'approve_votes': 0}
            route.fulfill(body=json.dumps({'status': 'APPEALED', 'appeal': input_appeal, 'reused': False}),
                          content_type='application/json')
            return
        if route.request.method == 'POST' and path.endswith('/plans/plan-1/tasks/input-task/submissions'):
            input_submitted_new = True
            route.fulfill(body=json.dumps({'submission_id': 'submission-input-new', 'status': 'SUBMITTED',
                                           'evidence': [], 'reused': False}), content_type='application/json')
            return
        if route.request.method == 'POST' and path.endswith('/appeals/appeal-needed/votes'):
            vote_attempts += 1
            body = json.loads(route.request.post_data)
            vote_action_ids.append(body['action_id'])
            owner_appeal = {**owner_appeal, 'status': 'APPROVED', 'approve_votes': 1}
            review_task_completed = True
            if vote_attempts == 1:
                route.abort()
                return
            route.fulfill(body=json.dumps({'status': 'COMPLETED', 'appeal': owner_appeal, 'reused': True}),
                          content_type='application/json')
            return
        if route.request.method == 'POST' and path.endswith('/appeals/appeal-input/votes'):
            route.fulfill(body=json.dumps({'status': 'APPEALED', 'appeal': input_appeal, 'reused': False}),
                          content_type='application/json')
            return
        if path == '/api/kickoff/groups/group-1':
            value = {'group_id': 'group-1', 'title': '테스트 그룹', 'members': [
                {'member_id': 'owner', 'nickname': '제출자'}, {'member_id': 'peer', 'nickname': '동료'},
            ]}
        elif path.endswith('/members/owner/completion/tasks'):
            value = {'group_id': 'group-1', 'member_id': 'owner', 'tasks': [
                {'plan_version_id': 'plan-1', 'task_id': 'ready-task', 'title': '준비된 과제',
                 'deliverable': '결과 화면', 'completion_criteria': ['결과 화면을 제출한다'],
                 'due_date': '2026-11-20', 'status': 'COMPLETED' if owner_review else ('REVIEW_FAILED' if owner_review_failed else ('SUBMITTED' if owner_submitted else 'TODO')), 'revision': 3 if owner_review else (2 if owner_review_failed else (1 if owner_submitted else 0)),
                 'latest_submission': ({'submission_id': 'submission-page-1', 'description': '완료한 결과 화면입니다.', 'evidence': []} if owner_submitted else None),
                 'latest_review': owner_review,
                 'review_error_code': 'AI_REVIEW_FAILED' if owner_review_failed else None,
                 'review_error_message': '결정적 판정 fixture 실패' if owner_review_failed else None},
                {'plan_version_id': 'plan-1', 'task_id': 'input-task', 'title': '자료 보완 과제',
                 'deliverable': '근거 자료', 'completion_criteria': ['근거 위치를 설명한다'],
                 'due_date': '2026-11-19', 'status': 'SUBMITTED' if input_submitted_new else ('APPEALED' if input_appeal else 'NEEDS_INPUT'), 'revision': 4 if input_submitted_new else (3 if input_appeal else 2),
                 'latest_submission': {'submission_id': 'submission-input-new' if input_submitted_new else 'submission-input', 'description': '새 근거를 제출했습니다.' if input_submitted_new else '근거 자료를 제출했습니다.', 'evidence': []},
                 'latest_review': {'review_id': 'review-input', 'decision': 'NEEDS_INPUT', 'summary': '자료를 더 확인해야 합니다.',
                                   'criteria_results': [{'criterion_index': 0, 'criterion': '근거 위치를 설명한다', 'status': 'UNKNOWN', 'reason': '최종 화면이 없습니다.'}],
                                   'missing_items': ['최종 결과 화면'], 'user_message': '자료를 보완하거나 소명해주세요.'},
                 'latest_appeal': None if input_submitted_new else input_appeal},
                {'plan_version_id': 'plan-1', 'task_id': 'review-task', 'title': '추가 확인 과제',
                 'deliverable': '검토 기록', 'completion_criteria': ['검토 승인을 받는다'],
                 'due_date': '2026-11-21', 'status': 'COMPLETED' if review_task_completed else ('APPEALED' if owner_appeal else 'REVIEW_NEEDED'), 'revision': 4 if review_task_completed else (3 if owner_appeal else 2),
                 'latest_submission': {'submission_id': 'submission-needed', 'description': '검토 기록을 제출했습니다.', 'evidence': []},
                 'latest_review': {'review_id': 'review-needed', 'decision': 'REVIEW_NEEDED', 'summary': '기준을 충족하지 못했습니다.',
                                   'criteria_results': [{'criterion_index': 0, 'criterion': '검토 승인을 받는다', 'status': 'NOT_MET', 'reason': '승인 기록이 없습니다.'}],
                                   'missing_items': [], 'user_message': '새 자료를 제출하거나 소명해주세요.'},
                 'latest_appeal': owner_appeal},
                {'plan_version_id': 'plan-1', 'task_id': 'completed-task', 'title': '완료 과제',
                 'deliverable': '완료본', 'completion_criteria': ['완료본을 공유한다'],
                 'due_date': '2026-11-18', 'status': 'COMPLETED', 'revision': 3,
                 'latest_submission': {'submission_id': 'submission-completed', 'description': '완료본을 공유했습니다.', 'evidence': []},
                 'latest_review': {'review_id': 'review-completed', 'decision': 'PASS', 'summary': '완료 기준을 충족했습니다.',
                                   'criteria_results': [{'criterion_index': 0, 'criterion': '완료본을 공유한다', 'status': 'MET', 'reason': '공유 기록을 확인했습니다.'}],
                                   'missing_items': [], 'user_message': '완료됐습니다.'}},
            ]}
        elif path.endswith('/members/owner/personal'):
            value = {'planning': {'plan': {'code_status': 'CURRENT', 'result': {'data': {
                'plan_version_id': 'plan-1', 'tasks': [
                    {'task_id': 'ready-task', 'title': '준비된 과제', 'deliverable': '결과 화면', 'due_date': '2026-11-20'},
                    {'task_id': 'input-task', 'title': '자료 보완 과제', 'deliverable': '근거 자료', 'due_date': '2026-11-19'},
                    {'task_id': 'review-task', 'title': '추가 확인 과제', 'deliverable': '검토 기록', 'due_date': '2026-11-21'},
                    {'task_id': 'completed-task', 'title': '완료 과제', 'deliverable': '완료본', 'due_date': '2026-11-18'},
                    {'task_id': 'unready-task', 'title': '기준 없는 과제', 'deliverable': '확인표', 'due_date': '2026-11-21'},
                ],
            }}}}}
        elif path.endswith('/members/peer/completion/tasks'):
            peer_attempts += 1
            if peer_attempts == 1:
                route.fulfill(status=503, body=json.dumps({'status': 'FAILED', 'error': {
                    'code': 'STORAGE_FAILED', 'message': '완료 저장소에 연결할 수 없습니다.',
                }}), content_type='application/json')
                return
            value = {'group_id': 'group-1', 'member_id': 'peer', 'tasks': [{
                'plan_version_id': 'plan-2', 'task_id': 'peer-task', 'title': '동료 과제',
                'deliverable': '검토표', 'completion_criteria': ['동료 검토를 받는다'],
                'due_date': '2026-11-22', 'status': 'APPEALED', 'revision': 1,
            }]}
        elif path.endswith('/members/peer/personal'):
            value = {'planning': {'plan': {'code_status': 'CURRENT', 'result': {'data': {
                'plan_version_id': 'plan-2', 'tasks': [
                    {'task_id': 'peer-task', 'title': '동료 과제', 'deliverable': '검토표', 'due_date': '2026-11-22'},
                ],
            }}}}}
        else:
            route.fulfill(status=500, body=json.dumps({'error': {'message': 'unexpected '+path}}), content_type='application/json')
            return
        route.fulfill(body=json.dumps(value), content_type='application/json')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page_errors = []
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        page.route('http://wemeet.test/kickoff/completion?*',
                   lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        page.route('**/assets/kickoff.css*',
                   lambda route: route.fulfill(path=ROOT / 'tools' / 'bench' / 'kickoff.css',
                                                content_type='text/css'))
        page.route('**/assets/kickoff-completion.js*',
                   lambda route: route.fulfill(path=ROOT / 'tools' / 'bench' / 'kickoff-completion.js'))
        page.route('**/assets/kickoff-completion-page.js*',
                   lambda route: route.fulfill(path=ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js'))
        page.route('**/api/kickoff/groups/**', api)

        page.goto('http://wemeet.test/kickoff/completion?group=group-1&member=owner')

        page.get_by_text('기준 없는 과제').wait_for()
        page.get_by_text('DB 연결 실패').wait_for()
        assert not page_errors, page_errors
        assert page.locator('body').get_attribute('data-completion-controller') == 'ready'
        assert page.evaluate("window.kickoffCompletionPage.completion === window.kickoffCompletion")
        assert page.evaluate("Object.keys(window.kickoffCompletion).sort()") == [
            'castVote', 'loadGroup', 'loadMemberPlan', 'loadMemberTasks', 'runReview', 'statusLabel',
            'submitAppeal', 'submitEvidence', 'validateEvidenceFiles',
        ]
        assert page.get_by_text('준비된 과제').is_visible()
        assert page.get_by_text('평가 기준 미확정').is_visible()
        assert page.locator('#completionProgressValue').inner_text() == '25%'
        assert page.locator('.completionMemberList').get_by_text('1/4 완료', exact=True).is_visible()
        cards = page.locator('.completionPageTask h4').all_text_contents()
        assert cards.index('자료 보완 과제') < cards.index('준비된 과제')
        page.get_by_role('button', name='자료 보완 과제').click()
        detail = page.locator('#completionTaskDetail')
        assert detail.get_by_role('heading', name='자료 보완 과제').is_visible()
        assert detail.get_by_text('제출자').is_visible()
        assert detail.get_by_text('자료 보완 필요').first.is_visible()
        assert detail.get_by_text('? 자료 부족').is_visible()
        assert detail.get_by_text('자료를 더 확인해야 합니다.').is_visible()
        assert detail.get_by_text('최종 화면이 없습니다.').is_visible()
        assert detail.get_by_text('최종 결과 화면').is_visible()
        assert detail.get_by_text('자료를 보완하거나 소명해주세요.').is_visible()
        assert detail.get_by_role('button', name='소명하기').is_visible()
        assert detail.get_by_role('button', name='새 자료 제출').is_visible()
        page.locator('.completionFilterList').get_by_role('button', name='자료 보완').click()
        assert page.get_by_role('button', name='자료 보완 과제').is_visible()
        assert page.get_by_role('button', name='준비된 과제').count() == 0
        page.locator('.completionFilterList').get_by_role('button', name='전체 과제').click()
        page.get_by_role('button', name='추가 확인 과제').click()
        assert detail.get_by_text('✕ 미충족').is_visible()
        assert detail.get_by_text('기준을 충족하지 못했습니다.').is_visible()
        assert detail.get_by_text('승인 기록이 없습니다.').is_visible()
        assert detail.get_by_role('button', name='소명하기').is_visible()
        assert detail.get_by_role('button', name='새 자료 제출').is_visible()
        completed = page.locator('.completionCompletedGroup')
        assert not completed.evaluate('element=>element.open')
        completed.locator('summary').click()
        assert page.get_by_role('button', name='완료 과제').is_visible()
        issue = page.locator('.completionIssue').filter(has_text='동료 · DB 연결 실패')
        assert issue.get_by_role('button', name='다시 시도').is_visible()
        for action in ('실행 계획으로 이동', '개인 계획 다시 생성', '그룹 선택으로 돌아가기'):
            assert issue.get_by_role('link', name=action).is_visible()
        issue.get_by_role('button', name='다시 시도').click()
        page.get_by_text('동료 과제').wait_for()
        assert page.get_by_text('준비된 과제').is_visible()
        assert page.locator('#completionProgressValue').inner_text() == '20%'
        page.locator('.completionFilterList').get_by_role('button', name='투표 필요').click()
        assert page.get_by_role('button', name='동료 과제').is_visible()
        assert page.get_by_text('내 투표가 필요함').is_visible()
        page.locator('.completionFilterList').get_by_role('button', name='내 과제').click()
        assert page.get_by_role('button', name='동료 과제').count() == 0
        assert page.get_by_role('button', name='준비된 과제').is_visible()
        page.get_by_role('button', name='준비된 과제').click()
        detail = page.locator('#completionTaskDetail')
        description = detail.get_by_label('제출 설명')
        file_input = detail.get_by_label('증빙 파일')
        assert description.get_attribute('maxlength') == '5000'
        file_input.set_input_files([
            {'name': f'proof-{index}.png', 'mimeType': 'image/png', 'buffer': b'png'}
            for index in range(4)
        ])
        detail.get_by_text('파일은 최대 3개까지 제출할 수 있습니다.').wait_for()
        file_input.set_input_files({'name': 'proof.png', 'mimeType': 'image/png', 'buffer': b'png'})
        assert detail.get_by_text('proof.png · PNG · 0.00MB').is_visible()
        description.fill('완료한 결과 화면입니다.')
        detail.get_by_role('button', name='제출 저장').click()
        detail.get_by_text('설명과 파일 선택은 유지됩니다.').wait_for()
        assert description.input_value() == '완료한 결과 화면입니다.'
        assert file_input.evaluate('element=>element.files.length') == 1
        detail.get_by_role('button', name='제출 저장').click()
        page.get_by_role('button', name='AI 완료 여부 검토').wait_for()
        assert submission_action_ids[0] and submission_action_ids[0] == submission_action_ids[1]
        assert page.get_by_text('제출 저장 완료').is_visible()
        page.get_by_role('button', name='AI 완료 여부 검토').click()
        page.get_by_role('button', name='AI 검토 다시 시도').wait_for()
        assert page.get_by_text('제출 저장 완료').is_visible()
        assert detail.get_by_text('AI 판정 실패').is_visible()
        assert detail.get_by_text('AI_REVIEW_FAILED').is_visible()
        assert detail.get_by_text('결정적 판정 fixture 실패').is_visible()
        assert review_attempts == 1
        page.get_by_role('button', name='AI 검토 다시 시도').click()
        detail.get_by_text('✓ 충족').wait_for()
        assert detail.get_by_text('모든 기준을 충족했습니다.').is_visible()
        assert detail.get_by_text('제출 화면에서 확인했습니다.').is_visible()
        assert detail.get_by_text('과제가 완료됐습니다.').is_visible()
        assert page.locator('#completionProgressValue').inner_text() == '40%'
        assert len(review_action_ids) == 2
        assert review_action_ids[0] and review_action_ids[1]
        assert review_action_ids[0] != review_action_ids[1]

        page.get_by_role('button', name='추가 확인 과제').click()
        detail.get_by_role('button', name='소명하기').click()
        appeal_reason = detail.get_by_label('소명 내용')
        appeal_reason.fill('승인 요청 기록을 함께 확인해주세요.')
        detail.get_by_role('button', name='소명 제출').click()
        detail.get_by_text('입력은 유지됩니다.').wait_for()
        assert appeal_reason.input_value() == '승인 요청 기록을 함께 확인해주세요.'
        detail.get_by_role('button', name='소명 제출').click()
        detail.get_by_text('과반 미달 · 투표 진행 중').wait_for()
        assert appeal_action_ids[0] and appeal_action_ids[0] == appeal_action_ids[1]
        assert detail.get_by_text('승인 요청 기록을 함께 확인해주세요.').is_visible()
        assert detail.get_by_text('필요한 찬성').is_visible()
        assert detail.get_by_text('현재 찬성').is_visible()
        assert detail.get_by_text('투표 가능').is_visible()
        assert detail.get_by_text('현재 투표자 · 제출자 · 현재 사용자').is_visible()
        assert detail.get_by_role('button', name='찬성', exact=True).is_disabled()
        assert detail.get_by_role('button', name='인정하지 않음').is_disabled()
        assert detail.get_by_text('제출자는 자신의 소명에 투표할 수 없습니다.').is_visible()

        page.evaluate("""
          window.kickoffCompletionPage.state.viewerMemberId='peer';
          window.kickoffCompletionPage.selectFilter('all');
          window.kickoffCompletionPage.selectTask('plan-1:review-task');
        """)
        assert detail.get_by_text('현재 투표자 · 동료 · 현재 사용자').is_visible()
        vote = detail.get_by_role('button', name='찬성', exact=True)
        assert vote.is_enabled()
        vote.click()
        detail.get_by_text('같은 투표로 다시 시도할 수 있습니다.').wait_for()
        detail.get_by_role('button', name='찬성', exact=True).click()
        detail.get_by_text('과반 도달 · 완료').wait_for()
        assert detail.get_by_text('내 투표 · 찬성').is_visible()
        assert detail.get_by_text('필요한 과반수에 도달해 과제가 완료됐습니다.').is_visible()
        assert vote_action_ids[0] and vote_action_ids[0] == vote_action_ids[1]
        assert page.locator('#completionProgressValue').inner_text() == '60%'

        page.evaluate("""
          window.kickoffCompletionPage.state.viewerMemberId='owner';
          window.kickoffCompletionPage.selectFilter('all');
          window.kickoffCompletionPage.selectTask('plan-1:input-task');
        """)
        detail.get_by_role('button', name='소명하기').click()
        detail.get_by_label('소명 내용').fill('기존 자료의 위치를 다시 설명합니다.')
        detail.get_by_role('button', name='소명 제출').click()
        detail.get_by_text('과반 미달 · 투표 진행 중').wait_for()
        page.evaluate("""
          window.kickoffCompletionPage.state.viewerMemberId='peer';
          window.kickoffCompletionPage.selectTask('plan-1:input-task');
        """)
        detail.get_by_role('button', name='인정하지 않음').click()
        detail.get_by_text('이미 투표함 · 인정하지 않음').wait_for()
        assert detail.get_by_text('과반 미달 · 투표 진행 중').is_visible()
        assert detail.get_by_role('button', name='찬성', exact=True).count() == 0
        page.evaluate("""
          window.kickoffCompletionPage.state.viewerMemberId='owner';
          window.kickoffCompletionPage.selectTask('plan-1:input-task');
        """)
        detail.get_by_label('제출 설명').fill('새 근거 자료를 제출합니다.')
        detail.get_by_role('button', name='새 자료 제출').click()
        detail.get_by_text('새 제출로 대체됨').wait_for()
        assert detail.get_by_text('새 자료가 제출되어 이 소명은 더 이상 투표할 수 없습니다.').is_visible()

        owner_reads = sum(path.endswith('/members/owner/completion/tasks') for path in calls)
        selected_url = page.url
        page.get_by_role('button', name='상태 새로고침').click()
        page.get_by_text('방금 DB 상태와 동기화했습니다.').wait_for()
        assert sum(path.endswith('/members/owner/completion/tasks') for path in calls) > owner_reads
        assert page.url == selected_url and 'task=input-task' in page.url
        assert detail.get_by_role('heading', name='자료 보완 과제').is_visible()

        page.reload()
        detail = page.locator('#completionTaskDetail')
        detail.get_by_role('heading', name='자료 보완 과제').wait_for()
        assert 'task=input-task' in page.url
        assert detail.locator('.completionReadinessBadge').get_by_text('제출 저장됨').is_visible()
        assert detail.get_by_text('새 제출로 대체됨').is_visible()
        completed = page.locator('.completionCompletedGroup')
        completed.locator('summary').click()
        page.get_by_role('button', name='준비된 과제').click()
        assert detail.get_by_text('모든 기준을 충족했습니다.').is_visible()
        assert detail.get_by_text('완료된 과제에는 새 결과를 제출할 수 없습니다.').is_visible()
        page.get_by_role('button', name='추가 확인 과제').click()
        assert detail.get_by_text('과반 도달 · 완료').is_visible()
        assert detail.locator('.completionAppealMetrics dd').all_text_contents() == ['1명', '1명', '1명']
        assert detail.get_by_role('button', name='새 자료 제출').count() == 0

        page.set_viewport_size({'width': 820, 'height': 900})
        tablet_columns = page.locator('#completionPageLayout').evaluate(
            "element=>getComputedStyle(element).gridTemplateColumns.split(' ').filter(Boolean).length")
        assert tablet_columns == 3
        assert page.locator('.completionPageOverview').bounding_box()['y'] == page.locator('.completionPageDetail').bounding_box()['y']

        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert page.locator('#completionPageLayout').evaluate("element=>getComputedStyle(element).display") == 'block'
        assert page.locator('.completionFilterList').evaluate("element=>getComputedStyle(element).overflowX") == 'auto'
        input_task = page.get_by_role('button', name='자료 보완 과제')
        input_task.focus()
        page.keyboard.press('Enter')
        page.wait_for_function("document.activeElement.id === 'completionTaskDetail'")
        assert detail.bounding_box()['width'] <= 390
        assert any(path.endswith('/members/owner/completion/tasks') for path in calls)
        assert any(path.endswith('/members/peer/completion/tasks') for path in calls)
        browser.close()


def test_baseline_not_ready_recovers_after_plan_confirmation_and_reentry():
    html = (ROOT / 'tools' / 'bench' / 'kickoff-completion.html').read_text(encoding='utf-8')
    baseline_ready = False

    def api(route):
        path = route.request.url.split('wemeet.test', 1)[-1]
        if path == '/api/kickoff/groups/group-1':
            value = {'group_id': 'group-1', 'title': '기준 준비 그룹', 'members': [
                {'member_id': 'owner', 'nickname': '제출자'},
            ]}
        elif path.endswith('/members/owner/personal'):
            value = {'planning': {'plan': {'code_status': 'CURRENT', 'result': {'data': {
                'plan_version_id': 'plan-1', 'tasks': [{
                    'task_id': 'task-1', 'title': '기준 준비 과제', 'deliverable': '결과 화면',
                    'due_date': '2026-11-20',
                }],
            }}}}}
        elif path.endswith('/members/owner/completion/tasks'):
            tasks = []
            if baseline_ready:
                tasks = [{
                    'plan_version_id': 'plan-1', 'task_id': 'task-1', 'title': '기준 준비 과제',
                    'deliverable': '결과 화면', 'completion_criteria': ['결과 화면을 제출한다'],
                    'due_date': '2026-11-20', 'status': 'TODO', 'revision': 0,
                    'latest_submission': None, 'latest_review': None, 'latest_appeal': None,
                }]
            value = {'group_id': 'group-1', 'member_id': 'owner', 'tasks': tasks}
        else:
            route.fulfill(status=500, body=json.dumps({'error': {'message': 'unexpected ' + path}}),
                          content_type='application/json')
            return
        route.fulfill(body=json.dumps(value), content_type='application/json')

    def confirm(route):
        nonlocal baseline_ready
        baseline_ready = True
        route.fulfill(body='{}', content_type='application/json')

    kickoff = """<!doctype html><html lang="ko"><body>
    <h1>실행 계획</h1><button id="confirmPlan">계획 확정</button>
    <script>confirmPlan.onclick=async()=>{await fetch('/fixture/confirm',{method:'POST'});
    location.href='/kickoff/completion?group=group-1&member=owner&owner=owner&task=task-1';};</script>
    </body></html>"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('http://wemeet.test/kickoff/completion?*',
                   lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        page.route('http://wemeet.test/kickoff?group=group-1',
                   lambda route: route.fulfill(body=kickoff, content_type='text/html; charset=utf-8'))
        page.route('http://wemeet.test/fixture/confirm', confirm)
        page.route('**/assets/kickoff.css*',
                   lambda route: route.fulfill(path=ROOT / 'tools' / 'bench' / 'kickoff.css',
                                                content_type='text/css'))
        page.route('**/assets/kickoff-completion.js*',
                   lambda route: route.fulfill(path=ROOT / 'tools' / 'bench' / 'kickoff-completion.js'))
        page.route('**/assets/kickoff-completion-page.js*',
                   lambda route: route.fulfill(path=ROOT / 'tools' / 'bench' / 'kickoff-completion-page.js'))
        page.route('**/api/kickoff/groups/**', api)

        page.goto('http://wemeet.test/kickoff/completion?group=group-1&member=owner')
        page.get_by_text('평가 기준 미확정').wait_for()
        assert page.get_by_role('button', name='기준 준비 과제').is_visible()
        issue = page.locator('.completionIssue').filter(has_text='평가 기준 미확정')
        assert issue.get_by_role('link', name='실행 계획으로 이동').get_attribute('href') == '/kickoff?group=group-1'
        issue.get_by_role('link', name='실행 계획으로 이동').click()
        page.get_by_role('button', name='계획 확정').click()
        page.get_by_text('제출 전').wait_for()
        page.get_by_role('button', name='기준 준비 과제').wait_for()
        page.get_by_text('평가 기준 미확정').wait_for(state='detached')
        assert 'task=task-1' in page.url
        browser.close()
