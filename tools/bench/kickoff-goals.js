/* Read explicit persisted entities, not inferred chat bubbles. No automatic AI. */
(()=>{
let snapshot=null,loadId=0;
const dialog=$('goalDatabaseDialog');
const card=(title)=>{const c=node('article');c.className='storedGoal';c.append(node('h4',title));return c;};
const empty=(target,text)=>{const p=node('p',text);p.className='storedEmpty';target.replaceChildren(p);};
function refs(c,values){const d=node('details');d.append(node('summary','DB 참조 ID 보기'),node('pre',JSON.stringify(values,null,2)));c.append(d);}
function list(c,values){const ul=node('ul');for(const value of values||[])ul.append(node('li',value));c.append(ul);}
function plan(c,p,owner=null){
 c.append(node('p',p.start_date+' ~ '+p.end_date+' · 과제 '+p.tasks.length+'개'));
 const current=snapshot?.heads?.current_plans?.[owner||'GROUP']===p.plan_version_id;
 c.append(node('small',current?'현재 검토 조건의 계획':'보관된 계획 · 현재 조건은 재검토가 필요할 수 있습니다.'));
 const tasks=node('details');tasks.append(node('summary','실행할 과제 '+p.tasks.length+'개 보기'));
 for(const t of p.tasks){const item=node('div');item.className='storedGoal';item.append(node('h4',t.title),node('p',t.deliverable),node('p',t.due_date+(t.estimated_minutes?' · 예상 '+t.estimated_minutes+'분':'')));list(item,t.completion_criteria);refs(item,{plan_version_id:p.plan_version_id,task_id:t.task_id});tasks.append(item);}c.append(tasks);
 refs(c,{shared_goal_version_id:p.shared_goal_version_id,member_goal_version_id:p.member_goal_version_id,plan_version_id:p.plan_version_id,previous_plan_version_id:p.previous_plan_version_id,baseline_id:p.baseline?.baseline_id||null});
 if(p.baseline)c.append(node('small','평가 기준 확인됨 · '+new Date(p.baseline.created_at).toLocaleString('ko-KR')));
 else{const b=node('button','이 계획을 평가 기준으로 확인');b.type='button';b.disabled=!current;b.onclick=async()=>{b.disabled=true;try{await api(endpoint('/goal-database/plans/'+encodeURIComponent(p.plan_version_id)+'/baseline'),{actor_member_id:owner||$('speaker').value});await load();}catch(e){notify(e.message,true);}finally{b.disabled=!current;}};c.append(b);}
}
async function loadPrivate(){
 const owner=$('databaseOwner').value,id=++loadId;
 empty($('storedMemberGoals'),'선택한 사람의 목표를 불러오는 중…');$('storedMemberPlans').replaceChildren();
 try{const value=await api(endpoint('/members/'+encodeURIComponent(owner)+'/goal-database'));if(id!==loadId||!dialog.open)return;
  $('storedMemberGoals').replaceChildren();$('storedMemberPlans').replaceChildren();
  if(!value.member_goals.length)empty($('storedMemberGoals'),'아직 확인·저장된 개인 기여 목표가 없습니다. 내 계획 상담에서 기여 목표와 조건을 확인해주세요.');
  for(const goal of [...value.member_goals].reverse()){const c=card(goal.title);c.append(node('p',goal.deliverable));list(c,goal.completion_criteria);const own=node('details');own.append(node('summary','본인만 보는 개인 조건'),node('p','현재 수준: '+goal.profile.current_level),node('p','주간 가능 시간: '+goal.profile.weekly_available_minutes+'분'));c.append(own);refs(c,{shared_goal_version_id:goal.shared_goal_version_id,member_goal_version_id:goal.member_goal_version_id});$('storedMemberGoals').append(c);}
  for(const p of [...value.plans].reverse()){const c=card('개인 실행 계획');plan(c,p,owner);$('storedMemberPlans').append(c);}
  if(!value.plans.length)empty($('storedMemberPlans'),'아직 저장된 개인 실행 계획이 없습니다.');
 }catch(e){if(id===loadId)empty($('storedMemberGoals'),e.message);}
}
async function load(){
 const gid=group.group_id;$('goalDatabaseStatus').textContent='PostgreSQL 저장 결과를 확인하고 있습니다…';
 try{const value=await api(endpoint('/goal-database'));if(!dialog.open||group.group_id!==gid)return;snapshot=value;
  $('goalDatabaseStatus').className=value.storage_status?.status==='SYNC_FAILED'?'error':'';$('goalDatabaseStatus').textContent=(value.storage_status?.status==='SYNC_FAILED'?'최신 동기화 실패 · 아래는 기존 저장 자료입니다. ':'')+'PostgreSQL · 공동 목표 '+value.shared_goals.length+'개 버전 · 공개 기여 목표 '+value.member_goals.length+'개 · 공동 계획 '+value.group_plans.length+'개 버전';
  $('storedSharedGoals').replaceChildren();$('storedGroupPlans').replaceChildren();
  for(const g of [...value.shared_goals].reverse()){const c=card(g.title);c.append(node('p',g.start_date+' ~ '+g.end_date));list(c,g.completion_criteria);c.append(node('p','인증: '+g.verification_method),node('small',value.heads?.current_shared_goal_version_id===g.goal_version_id?'현재 확인된 공동 목표':'보관된 공동 목표 · 대화가 바뀌었다면 재검토가 필요합니다.'));refs(c,{shared_goal_version_id:g.goal_version_id});$('storedSharedGoals').append(c);}
  if(!value.shared_goals.length)empty($('storedSharedGoals'),'아직 확인된 공동 목표가 없습니다. 목표 정리에서 확인한 뒤 저장됩니다.');
  for(const p of [...value.group_plans].reverse()){const c=card('공동 실행 계획');plan(c,p);$('storedGroupPlans').append(c);}
  if(!value.group_plans.length)empty($('storedGroupPlans'),'아직 저장된 공동 실행 계획이 없습니다.');
  await loadPrivate();
 }catch(e){$('goalDatabaseStatus').className='error';$('goalDatabaseStatus').textContent='정식 목표 DB를 읽지 못했습니다. 대화 기록은 보존됩니다. DB 연결·마이그레이션을 확인해주세요.';}
}
$('openGoalDatabase').onclick=()=>{const options=group.members.map(m=>{const o=node('option',m.nickname);o.value=m.member_id;return o;});$('databaseOwner').replaceChildren(...options);$('databaseOwner').value=$('speaker').value;dialog.showModal();load();};
$('closeGoalDatabase').onclick=()=>{loadId++;dialog.close();};dialog.addEventListener('close',()=>{loadId++;$('storedMemberGoals').replaceChildren();$('storedMemberPlans').replaceChildren();});
$('databaseOwner').onchange=loadPrivate;
$('syncGoalDatabase').onclick=async()=>{const b=$('syncGoalDatabase');b.disabled=true;try{await api(endpoint('/goal-database/sync'),{});await load();notify('목표 DB를 동기화했습니다. AI 호출 없음');}catch(e){notify('정식 DB 동기화가 완료되지 않았습니다. 기존 대화와 결과는 유지됩니다.',true);}finally{b.disabled=false;}};
window.addEventListener('kickoff:group',event=>{const s=event.detail.goal_database;$('openGoalDatabase').textContent=s?.status==='CONNECTED'?'저장된 목표·과제':'목표 DB 확인';});
})();
