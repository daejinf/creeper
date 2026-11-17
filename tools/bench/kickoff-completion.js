/* Shared M2 completion controller plus the existing personal-task-card adapter. */
(()=>{
const statusText={TODO:'제출 전',SUBMITTED:'제출 저장됨',REVIEWING:'AI 검토 중',REVIEW_NEEDED:'추가 확인 필요',NEEDS_INPUT:'자료 보완 필요',APPEALED:'동료 투표 중',COMPLETED:'완료',REVIEW_FAILED:'AI 검토 실패'};
const decisionText={PASS:'완료',REVIEW_NEEDED:'추가 확인 필요',NEEDS_INPUT:'자료 보완 필요'};
const criterionMark={MET:'✓',UNKNOWN:'?',NOT_MET:'✕'};
const criterionText={MET:'충족',UNKNOWN:'확인 자료 부족',NOT_MET:'미충족'};

function randomId(){return crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random().toString(16).slice(2);}
function actionKey(groupId,scope){return 'kickoff:'+groupId+':completion-action:'+scope;}
function pendingAction(groupId,scope,signature){
  const key=actionKey(groupId,scope);let pending;
  try{pending=JSON.parse(localStorage.getItem(key)||'null');}catch{pending=null;}
  if(!pending||pending.signature!==signature){pending={actionId:randomId(),signature};localStorage.setItem(key,JSON.stringify(pending));}
  return {...pending,key};
}
function finishAction(pending){if(pending)localStorage.removeItem(pending.key);}
function groupPath(groupId,suffix=''){return '/api/kickoff/groups/'+encodeURIComponent(groupId)+(suffix||'');}
function memberPath(groupId,memberId,suffix=''){return groupPath(groupId,'/members/'+encodeURIComponent(memberId)+suffix);}
function requireText(value,message){const text=String(value||'').trim();if(!text)throw Error(message);return text;}
function fileInput(files){return [...files].map(file=>({name:file.name,size:file.size,type:file.type,lastModified:file.lastModified}));}
function validateFiles(files){
  if(files.length>3)throw Error('파일은 최대 3개까지 제출할 수 있습니다.');let total=0;
  const allowed=new Set(['image/png','image/jpeg','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document']),extensions=/\.(png|jpe?g|pdf|docx)$/i;
  for(const file of files){if(!allowed.has(file.type)&&!extensions.test(file.name))throw Error(file.name+' 형식은 지원하지 않습니다.');if(file.size>10*1024*1024)throw Error(file.name+' 파일이 10MB를 넘습니다.');total+=file.size;}
  if(total>20*1024*1024)throw Error('전체 파일 크기가 20MB를 넘습니다.');
}
async function request(path,{method='GET',body,traceBody,onTrace}={}){
  const options={method};if(body instanceof FormData)options.body=body;else if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
  const response=await fetch(path,options);let value;try{value=await response.json();}catch{value={status:'FAILED',error:{message:'서버 응답을 읽지 못했습니다.'}};}
  onTrace?.({method,path,body:traceBody??body??null},value);
  if(!response.ok){const error=new Error(value.error?.message||'요청을 완료하지 못했습니다. 입력은 유지됩니다.');error.code=value.error?.code;error.status=response.status;throw error;}
  return value;
}
async function idempotentRequest({groupId,scope,signature,path,body,traceBody,onTrace}){
  const pending=pendingAction(groupId,scope,signature),payload=body instanceof FormData?body:{...body,action_id:pending.actionId};
  if(body instanceof FormData)payload.set('action_id',pending.actionId);
  const visibleTrace=traceBody?{...traceBody,action_id:pending.actionId}:payload;
  try{const value=await request(path,{method:'POST',body:payload,traceBody:visibleTrace,onTrace});finishAction(pending);return value;}
  catch(error){if(error.status)finishAction(pending);throw error;}
}
function normalizedTasks(value){return {...value,tasks:(value.tasks||[]).map(task=>({...task,status:task.status||'TODO',revision:Number(task.revision||0)}))};}

const controller={
  statusLabel(status){return statusText[status]||status;},
  validateEvidenceFiles(files){files=[...files];validateFiles(files);return fileInput(files);},
  async loadGroup({groupId,onTrace}){
    groupId=requireText(groupId,'groupId is required');
    return request(groupPath(groupId),{onTrace});
  },
  async loadMemberPlan({groupId,memberId,onTrace}){
    requireText(groupId,'groupId is required');requireText(memberId,'memberId is required');
    return request(memberPath(groupId,memberId,'/personal'),{onTrace});
  },
  async loadMemberTasks({groupId,memberId,onTrace}){
    requireText(groupId,'groupId is required');requireText(memberId,'memberId is required');
    return normalizedTasks(await request(memberPath(groupId,memberId,'/completion/tasks'),{onTrace}));
  },
  async submitEvidence({groupId,memberId,planId,taskId,description,files=[],onTrace}){
    description=requireText(description,'제출 설명을 입력해주세요.');files=[...files];validateFiles(files);
    const inputs=fileInput(files),signature=JSON.stringify({description,files:inputs}),form=new FormData();form.set('description',description);for(const file of files)form.append('files[]',file);
    return idempotentRequest({groupId,scope:'submission:'+planId+':'+taskId,signature,path:memberPath(groupId,memberId,'/plans/'+encodeURIComponent(planId)+'/tasks/'+encodeURIComponent(taskId)+'/submissions'),body:form,traceBody:{description,files:inputs},onTrace});
  },
  async runReview({groupId,memberId,submissionId,onTrace}){
    submissionId=requireText(submissionId,'submissionId is required');
    return idempotentRequest({groupId,scope:'review:'+submissionId,signature:submissionId,path:memberPath(groupId,memberId,'/submissions/'+encodeURIComponent(submissionId)+'/review'),body:{},onTrace});
  },
  async submitAppeal({groupId,memberId,reviewId,reason,onTrace}){
    reason=requireText(reason,'소명 내용을 입력해주세요.');
    return idempotentRequest({groupId,scope:'appeal:'+reviewId,signature:reason,path:memberPath(groupId,memberId,'/reviews/'+encodeURIComponent(reviewId)+'/appeals'),body:{reason},onTrace});
  },
  async castVote({groupId,appealId,voterMemberId,approve,onTrace}){
    const signature=JSON.stringify({voter:voterMemberId,approve:Boolean(approve)});
    return idempotentRequest({groupId,scope:'vote:'+appealId+':'+voterMemberId,signature,path:groupPath(groupId,'/appeals/'+encodeURIComponent(appealId)+'/votes'),body:{voter_member_id:voterMemberId,approve:Boolean(approve)},onTrace});
  },
};
window.kickoffCompletion=controller;

/* The dedicated page loads the controller without the legacy card DOM. */
if(!document.getElementById('completionSubmissionForm'))return;

const byId=id=>document.getElementById(id);
let context=null,taskState=new Map(),loading=false,busy=false,epoch=0,activeTask=null;
const dialogOpeners=new Map();
function make(tag,text,className){const value=node(tag,text);if(className)value.className=className;return value;}
function button(text,handler,className=''){const value=make('button',text,className);value.type='button';value.onclick=handler;return value;}
function taskKey(planId,taskId){return planId+':'+taskId;}
function currentVoter(){return byId('speaker').value;}
function memberName(memberId){return group?.members.find(item=>item.member_id===memberId)?.nickname||memberId||'구성원';}
function setTrace(requestValue,responseValue){
  if(document.querySelector('[data-assistant=plans]')?.getAttribute('aria-pressed')!=='true')return;
  byId('requestJson').textContent=JSON.stringify(requestValue,null,2);byId('responseJson').textContent=JSON.stringify(responseValue,null,2);byId('modulePythonTitle').textContent='과제 완료 판정 API 호출';byId('modulePython').textContent='제출 저장 → AI 판정 → 소명 → 동료 투표\n각 단계의 규칙과 상태 저장은 completion API에서 처리합니다.';
}
function setBusy(value){busy=value;renderCards();byId('submitCompletionEvidence').disabled=value;byId('submitCompletionAppeal').disabled=value;}
function completionPanel(card){let panel=card.querySelector('.completionPanel');if(!panel){panel=make('section',undefined,'completionPanel');panel.setAttribute('aria-live','polite');card.append(panel);}return panel;}
function taskPageLink(task){const query=new URLSearchParams({group:group.group_id,member:context.memberId,owner:context.memberId,task:task.task_id}),link=make('a','전용 페이지에서 보기','completionTaskPageLink');link.href='/kickoff/completion?'+query.toString();link.setAttribute('aria-label',task.title+' 과제를 전용 완료 판정 페이지에서 보기');return link;}
function stateFor(task){return taskState.get(taskKey(context.plan.plan_version_id,task.task_id));}
function renderCriteria(review){
  const list=make('ul',undefined,'completionCriteriaResults');
  for(const item of review.criteria_results||[]){const row=make('li');row.dataset.status=item.status;row.append(make('strong',(criterionMark[item.status]||'•')+' 기준 '+(Number(item.criterion_index)+1)+': '+(criterionText[item.status]||item.status)),document.createTextNode(' · '+item.criterion));if(item.reason)row.append(make('small',item.reason));list.append(row);}return list;
}
function reviewRecord(review){
  const box=make('div',undefined,'completionReviewRecord');box.hidden=true;box.append(make('h4','AI 1차 검토: '+(decisionText[review.decision]||review.decision)),make('p',review.summary||''),renderCriteria(review));
  if(review.missing_items?.length){box.append(make('strong','부족한 자료'));const missing=make('ul');for(const item of review.missing_items)missing.append(make('li',item));box.append(missing);}if(review.user_message)box.append(make('p',review.user_message,'completionUserMessage'));return box;
}
function appealBox(state){
  const appeal=state.latest_appeal,box=make('div',undefined,'completionAppealBox');box.append(make('strong',appeal.status==='APPROVED'?'동료 확인 완료':'동료 투표'),make('p','찬성 '+appeal.approve_votes+' / 필요 '+appeal.required_votes+' · 투표 가능 '+appeal.eligible_voters+'명'));if(appeal.reason)box.append(make('p','소명: '+appeal.reason));if(appeal.status!=='OPEN')return box;
  const voter=currentVoter(),self=voter===context.memberId,note=make('small',self?'제출자는 자신의 소명에 투표할 수 없습니다.':memberName(voter)+'님의 표로 기록됩니다.');const actions=make('div',undefined,'completionActions'),approve=button('완료 인정에 찬성',()=>voteFromCard(state,true),'primary'),reject=button('인정하지 않음',()=>voteFromCard(state,false));approve.disabled=reject.disabled=busy||self;actions.append(approve,reject);box.append(note,actions);return box;
}
function renderTask(entry){
  const {card,task}=entry,panel=completionPanel(card),state=stateFor(task),pageLink=taskPageLink(task);panel.replaceChildren();if(loading&&!state){panel.append(make('p','완료 상태 불러오는 중…','completionLoading'),pageLink);return;}if(!state){panel.append(make('p','TASK_STATE_MISSING · 현재 계획과 완료 기록을 연결하지 못했습니다. 개인 계획을 다시 확인해주세요.','completionError'),pageLink);return;}
  const status=make('div',undefined,'completionStatus');status.append(make('span',controller.statusLabel(state.status),'completionBadge status-'+String(state.status).toLowerCase()),make('small','기록 버전 '+state.revision));panel.append(status);if(state.status==='REVIEW_FAILED')panel.append(make('p','AI 검토를 마치지 못했습니다. 저장된 제출로 다시 시도할 수 있습니다.','completionError'));
  const actions=make('div',undefined,'completionActions'),submitLabel=['REVIEW_NEEDED','NEEDS_INPUT','APPEALED','REVIEW_FAILED'].includes(state.status)?'새 자료 제출':'결과 제출',submit=button(submitLabel,()=>openSubmission(task,state),'primary');submit.dataset.dialogOpener='completionSubmissionDialog';submit.disabled=busy||state.status==='COMPLETED'||state.status==='REVIEWING';actions.append(submit);
  if(state.latest_submission&&['SUBMITTED','REVIEW_FAILED'].includes(state.status)){const review=button(state.status==='REVIEW_FAILED'?'AI 검토 다시 시도':'AI 완료 여부 검토',()=>reviewFromCard(state));review.disabled=busy;actions.append(review);}
  const record=state.latest_review?reviewRecord(state.latest_review):null,history=button('판정 기록 보기',()=>{if(record){record.hidden=!record.hidden;history.setAttribute('aria-expanded',String(!record.hidden));}});history.disabled=!record;history.setAttribute('aria-expanded','false');actions.append(history,pageLink);panel.append(actions);
  if(record){panel.append(record);if(['REVIEW_NEEDED','NEEDS_INPUT'].includes(state.status)){const appeal=button('소명하기',()=>openAppeal(task,state));appeal.dataset.dialogOpener='completionAppealDialog';appeal.disabled=busy;panel.append(appeal);}}if(state.latest_appeal)panel.append(appealBox(state));
}
function renderCards(){if(context)for(const entry of context.taskCards)renderTask(entry);}
async function refresh(){
  if(!context||!group)return;const selected=context.memberId,gid=group.group_id,version=++epoch;loading=true;renderCards();
  try{const value=await controller.loadMemberTasks({groupId:gid,memberId:selected,onTrace:setTrace});if(version!==epoch||!group||group.group_id!==gid||context.memberId!==selected)return;taskState=new Map(value.tasks.map(item=>[taskKey(item.plan_version_id,item.task_id),item]));}
  catch(error){if(version===epoch)notify((error.code?error.code+' · ':'')+error.message,true);}finally{if(version===epoch){loading=false;renderCards();}}
}
function submissionDraftKey(task){return storageKey('completion-submission:'+task.task_id);}
function fileSummary(file){return file.name+' · '+(file.size/1024/1024).toFixed(2)+'MB · '+(file.type||'형식 미상');}
function renderFiles(){byId('completionFileList').replaceChildren(...[...byId('completionFiles').files].map(file=>make('li',fileSummary(file))));}
function openSubmission(task,state){
  activeTask={task,state};byId('completionDescription').value=localStorage.getItem(submissionDraftKey(task))||'';byId('completionFiles').value='';renderFiles();const wrap=byId('completionSubmissionContext');wrap.replaceChildren(make('h3',task.title),make('p','결과물: '+task.deliverable),make('strong','완료 기준'));const list=make('ul');for(const item of task.completion_criteria||[])list.append(make('li',item));wrap.append(list);byId('completionSubmissionStatus').textContent=state.latest_submission?'새 제출을 저장하면 현재 판정·소명보다 최신 증빙이 됩니다.':'설명만 제출하거나 파일을 함께 제출할 수 있습니다.';openDialog('completionSubmissionDialog','completionDescription');
}
async function submitFromCard(event){
  event.preventDefault();if(busy||!activeTask)return;const {task}=activeTask,description=byId('completionDescription').value,files=[...byId('completionFiles').files];setBusy(true);byId('completionSubmissionStatus').textContent='제출 파일을 안전하게 확인하고 저장하는 중…';
  try{await controller.submitEvidence({groupId:group.group_id,memberId:context.memberId,planId:context.plan.plan_version_id,taskId:task.task_id,description,files,onTrace:setTrace});localStorage.removeItem(submissionDraftKey(task));byId('completionSubmissionDialog').close();notify('과제 제출을 저장했습니다. AI 검토는 별도 버튼으로 실행합니다.');}
  catch(error){byId('completionSubmissionStatus').textContent=(error.code?error.code+' · ':'')+error.message+' 입력과 파일 선택은 유지됩니다.';notify(error.message,true);}finally{await refresh();setBusy(false);}
}
async function reviewFromCard(state){
  if(busy||!state.latest_submission)return;setBusy(true);try{await controller.runReview({groupId:group.group_id,memberId:context.memberId,submissionId:state.latest_submission.submission_id,onTrace:setTrace});notify('AI 완료 판정을 저장했습니다.');}catch(error){notify((error.code?error.code+' · ':'')+error.message,true);}finally{await refresh();setBusy(false);}
}
function appealDraftKey(state){return storageKey('completion-appeal:'+state.latest_review.review_id);}
function openAppeal(task,state){
  activeTask={task,state};byId('completionAppealReason').value=localStorage.getItem(appealDraftKey(state))||'';const summary=byId('completionAppealReview');summary.replaceChildren(make('p',state.latest_review.summary||''),renderCriteria(state.latest_review));const eligible=Math.max(0,(group?.members.length||1)-1),required=Math.floor(eligible/2)+1;byId('completionAppealVotes').textContent='현재 찬성 0명 · 필요한 찬성 '+required+'명';byId('completionAppealStatus').textContent='소명 제출 후 다른 구성원이 투표할 수 있습니다.';openDialog('completionAppealDialog','completionAppealReason');
}
async function appealFromCard(event){
  event.preventDefault();if(busy||!activeTask)return;const {state}=activeTask,reason=byId('completionAppealReason').value;setBusy(true);byId('completionAppealStatus').textContent='소명을 저장하는 중…';
  try{await controller.submitAppeal({groupId:group.group_id,memberId:context.memberId,reviewId:state.latest_review.review_id,reason,onTrace:setTrace});localStorage.removeItem(appealDraftKey(state));byId('completionAppealDialog').close();notify('소명을 저장했습니다. 동료 투표를 기다립니다.');}
  catch(error){byId('completionAppealStatus').textContent=(error.code?error.code+' · ':'')+error.message+' 입력은 유지됩니다.';notify(error.message,true);}finally{await refresh();setBusy(false);}
}
async function voteFromCard(state,approve){
  if(busy||!state.latest_appeal)return;const voter=currentVoter();setBusy(true);try{await controller.castVote({groupId:group.group_id,appealId:state.latest_appeal.appeal_id,voterMemberId:voter,approve,onTrace:setTrace});notify(memberName(voter)+'님의 투표를 저장했습니다.');}catch(error){notify((error.code?error.code+' · ':'')+error.message,true);}finally{await refresh();setBusy(false);}
}
function openDialog(id,focusId){const dialog=byId(id);dialogOpeners.set(id,document.activeElement);dialog.showModal();byId(focusId)?.focus();}
function focusDialogOpener(id,opener){const target=opener?.isConnected?opener:document.querySelector('[data-dialog-opener="'+id+'"]');if(target&&!target.disabled)target.focus();}
function closeDialog(id){if(!busy){const opener=dialogOpeners.get(id);byId(id).close();focusDialogOpener(id,opener);}}
byId('completionDescription').oninput=()=>{if(activeTask)localStorage.setItem(submissionDraftKey(activeTask.task),byId('completionDescription').value);};byId('completionFiles').onchange=renderFiles;byId('completionSubmissionForm').onsubmit=submitFromCard;byId('closeCompletionSubmission').onclick=byId('cancelCompletionSubmission').onclick=()=>closeDialog('completionSubmissionDialog');
byId('completionAppealReason').oninput=()=>{if(activeTask?.state?.latest_review)localStorage.setItem(appealDraftKey(activeTask.state),byId('completionAppealReason').value);};byId('completionAppealForm').onsubmit=appealFromCard;byId('closeCompletionAppeal').onclick=byId('cancelCompletionAppeal').onclick=()=>closeDialog('completionAppealDialog');
for(const id of ['completionSubmissionDialog','completionAppealDialog']){const dialog=byId(id);dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});dialog.addEventListener('close',()=>{const opener=dialogOpeners.get(id);dialogOpeners.delete(id);setTimeout(()=>focusDialogOpener(id,opener),0);});}byId('speaker').addEventListener('change',renderCards);
window.addEventListener('kickoff:personal-plan-rendered',event=>{context=event.detail;taskState=new Map();refresh();});
})();
