/* Dedicated completion page: group progress, filters, task list, and detail selection. */
(()=>{
const params=new URLSearchParams(location.search),groupId=params.get('group')?.trim()||'';
const completion=window.kickoffCompletion,$=id=>document.getElementById(id);
const missing=$('completionMissingGroup'),layout=$('completionPageLayout'),back=$('backToKickoff'),marker=$('completionGroupMarker');
const refreshButton=$('refreshCompletionState'),syncStatus=$('completionSyncStatus'),AUTO_SYNC_MS=30000;
const state={epoch:0,syncEpoch:0,group:null,members:new Map(),memberEpoch:new Map(),viewerMemberId:params.get('member')||'',selectedMemberId:params.get('owner')||'',selectedTaskKey:'',requestedTaskId:params.get('task')||'',filter:'all',completedOpen:false,loading:false,syncing:false,groupIssue:null,lastTrace:null};
const errorLabels={STORAGE_FAILED:'DB 연결 실패',GOAL_DATABASE_NOT_CONFIGURED:'DB 연결 실패',BASELINE_NOT_READY:'평가 기준 미확정',PLAN_VERSION_MISMATCH:'계획 버전 불일치',TASK_NOT_FOUND:'계획 버전 불일치',NO_TASKS:'과제 없음',TASK_OWNER_MISMATCH:'구성원 범위 오류',INPUT_INVALID:'구성원 범위 오류',SERVER_CODE_MISMATCH:'서버 코드 버전 불일치',CONTROLLER_NOT_READY:'완료 컨트롤러 준비 실패',UNEXPECTED_ERROR:'예상하지 못한 조회 오류'};
const reviewStates=new Set(['SUBMITTED','REVIEWING','REVIEW_NEEDED','REVIEW_FAILED']);

if(!groupId){missing.hidden=false;layout.hidden=true;return;}
document.body.dataset.groupId=groupId;document.body.dataset.completionController=completion?'ready':'missing';marker.textContent='그룹 ID · '+groupId;back.href='/kickoff?group='+encodeURIComponent(groupId);

function element(tag,text,className){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;}
function domToken(value){return String(value||'field').replace(/[^a-zA-Z0-9_-]/g,'-');}
function trace(request,response){state.lastTrace={request,response};}
function issue(code,message,member=null){return {code:code||'UNEXPECTED_ERROR',title:errorLabels[code]||errorLabels.UNEXPECTED_ERROR,message:message||'오류 메시지가 제공되지 않았습니다.',memberId:member?.member_id||'',memberName:member?.nickname||''};}
function errorIssue(error,member){return issue(error.code||'UNEXPECTED_ERROR',error.message,member);}
function taskKey(planId,taskId){return planId+':'+taskId;}
function currentPlan(personal){return personal?.planning?.plan?.result?.data||null;}
function buildMemberResult(member,completionResult,personalResult){
  const output={member,ready:[],unready:[],issues:[],loading:false},fetched=completionResult.status==='fulfilled'?(completionResult.value.tasks||[]):[];
  if(completionResult.status==='rejected')output.issues.push(errorIssue(completionResult.reason,member));
  if(personalResult.status==='rejected'){output.ready=fetched;output.issues.push(errorIssue(personalResult.reason,member));return output;}
  const personal=personalResult.value,plan=currentPlan(personal),planTasks=plan?.tasks||[];
  output.ready=plan?fetched.filter(task=>task.plan_version_id===plan.plan_version_id):fetched;
  const readyKeys=new Set(output.ready.map(task=>taskKey(task.plan_version_id,task.task_id)));
  if(completionResult.status==='fulfilled'){
    output.unready=planTasks.filter(task=>!readyKeys.has(taskKey(plan.plan_version_id,task.task_id))).map(task=>({...task,plan_version_id:plan.plan_version_id,status:'BASELINE_NOT_READY',revision:0}));
    if(output.unready.length)output.issues.push(issue('BASELINE_NOT_READY',output.unready.length+'개 과제의 평가 기준이 아직 확정되지 않았습니다.',member));
    if(plan&&fetched.length&&!output.ready.length)output.issues.push(issue('PLAN_VERSION_MISMATCH','현재 개인 계획과 완료 조회의 계획 버전이 일치하지 않습니다.',member));
  }
  const codeStatus=personal?.planning?.plan?.code_status;if(plan&&codeStatus&&codeStatus!=='CURRENT')output.issues.push(issue('SERVER_CODE_MISMATCH','개인 계획이 현재 서버 코드가 아닌 '+codeStatus+' 상태입니다.',member));
  if(!planTasks.length&&!output.ready.length)output.issues.push(issue('NO_TASKS','확정된 개인 계획 과제가 없습니다.',member));return output;
}
function allRows(){return [...state.members.values()];}
function scopedRows(){const rows=allRows();if(state.filter==='mine')return rows.filter(row=>row.member.member_id===state.viewerMemberId);return state.selectedMemberId?rows.filter(row=>row.member.member_id===state.selectedMemberId):rows;}
function allEntries(rows=allRows()){const entries=[];for(const row of rows){for(const task of row.ready)entries.push({task,member:row.member,readiness:'ready',key:taskKey(task.plan_version_id,task.task_id)});for(const task of row.unready)entries.push({task,member:row.member,readiness:'unready',key:taskKey(task.plan_version_id,task.task_id)});}return entries;}
function dueValue(task){return task.due_date||'9999-12-31';}
function sortTasks(a,b){const completedA=a.task.status==='COMPLETED',completedB=b.task.status==='COMPLETED';if(completedA!==completedB)return completedA?1:-1;return dueValue(a.task).localeCompare(dueValue(b.task))||String(a.task.title||'').localeCompare(String(b.task.title||''),'ko');}
function matchesFilter(entry){const status=entry.task.status;if(state.filter==='all'||state.filter==='mine')return true;if(state.filter==='todo')return status==='TODO'||status==='BASELINE_NOT_READY';if(state.filter==='review')return reviewStates.has(status);if(state.filter==='input')return status==='NEEDS_INPUT';if(state.filter==='appeal')return status==='APPEALED'&&entry.member.member_id!==state.viewerMemberId;if(state.filter==='completed')return status==='COMPLETED';return true;}
function needsViewerAction(entry){const mine=entry.member.member_id===state.viewerMemberId,status=entry.task.status;return mine&&['TODO','BASELINE_NOT_READY','SUBMITTED','REVIEW_FAILED','REVIEW_NEEDED','NEEDS_INPUT'].includes(status)||!mine&&status==='APPEALED';}
function issueActions(value){
  const actions=element('div',undefined,'completionIssueActions'),retry=element('button','다시 시도');retry.type='button';retry.dataset.retryMember=value.memberId;
  const plan=element('a','실행 계획으로 이동');plan.href='/kickoff?group='+encodeURIComponent(groupId);
  const regenerate=element('a','개인 계획 다시 생성');regenerate.href='/kickoff?group='+encodeURIComponent(groupId)+(value.memberId?'&member='+encodeURIComponent(value.memberId):'');
  const groups=element('a','그룹 선택으로 돌아가기');groups.href='/kickoff';actions.append(retry,plan,regenerate,groups);return actions;
}
function renderIssue(value){const card=element('article',undefined,'completionIssue'),header=element('div',undefined,'completionIssueHeader');header.append(element('h3',(value.memberName?value.memberName+' · ':'')+value.title),element('span',value.code,'completionIssueCode'));card.append(header,element('p',value.message),issueActions(value));return card;}
function completedCount(row){return row.ready.filter(task=>task.status==='COMPLETED').length;}
function renderMembers(){
  const target=$('completionMemberList');target.replaceChildren();if(!state.group)return;
  const ready=allRows().reduce((sum,row)=>sum+row.ready.length,0),done=allRows().reduce((sum,row)=>sum+completedCount(row),0),all=element('button');all.type='button';all.dataset.member='';all.setAttribute('aria-pressed',String(!state.selectedMemberId));all.append(element('span','모든 구성원'),element('small',done+'/'+ready+' 완료'));target.append(all);
  for(const member of state.group.members||[]){const row=state.members.get(member.member_id),button=element('button');button.type='button';button.dataset.member=member.member_id;button.setAttribute('aria-pressed',String(state.selectedMemberId===member.member_id));const count=row?.loading?'불러오는 중':row?(completedCount(row)+'/'+row.ready.length+' 완료'+(row.unready.length?' · 준비 '+row.unready.length:'')):'대기';button.append(element('span',memberName(member.member_id)),element('small',count));target.append(button);}
}
function statusBadge(entry){const text=entry.readiness==='ready'?completion.statusLabel(entry.task.status):'준비 필요';return element('span',text,'completionReadinessBadge status-'+String(entry.task.status).toLowerCase());}
function taskCard(entry){
  const card=element('button',undefined,'completionPageTask');card.type='button';card.dataset.readiness=entry.readiness;card.dataset.taskKey=entry.key;card.setAttribute('aria-pressed',String(entry.key===state.selectedTaskKey));if(entry.key===state.selectedTaskKey)card.classList.add('selected');
  const header=element('div',undefined,'completionPageTaskHeader'),title=element('div',undefined,'completionPageTaskTitle'),mine=entry.member.member_id===state.viewerMemberId;title.append(element('span',mine?'내 과제':memberName(entry.member.member_id)+' 담당','completionOwnerBadge'),element('h4',entry.task.title||'이름 없는 과제'));header.append(title,statusBadge(entry));card.append(header,element('p','결과물 · '+(entry.task.deliverable||'확인 필요')));
  const meta=element('div',undefined,'completionPageTaskMeta');meta.append(element('span','담당 · '+memberName(entry.member.member_id)),element('span','마감 · '+(entry.task.due_date||'미정')));if(needsViewerAction(entry))meta.append(element('span',entry.member.member_id===state.viewerMemberId?'내가 처리할 과제':'내 투표가 필요함','completionActionHint'));card.append(meta);return card;
}
function taskSection(title,entries,{completed=false}={}){
  const container=completed?element('details',undefined,'completionTaskSection completionCompletedGroup'):element('section',undefined,'completionTaskSection');
  if(completed){container.open=state.completedOpen||state.filter==='completed';const summary=element('summary');summary.append(document.createTextNode(title+' '),element('small',entries.length+'개'));container.append(summary);container.addEventListener('toggle',()=>{state.completedOpen=container.open;});}
  else{const heading=element('h3');heading.append(document.createTextNode(title+' '),element('small',entries.length+'개'));container.append(heading);}
  const list=element('div',undefined,'completionTaskRows');for(const entry of entries)list.append(taskCard(entry));container.append(list);return container;
}
function memberTaskGroup(member,entries){
  const group=element('section',undefined,'completionMemberTaskGroup'),mine=member.member_id===state.viewerMemberId,active=entries.filter(entry=>entry.task.status!=='COMPLETED'),completed=entries.filter(entry=>entry.task.status==='COMPLETED'),heading=element('div',undefined,'completionMemberTaskGroupHeader');heading.append(element('h3',memberName(member.member_id)+(mine?' · 내 과제':'의 과제')),element('small',completed.length+'/'+entries.length+' 완료'));group.append(heading);if(active.length)group.append(taskSection('진행 중',active));if(completed.length)group.append(taskSection('완료',completed,{completed:true}));return group;
}
function renderProgress(){const ready=allEntries().filter(entry=>entry.readiness==='ready'),completed=ready.filter(entry=>entry.task.status==='COMPLETED').length,total=ready.length,percent=total?Math.round(completed/total*100):0;const value=$('completionProgressValue'),card=$('completionProgressCard');value.textContent=percent+'%';value.title=completed+' / '+total+' 완료';card.setAttribute('aria-valuenow',String(percent));card.setAttribute('aria-valuetext',completed+'개 완료 / '+total+'개 평가 준비 과제');card.setAttribute('aria-label','그룹 진행률 '+percent+'퍼센트 · '+completed+'개 완료 / '+total+'개 평가 준비 과제');}
function findEntry(key){return allEntries().find(entry=>entry.key===key)||null;}
function submissionDraftKey(entry){return 'kickoff:'+groupId+':completion-submission:'+entry.key;}
function appealDraftKey(entry){return 'kickoff:'+groupId+':completion-appeal:'+(entry.task.latest_review?.review_id||entry.key);}
function voteRecordKey(appealId){return 'kickoff:'+groupId+':completion-vote:'+appealId+':'+state.viewerMemberId;}
function supersededAppealKey(entry){return 'kickoff:'+groupId+':completion-superseded-appeal:'+entry.key;}
function readStored(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}
function memberName(memberId){const member=state.group?.members?.find(value=>value.member_id===memberId),raw=String(member?.nickname||'').trim(),nickname=raw&&!/^[□�\s]+$/.test(raw)?raw:'이름 미설정';return nickname+(memberId===state.viewerMemberId?' · 현재 사용자':'');}
function fileType(file){const labels={'image/png':'PNG','image/jpeg':'JPEG','application/pdf':'PDF','application/vnd.openxmlformats-officedocument.wordprocessingml.document':'DOCX'};return labels[file.type]||file.name.split('.').pop()?.toUpperCase()||'형식 미상';}
function fileSummary(file){return file.name+' · '+fileType(file)+' · '+(file.size/1024/1024).toFixed(2)+'MB';}
function renderSelectedFiles(input,list,status){
  const files=[...input.files];list.replaceChildren(...files.map(file=>element('li',fileSummary(file))));
  try{completion.validateEvidenceFiles(files);status.textContent=files.length?files.length+'개 파일 · 제출 전 검사를 통과했습니다.':'파일 없이 설명만 제출할 수 있습니다.';status.classList.remove('error');}
  catch(error){status.textContent=error.message;status.classList.add('error');}
}
function latestSubmissionCard(entry){
  const submission=entry.task.latest_submission;if(!submission)return null;const card=element('section',undefined,'completionSavedSubmission');card.append(element('strong','제출 저장 완료'),element('p',submission.description||'제출 설명 없음'));
  const files=element('ul');for(const file of submission.evidence||[])files.append(element('li',(file.original_name||'증빙 파일')+' · '+(file.mime_type||'형식 미상')+' · '+((file.size_bytes||0)/1024/1024).toFixed(2)+'MB'));if(files.childElementCount)card.append(files);return card;
}
function criterionResult(item){
  const labels={MET:'✓ 충족',UNKNOWN:'? 자료 부족',NOT_MET:'✕ 미충족'},row=element('li',undefined,'completionReviewCriterion');row.dataset.status=item.status;
  const heading=element('div',undefined,'completionReviewCriterionHeading');heading.append(element('strong',labels[item.status]||item.status),element('span',item.criterion||('기준 '+(Number(item.criterion_index)+1))));row.append(heading);if(item.reason)row.append(element('p',item.reason));return row;
}
function renderReview(entry,target){
  if(entry.task.status==='REVIEW_FAILED'){
    const failed=element('section',undefined,'completionReviewFailure'),header=element('div',undefined,'completionReviewFailureHeader');failed.setAttribute('role','alert');header.append(element('strong','AI 판정 실패'),element('code',entry.task.review_error_code||'AI_REVIEW_FAILED'));failed.append(header,element('p',entry.task.review_error_message||'AI 판정을 완료하지 못했습니다. 저장된 제출로 다시 시도할 수 있습니다.'));target.append(failed);
  }
  const review=entry.task.latest_review;if(!review)return;
  const section=element('section',undefined,'completionReviewResult'),header=element('div',undefined,'completionReviewResultHeader'),decision={PASS:'완료',NEEDS_INPUT:'자료 보완 필요',REVIEW_NEEDED:'추가 확인 필요'}[review.decision]||review.decision;section.setAttribute('aria-live','polite');header.append(element('h4','AI 판정 결과'),element('span',decision,'completionReviewDecision status-'+String(review.decision).toLowerCase()));section.append(header);
  if(review.summary)section.append(element('p',review.summary,'completionReviewSummary'));const criteria=element('ul',undefined,'completionReviewCriteria');for(const item of review.criteria_results||[])criteria.append(criterionResult(item));if(criteria.childElementCount)section.append(criteria);
  if(review.missing_items?.length){section.append(element('h5','부족한 자료'));const missing=element('ul',undefined,'completionReviewMissing');for(const item of review.missing_items)missing.append(element('li',item));section.append(missing);}
  if(review.user_message)section.append(element('p',review.user_message,'completionReviewMessage'));
  if(['NEEDS_INPUT','REVIEW_NEEDED'].includes(entry.task.status)&&entry.member.member_id===state.viewerMemberId&&!entry.task.latest_appeal){
    const actions=element('div',undefined,'completionReviewActions'),appeal=element('button','소명하기'),form=element('form',undefined,'completionAppealForm');appeal.type='button';appeal.dataset.appealReview=review.review_id;
    const note=element('p','새 자료를 제출하거나 현재 판정에 소명할 수 있습니다.','completionReviewChoice'),label=element('label','소명 내용'),reason=element('textarea'),eligible=Math.max(0,(state.group?.members?.length||1)-1),required=Math.floor(eligible/2)+1,votes=element('p','필요한 찬성 '+required+'명 · 현재 찬성 0명 · 투표 가능 '+eligible+'명','completionAppealVoteSummary'),status=element('p','소명 제출 후 다른 구성원이 투표할 수 있습니다.','completionSubmissionStatus'),submit=element('button','소명 제출','primary'),reasonId='completion-appeal-'+domToken(entry.key);
    reason.id=reasonId;label.htmlFor=reasonId;reason.required=true;reason.maxLength=5000;reason.rows=4;reason.value=localStorage.getItem(appealDraftKey(entry))||'';reason.placeholder='AI 판정을 다시 확인해야 하는 이유와 근거 위치를 적어주세요.';label.append(reason);status.setAttribute('role','status');submit.type='submit';form.hidden=true;form.append(label,votes,status,submit);reason.addEventListener('input',()=>localStorage.setItem(appealDraftKey(entry),reason.value));form.addEventListener('submit',event=>appealSelected(event,entry));appeal.addEventListener('click',()=>{form.hidden=!form.hidden;appeal.setAttribute('aria-expanded',String(!form.hidden));if(!form.hidden)reason.focus();});appeal.setAttribute('aria-expanded','false');actions.append(appeal);section.append(note,actions,form);
  }
  target.append(section);
}
async function appealSelected(event,entry){
  event.preventDefault();const form=event.currentTarget,reason=form.querySelector('textarea'),status=form.querySelector('[role=status]'),button=form.querySelector('button[type=submit]');button.disabled=true;reason.disabled=true;status.textContent='소명을 저장하는 중…';status.classList.remove('error');
  try{await completion.submitAppeal({groupId,memberId:entry.member.member_id,reviewId:entry.task.latest_review.review_id,reason:reason.value,onTrace:trace});localStorage.removeItem(appealDraftKey(entry));localStorage.removeItem(supersededAppealKey(entry));status.textContent='소명을 저장했습니다. 동료 투표를 기다립니다.';await retryMember(entry.member.member_id);}
  catch(error){status.textContent=(error.code?error.code+' · ':'')+error.message+' 입력은 유지됩니다.';status.classList.add('error');button.disabled=false;reason.disabled=false;}
}
function appealState(entry){return entry.task.latest_appeal||readStored(supersededAppealKey(entry));}
function renderAppeal(entry,target){
  const appeal=appealState(entry);if(!appeal)return;const section=element('section',undefined,'completionAppealDetail'),header=element('div',undefined,'completionAppealHeader'),labels={OPEN:'과반 미달 · 투표 진행 중',APPROVED:'과반 도달 · 완료',SUPERSEDED:'새 제출로 대체됨'},badge=element('span',labels[appeal.status]||appeal.status,'completionAppealState status-'+String(appeal.status).toLowerCase());section.setAttribute('aria-live','polite');header.append(element('h4','소명과 동료 투표'),badge);section.append(header);
  if(appeal.reason)section.append(element('p',appeal.reason,'completionAppealReason'));const metrics=element('dl',undefined,'completionAppealMetrics');for(const [label,value] of [['필요한 찬성',appeal.required_votes+'명'],['현재 찬성',appeal.approve_votes+'명'],['투표 가능',appeal.eligible_voters+'명']]){const metric=element('div');metric.append(element('dt',label),element('dd',value));metrics.append(metric);}section.append(metrics);
  const voter=state.viewerMemberId,record=readStored(voteRecordKey(appeal.appeal_id));if(record)section.append(element('p','내 투표 · '+(record.approve===true?'찬성':record.approve===false?'인정하지 않음':'이미 투표함'),'completionVoteRecord'));
  if(appeal.status==='SUPERSEDED'){section.append(element('p','새 자료가 제출되어 이 소명은 더 이상 투표할 수 없습니다.','completionAppealNotice'));target.append(section);return;}
  if(appeal.status==='APPROVED'){section.append(element('p','필요한 과반수에 도달해 과제가 완료됐습니다.','completionAppealNotice'));target.append(section);return;}
  const self=voter===entry.member.member_id,identity=element('dl',undefined,'completionVoteIdentity');for(const [label,value] of [['소명 제출자',memberName(entry.member.member_id)],['현재 사용자',memberName(voter)],['내 역할',self?'소명 제출자 · 투표 불가':'완료 인정 투표']]){const item=element('div');item.append(element('dt',label),element('dd',value));identity.append(item);}const voterLine=element('p','현재 투표자 · '+memberName(voter),'completionCurrentVoter'),status=element('p',undefined,'completionSubmissionStatus');status.setAttribute('role','status');section.append(identity,voterLine);
  if(record){status.textContent='이미 투표함 · '+(record.approve===true?'찬성':record.approve===false?'인정하지 않음':'결과 확인 필요');section.append(status);target.append(section);return;}
  status.textContent=self?'제출자는 자신의 소명에 투표할 수 없습니다.':'아직 투표하지 않았습니다.';const actions=element('div',undefined,'completionVoteActions'),approve=element('button','찬성','primary'),reject=element('button','인정하지 않음');approve.type=reject.type='button';approve.disabled=reject.disabled=self;approve.addEventListener('click',()=>voteSelected(entry,appeal,true,approve,reject,status));reject.addEventListener('click',()=>voteSelected(entry,appeal,false,approve,reject,status));actions.append(approve,reject);section.append(status,actions);target.append(section);
}
async function voteSelected(entry,appeal,approve,approveButton,rejectButton,status){
  approveButton.disabled=rejectButton.disabled=true;status.textContent='투표를 저장하는 중…';status.classList.remove('error');
  try{await completion.castVote({groupId,appealId:appeal.appeal_id,voterMemberId:state.viewerMemberId,approve,onTrace:trace});localStorage.setItem(voteRecordKey(appeal.appeal_id),JSON.stringify({approve}));status.textContent='투표를 저장했습니다.';await retryMember(entry.member.member_id);}
  catch(error){if(error.code==='VOTE_ALREADY_CAST'){localStorage.setItem(voteRecordKey(appeal.appeal_id),JSON.stringify({approve:null}));status.textContent='이미 투표한 구성원입니다. 저장된 결과는 변경할 수 없습니다.';}else{status.textContent=(error.code?error.code+' · ':'')+error.message+' 같은 투표로 다시 시도할 수 있습니다.';status.classList.add('error');approveButton.disabled=rejectButton.disabled=false;}}
}
async function submitSelected(event,entry){
  event.preventDefault();const form=event.currentTarget,description=form.querySelector('textarea'),files=form.querySelector('input[type=file]'),status=form.querySelector('[role=status]'),button=form.querySelector('button[type=submit]');
  try{completion.validateEvidenceFiles(files.files);}catch(error){status.textContent=error.message;status.classList.add('error');return;}
  button.disabled=true;description.disabled=true;files.disabled=true;status.textContent='제출을 저장하는 중…';status.classList.remove('error');
  try{
    await completion.submitEvidence({groupId,memberId:state.viewerMemberId,planId:entry.task.plan_version_id,taskId:entry.task.task_id,description:description.value,files:files.files,onTrace:trace});
    if(entry.task.latest_appeal)localStorage.setItem(supersededAppealKey(entry),JSON.stringify({...entry.task.latest_appeal,status:'SUPERSEDED'}));
    localStorage.removeItem(submissionDraftKey(entry));status.textContent='제출을 저장했습니다. AI 판정은 별도 버튼으로 실행합니다.';await retryMember(entry.member.member_id);
  }catch(error){status.textContent=(error.code?error.code+' · ':'')+error.message+' 설명과 파일 선택은 유지됩니다.';status.classList.add('error');button.disabled=false;description.disabled=false;files.disabled=false;}
}
async function reviewSelected(entry,button,status){
  if(!entry.task.latest_submission)return;button.disabled=true;status.textContent='저장된 제출로 AI 판정을 요청하는 중…';status.classList.remove('error');
  try{await completion.runReview({groupId,memberId:entry.member.member_id,submissionId:entry.task.latest_submission.submission_id,onTrace:trace});status.textContent='AI 판정을 저장했습니다.';}
  catch(error){status.textContent=(error.code?error.code+' · ':'')+error.message+' 제출 저장 상태는 유지됩니다.';status.classList.add('error');}
  finally{await retryMember(entry.member.member_id);}
}
function renderSubmission(entry,target){
  const area=element('section',undefined,'completionSubmissionArea'),saved=latestSubmissionCard(entry);area.append(element('h4','결과 제출'));if(saved)area.append(saved);
  if(entry.member.member_id!==state.viewerMemberId){area.append(element('p','담당자만 이 과제의 결과를 제출할 수 있습니다.','completionSubmissionNotice'));target.append(area);return;}
  if(entry.readiness!=='ready'){area.append(element('p','평가 기준을 먼저 확정한 뒤 결과를 제출할 수 있습니다.','completionSubmissionNotice'));target.append(area);return;}
  if(entry.task.status==='COMPLETED'){area.append(element('p','완료된 과제에는 새 결과를 제출할 수 없습니다.','completionSubmissionNotice'));target.append(area);return;}
  if(entry.task.status==='REVIEWING'){area.append(element('p','AI 판정이 진행 중입니다. 제출 저장 상태는 유지됩니다.','completionSubmissionNotice'));target.append(area);return;}
  if(entry.task.latest_submission&&['SUBMITTED','REVIEW_FAILED'].includes(entry.task.status)){
    const reviewStatus=element('p',entry.task.status==='REVIEW_FAILED'?'AI 판정에 실패했습니다. 저장된 제출로 다시 시도할 수 있습니다.':'제출이 저장됐습니다. 파일을 다시 선택하지 않고 판정을 실행할 수 있습니다.','completionSubmissionStatus');reviewStatus.setAttribute('role','status');
    const review=element('button',entry.task.status==='REVIEW_FAILED'?'AI 검토 다시 시도':'AI 완료 여부 검토','primary');review.type='button';review.addEventListener('click',()=>reviewSelected(entry,review,reviewStatus));area.append(reviewStatus,review);
    if(entry.task.status==='SUBMITTED'){target.append(area);return;}
  }
  const form=element('form',undefined,'completionSubmissionForm'),descriptionLabel=element('label','제출 설명'),description=element('textarea'),fileLabel=element('label','증빙 파일'),input=element('input'),hint=element('p','PNG·JPEG·PDF·DOCX · 최대 3개 · 파일당 10MB · 전체 20MB','hint'),list=element('ul',undefined,'completionSelectedFiles'),status=element('p','파일 없이 설명만 제출할 수 있습니다.','completionSubmissionStatus'),submit=element('button',entry.task.latest_submission?'새 자료 제출':'제출 저장','primary'),fieldToken=domToken(entry.key),descriptionId='completion-description-'+fieldToken,fileId='completion-files-'+fieldToken;
  description.id=descriptionId;descriptionLabel.htmlFor=descriptionId;description.required=true;description.maxLength=5000;description.rows=5;description.placeholder='완료한 내용과 증빙에서 확인할 위치를 적어주세요.';description.value=localStorage.getItem(submissionDraftKey(entry))||'';descriptionLabel.append(description);
  input.id=fileId;fileLabel.htmlFor=fileId;input.type='file';input.multiple=true;input.accept='.png,.jpg,.jpeg,.pdf,.docx,image/png,image/jpeg,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document';fileLabel.append(input);status.setAttribute('role','status');submit.type='submit';
  description.addEventListener('input',()=>localStorage.setItem(submissionDraftKey(entry),description.value));input.addEventListener('change',()=>renderSelectedFiles(input,list,status));form.addEventListener('submit',event=>submitSelected(event,entry));form.append(descriptionLabel,fileLabel,hint,list,status,submit);area.append(form);target.append(area);
}
function renderDetail(entry){
  const target=$('completionTaskDetail');target.tabIndex=-1;target.replaceChildren();if(!entry){target.className='completionPageEmpty';target.append(element('p','선택한 과제가 없습니다.'));return;}
  target.className='completionTaskDetailContent';const heading=element('div',undefined,'completionTaskDetailHeading');heading.append(element('div',undefined),statusBadge(entry));heading.firstChild.append(element('span','선택한 과제','eyebrow'),element('h3',entry.task.title));target.append(heading);
  const facts=element('dl',undefined,'completionTaskFacts');for(const [label,value] of [['담당자',memberName(entry.member.member_id)],['결과물',entry.task.deliverable||'확인 필요'],['마감일',entry.task.due_date||'미정'],['현재 상태',entry.readiness==='ready'?completion.statusLabel(entry.task.status):'평가 기준 준비 필요']]){facts.append(element('dt',label),element('dd',value));}target.append(facts);
  target.append(element('h4','완료 기준'));const criteria=element('ul');for(const text of entry.task.completion_criteria||[])criteria.append(element('li',text));if(!criteria.childElementCount)criteria.append(element('li','완료 기준을 실행 계획에서 확인해주세요.'));target.append(criteria);
  if(needsViewerAction(entry))target.append(element('p',entry.member.member_id===state.viewerMemberId?'현재 내가 처리해야 하는 과제입니다.':'현재 내 투표가 필요한 과제입니다.','completionDetailAction'));renderReview(entry,target);renderAppeal(entry,target);renderSubmission(entry,target);
}
function selectTask(key){const entry=findEntry(key);if(!entry)return;state.selectedTaskKey=key;state.requestedTaskId=entry.task.task_id;params.set('task',entry.task.task_id);history.replaceState(null,'',location.pathname+'?'+params.toString());renderContent();if(matchMedia('(max-width:720px)').matches)requestAnimationFrame(()=>{const detail=$('completionTaskDetail');detail.scrollIntoView({block:'start'});detail.focus({preventScroll:true});});}
function renderContent(){
  const rows=scopedRows(),issues=[];if(state.groupIssue)issues.push(state.groupIssue);for(const row of rows)issues.push(...row.issues);$('completionPageIssues').replaceChildren(...issues.map(renderIssue));
  const visible=allEntries(rows).filter(matchesFilter).sort(sortTasks);if(!state.selectedTaskKey&&state.requestedTaskId){const requested=visible.find(entry=>entry.task.task_id===state.requestedTaskId);if(requested)state.selectedTaskKey=requested.key;}
  if(!visible.some(entry=>entry.key===state.selectedTaskKey))state.selectedTaskKey=visible.find(entry=>entry.task.status!=='COMPLETED')?.key||visible[0]?.key||'';
  const active=visible.filter(entry=>entry.task.status!=='COMPLETED'),completed=visible.filter(entry=>entry.task.status==='COMPLETED'),target=$('completionTaskList');target.replaceChildren();if(!state.selectedMemberId&&state.filter!=='mine'){for(const member of state.group?.members||[]){const entries=visible.filter(entry=>entry.member.member_id===member.member_id);if(entries.length)target.append(memberTaskGroup(member,entries));}}else{if(active.length)target.append(taskSection('진행 중인 과제',active));if(completed.length)target.append(taskSection('완료한 과제',completed,{completed:true}));}
  if(!state.loading&&!target.childElementCount)target.append(element('p','선택한 범위에 표시할 과제가 없습니다.','completionTasksEmpty'));renderDetail(findEntry(state.selectedTaskKey));
  const loaded=allRows().filter(row=>!row.loading).length,total=state.group?.members?.length||0,complete=allEntries().filter(entry=>entry.readiness==='ready'&&entry.task.status==='COMPLETED').length,ready=allEntries().filter(entry=>entry.readiness==='ready').length;$('completionLoadSummary').textContent=state.loading?'구성원 '+loaded+' / '+total+'명의 완료 과제를 불러오는 중입니다.':'그룹 전체 '+complete+' / '+ready+'개 완료 · 구성원 '+loaded+'명 조회';renderProgress();
}
function render(){renderMembers();renderContent();$('completionPageLoading').hidden=!state.loading;}
function selectMember(memberId){state.selectedMemberId=memberId;state.filter='all';for(const button of document.querySelectorAll('[data-task-filter]'))button.setAttribute('aria-pressed',String(button.dataset.taskFilter==='all'));if(memberId)params.set('owner',memberId);else params.delete('owner');history.replaceState(null,'',location.pathname+'?'+params.toString());render();}
function selectFilter(filter){state.filter=filter;if(filter==='mine'){state.selectedMemberId='';params.delete('owner');}for(const button of document.querySelectorAll('[data-task-filter]'))button.setAttribute('aria-pressed',String(button.dataset.taskFilter===filter));history.replaceState(null,'',location.pathname+'?'+params.toString());render();}
async function fetchMember(member,parentEpoch,token){const results=await Promise.allSettled([completion.loadMemberTasks({groupId,memberId:member.member_id,onTrace:trace}),completion.loadMemberPlan({groupId,memberId:member.member_id,onTrace:trace})]);if(state.epoch!==parentEpoch||state.memberEpoch.get(member.member_id)!==token)return null;return buildMemberResult(member,results[0],results[1]);}
async function refreshAllMembers({reason='manual'}={}){
  if(!completion||!state.group||state.loading||state.syncing||document.hidden)return;const syncToken=++state.syncEpoch,parentEpoch=state.epoch,members=[...(state.group.members||[])];state.syncing=true;refreshButton.disabled=true;syncStatus.textContent=reason==='manual'?'새 DB 상태를 확인하는 중…':'다른 화면의 변경을 확인하는 중…';
  try{const results=await Promise.all(members.map(async member=>{const token=(state.memberEpoch.get(member.member_id)||0)+1;state.memberEpoch.set(member.member_id,token);return [member.member_id,await fetchMember(member,parentEpoch,token)];}));if(state.epoch!==parentEpoch||state.syncEpoch!==syncToken)return;for(const [memberId,result] of results)if(result)state.members.set(memberId,result);render();syncStatus.textContent='방금 DB 상태와 동기화했습니다.';}
  finally{if(state.syncEpoch===syncToken){state.syncing=false;refreshButton.disabled=false;}}
}
async function retryMember(memberId){
  if(!completion){state.groupIssue=issue('CONTROLLER_NOT_READY','공통 완료 컨트롤러를 불러오지 못했습니다.');render();return;}if(!memberId){await loadAll();return;}const member=state.group?.members?.find(item=>item.member_id===memberId);if(!member)return;
  const parentEpoch=state.epoch,token=(state.memberEpoch.get(memberId)||0)+1,current=state.members.get(memberId);state.memberEpoch.set(memberId,token);state.members.set(memberId,current?{...current,loading:true}:{member,ready:[],unready:[],issues:[],loading:true});render();const result=await fetchMember(member,parentEpoch,token);if(result){state.members.set(memberId,result);render();}
}
async function loadAll(){
  const requestEpoch=++state.epoch;++state.syncEpoch;state.loading=true;state.syncing=false;refreshButton.disabled=true;syncStatus.textContent='DB 상태를 복원하는 중…';state.groupIssue=null;state.group=null;state.members.clear();render();if(!completion){state.loading=false;refreshButton.disabled=false;state.groupIssue=issue('CONTROLLER_NOT_READY','공통 완료 컨트롤러를 불러오지 못했습니다.');render();return;}
  let group;try{group=await completion.loadGroup({groupId,onTrace:trace});}catch(error){if(state.epoch!==requestEpoch)return;state.loading=false;refreshButton.disabled=false;syncStatus.textContent='DB 상태를 복원하지 못했습니다.';state.groupIssue=errorIssue(error);render();return;}if(state.epoch!==requestEpoch)return;
  state.group=group;$('completionGroupHeading').textContent=group.title||'현재 그룹';$('completionOverviewHeading').textContent=(group.title||'그룹')+' 완료 현황';const members=group.members||[];if(!state.viewerMemberId||!members.some(member=>member.member_id===state.viewerMemberId)){state.viewerMemberId=members[0]?.member_id||'';if(state.viewerMemberId)params.set('member',state.viewerMemberId);}if(state.selectedMemberId&&!members.some(member=>member.member_id===state.selectedMemberId))state.selectedMemberId='';if(!members.length)state.groupIssue=issue('NO_TASKS','그룹에 조회할 구성원이 없습니다.');history.replaceState(null,'',location.pathname+'?'+params.toString());
  for(const member of members){const token=(state.memberEpoch.get(member.member_id)||0)+1;state.memberEpoch.set(member.member_id,token);state.members.set(member.member_id,{member,ready:[],unready:[],issues:[],loading:true});}render();
  await Promise.all(members.map(async member=>{const token=state.memberEpoch.get(member.member_id),result=await fetchMember(member,requestEpoch,token);if(result){state.members.set(member.member_id,result);render();}}));if(state.epoch!==requestEpoch)return;state.loading=false;refreshButton.disabled=false;syncStatus.textContent='DB 상태를 복원했습니다.';render();
}

$('completionMemberList').addEventListener('click',event=>{const button=event.target.closest('[data-member]');if(button)selectMember(button.dataset.member);});
document.querySelector('.completionFilterList').addEventListener('click',event=>{const button=event.target.closest('[data-task-filter]');if(button)selectFilter(button.dataset.taskFilter);});
$('completionPageIssues').addEventListener('click',event=>{const button=event.target.closest('[data-retry-member]');if(button)retryMember(button.dataset.retryMember);});
$('completionTaskList').addEventListener('click',event=>{const button=event.target.closest('[data-task-key]');if(button)selectTask(button.dataset.taskKey);});
refreshButton.addEventListener('click',()=>refreshAllMembers({reason:'manual'}));
document.addEventListener('visibilitychange',()=>{if(document.hidden){syncStatus.textContent='숨겨진 탭에서는 자동 갱신을 멈춥니다.';return;}syncStatus.textContent='화면이 다시 보여 최신 상태를 확인합니다.';refreshAllMembers({reason:'visible'});});
setInterval(()=>{if(!document.hidden)refreshAllMembers({reason:'auto'});},AUTO_SYNC_MS);
window.kickoffCompletionPage={groupId,completion,reload:loadAll,refresh:refreshAllMembers,retryMember,selectMember,selectFilter,selectTask,state};loadAll();
})();
