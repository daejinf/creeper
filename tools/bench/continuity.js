/* SQLite is a local adapter, never a requirement of the installed AI modules. */
class RecordController {
 constructor(){this.view=null;this.owner='';this.ready=false;this.queue=Promise.resolve();this.timer=null;this.dirty=false;this.actions={};this.previousSuccess={};this.dependencies={};this.unsavedResult={};this.unsavedBuckets={};this.inFlight=0;this.resultStates={};}
 assertIdle(){if(busy||this.inFlight)throw Error('현재 실행을 저장한 뒤 기록이나 대상을 바꿔주세요.');}
 badge(text,error=false){$('recordSave').textContent=text;$('recordSave').className=error?'error':'badge';}
 noteRevision(value){if(this.view&&value!==null)this.view.revision=Math.max(this.view.revision,Number(value));}
 async raw(path,body){let response;try{response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}catch(e){$('recordOwner').disabled=$('recordList').disabled=false;this.badge('서버 연결 실패 · 저장 완료가 아닙니다.',true);throw Error('기록 서버에 연결하지 못했습니다. 연결을 확인하고 다시 열어주세요.');}const value=await response.json();if(!response.ok){$('recordOwner').disabled=$('recordList').disabled=false;this.badge(value.error?.message||'저장 실패 · 입력 유지',true);throw Error(value.error?.message||'기록 작업 실패');}return value;}
 async init(){
  try{const rows=await this.raw('/api/records');this.renderList(rows);const saved=localStorage.getItem('wemeet-record-id');
   if(saved&&rows.some(r=>r.id===saved))await this.open(saved);else if(rows.length)await this.open(rows[0].id);else await this.create();
  }catch(e){this.badge('기록 복원 실패 · 저장된 것으로 안내하지 않습니다.',true);showStatus(e.message,true);}
 }
 renderList(rows){$('recordList').replaceChildren(...rows.map(r=>Object.assign(el('option',r.title+' · '+new Date(r.updated_at).toLocaleString()),{value:r.id})));if(this.view)$('recordList').value=this.view.record_id;}
 async create(title='새 목표 대화',input=null,target='A'){
  this.assertIdle();
  if(this.ready)await this.flush();const view=await this.raw('/api/records',{title,members:input?.members||(input?.owner_member_id?[{member_id:input.owner_member_id,nickname:'나'}]:undefined),reference_date:input?.reference_date,execution_mode:$('mode').value});
  this.owner=input?.owner_member_id||'';this.ready=false;module=target;
  await this.open(view.record_id,target,false);
  if(input){drafts[target]=structuredClone(input);outputs[target]=null;pending[target]='';await this.flush();render();}
  this.renderList(await this.raw('/api/records'));showStatus('새 기록입니다. 이전 기록은 보존했습니다. · AI 호출 없음');
 }
 conceal(){
  $('form').hidden=true;$('result').hidden=true;$('raw').value='';$('response').textContent='기록 범위 전환 중 · AI 호출 없음';
  $('recordContext').textContent='선택한 범위의 기록을 불러오는 중입니다.';
  $('recordOwner').disabled=$('recordList').disabled=true;
 }
 async open(id,target=null,save=true){
  this.assertIdle();this.conceal();
  if(save&&this.ready)await this.flush();this.ready=false;
  let view=await this.raw('/api/records/'+id);const selected=target||view.selected_module||'A';
  if(!this.view||this.view.record_id!==id)this.owner=view.selected_owner_member_id||'';
  if(this.owner&&!view.members.some(m=>m.member_id===this.owner))this.owner='';
  if(selected.startsWith('E.')&&!this.owner)this.owner=view.members[0]?.member_id||'u1';
  if(selected.startsWith('E.')||selected.startsWith('F'))view=await this.raw('/api/records/'+id+'?owner_member_id='+encodeURIComponent(this.owner||view.members[0]?.member_id||'u1')+'&internal='+(selected.startsWith('F')?'true':'false'));
  this.view=view;this.unsavedResult=this.unsavedBuckets[id+'|'+(view.owner_member_id||'PUBLIC')]??={};module=selected;this.hydrate(view);this.ready=true;localStorage.setItem('wemeet-record-id',id);if(![...$('recordList').options].some(o=>o.value===id))$('recordList').append(Object.assign(el('option',view.title),{value:id}));$('recordList').value=id;this.renderOwners();render();$('form').hidden=$('result').hidden=false;$('recordOwner').disabled=$('recordList').disabled=false;this.dirty=false;this.badge('저장된 기록 복원 · AI 호출 없음');
  $('recordContext').textContent='공개 대화 '+view.conversation.length+'개 · '+(view.owner_member_id?'본인 대화 '+view.private_conversation.length+'개 · ':'')+'저장된 기록입니다. 필요한 모듈에서 입력 준비를 누르세요.';
 }
 hydrate(view){
  for(const box of [drafts,outputs,pending,lastUserKeys])for(const key of Object.keys(box))delete box[key];this.previousSuccess={};this.resultStates={};
  Object.assign(drafts,view.inputs);Object.assign(pending,view.pending);this.dependencies=structuredClone(view.input_dependencies||{});$('mode').value=view.execution_mode;
  for(const a of view.artifacts){if(a.scope==='PRIVATE'&&a.owner_member_id!==view.owner_member_id)continue;
   const key=a.module==='C.review'?'C':a.module==='E.review'?'E.coach':a.module;
   if(!info[key])continue;
   if(a.module==='E.review'){
    const old=outputs['E.coach'];if(old?.data){old.data.state=a.response.data;drafts['E.coach']??=empty('E.coach');drafts['E.coach'].state=a.response.data;}
    continue;
   }
   outputs[key]=structuredClone(a.response);this.resultStates[key]=view.artifact_states?.[a.artifact_id]||{};
   if(a.response.status==='OK')this.previousSuccess[key]=structuredClone(a.response);
   if(a.response.data?.state){drafts[key]??=empty(key);drafts[key].state=a.response.data.state;}
  }
  for(const key of ['A','B']){drafts[key]??=empty(key);drafts[key].conversation=view.conversation;drafts[key].members=view.members;drafts[key].reference_date=view.reference_date;}
  for(const key of ['E.coach','E.draft','E.plan'])if(view.owner_member_id){drafts[key]??=empty(key);drafts[key].owner_member_id=view.owner_member_id;if(key!=='E.plan')drafts[key].conversation=view.private_conversation;}
  for(const key of ['A','E.coach'])if(outputs[key]?.status==='OK'&&drafts[key])lastUserKeys[key]=userKey(drafts[key]);
 }
 renderOwners(){const id=this.view?.members||[];$('recordOwner').replaceChildren(...id.map(m=>Object.assign(el('option',m.nickname),{value:m.member_id})));if(this.owner)$('recordOwner').value=this.owner;$('ownerWrap').hidden=!module.startsWith('E.')&&!module.startsWith('F');}
 schedule(){if(!this.ready)return;this.dirty=true;this.badge('저장 중…');clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush().catch(e=>showStatus(e.message,true)),300);}
 async saveNow(){
  if(!this.ready||!this.view)return;const target=module;const input=structuredClone(current());delete input._speaker;
  const owner=input.owner_member_id||this.owner;
  if(target.startsWith('E.')&&!input.owner_member_id)input.owner_member_id=owner||this.view.members[0]?.member_id||'u1';
  if(target.startsWith('E.')&&input.owner_member_id!==this.view.owner_member_id)throw Error('개인 대상은 위의 본인 코칭 대상 메뉴에서 변경해주세요. 입력을 다른 사람에게 저장하지 않았습니다.');
  const result=await this.raw('/api/records/'+this.view.record_id+'/input',{module:target,input,pending:pending[target]||'',expected_revision:this.view.revision,
      metadata:{selected_module:target,execution_mode:$('mode').value,selected_owner_member_id:this.owner||null},dependencies:this.dependencies[target]||{}});
  this.noteRevision(result.revision);if(['A','B'].includes(target))this.view.conversation=input.conversation||[];if(['E.coach','E.draft'].includes(target))this.view.private_conversation=input.conversation||[];this.view.inputs[target]=input;this.view.pending[target]=pending[target]||'';this.dirty=false;this.badge('자동 저장 완료');this.indicate();
 }
 flush(){clearTimeout(this.timer);this.queue=this.queue.catch(()=>{}).then(()=>this.saveNow());return this.queue;}
 async switchTo(target){
  this.assertIdle();
  await this.flush();const id=this.view.record_id;await this.open(id,target,false);
  // Prepared requests are optional; existing editable inputs are never discarded.
  await this.prepare(target,false);
 }
 async prepare(target=module,replace=true){
  const prepared=await this.raw('/api/records/'+this.view.record_id+'/prepare',{module:target,owner_member_id:target.startsWith('E.')||target.startsWith('F')?this.owner||this.view.members[0]?.member_id:null,internal:target.startsWith('F')});
  const saved=this.view.inputs[target];
  const emptyInput=!saved||target==='C'&&!Object.keys(saved.facts||{}).length||target==='D'&&!saved.goal||target.startsWith('E.')&&!saved.context?.goal||target==='F'&&!saved.group_plan&&!saved.member_plans?.length;
  if(replace||emptyInput){
   if(Object.keys(prepared.request).length){drafts[target]=prepared.request;this.dependencies[target]=prepared.dependencies;}
   if(target==='C'&&outputs.C?.data?.fields)drafts.C.previous=outputs.C.data;
   render();await this.flush();
  }
  const oldInput=!replace&&!emptyInput&&this.view.input_states?.[target]?.stale;
  $('recordContext').textContent='공개 대화 '+this.view.conversation.length+'개 · '+(this.view.owner_member_id?'본인 대화 '+this.view.private_conversation.length+'개 · ':'')+
    (prepared.stale||oldInput?'이전 기록 기준 · 입력 준비로 최신 근거를 반영하세요':prepared.ready?'입력 준비됨':'입력 필요')+(prepared.needed_inputs.length?' · '+prepared.needed_inputs.join(' / '):'');
  return prepared;
 }
 indicate(){
  const text=$('recordContext');const suffix=text.textContent.split(' · ').filter(x=>!x.startsWith('공개 대화')&&!x.startsWith('본인 대화')).join(' · ');
  text.textContent='공개 대화 '+this.view.conversation.length+'개 · '+(this.view.owner_member_id?'본인 대화 '+this.view.private_conversation.length+'개 · ':'')+suffix;
  for(const b of $('modules').querySelectorAll('[data-module]')){
   const key=b.dataset.module;const stale=this.view.input_states?.[key]?.stale||this.resultStates[key]?.stale;
   b.title=stale?'이전 기록 기준':this.view.inputs[key]?'입력 준비됨 · 내용 확인 후 실행':'입력 필요';
  }
 }
 async beforeCall(path,body){
  await this.flush();const key=path+JSON.stringify(body);const action=this.actions[key]??=crypto.randomUUID();
  return {...body,record_id:this.view.record_id,expected_revision:this.view.revision,action_id:action};
 }
 afterCall(path,body,result){
  if(result?.error?.code==='STORAGE_FAILED'){
   if(result.unsaved_result&&path.startsWith('/api/run/')){this.unsavedResult[module]=result.unsaved_result;renderResult();}
   this.badge('AI 결과 저장 실패 · 같은 동작으로 저장 재시도',true);return;
  }
  if(path.startsWith('/api/run/')){delete this.unsavedResult[module];this.resultStates[module]={stale:false};}
  if(result?.status==='OK'&&(path.startsWith('/api/run/')||path==='/api/review'))this.previousSuccess[module]=structuredClone(result);
  // Network retries retain their action ID; terminal responses complete the action.
  for(const[k,v]of Object.entries(this.actions))if(v===body.action_id)delete this.actions[k];
 }
}
const records=new RecordController();
$('recordNew').onclick=()=>safe(()=>changeInput(()=>records.create()));
$('recordList').onchange=()=>safe(()=>changeInput(()=>records.open($('recordList').value)));
$('recordPrepare').onclick=()=>safe(async()=>{await records.flush();const result=await records.prepare(module,true);showStatus(result.stale?'이전 입력의 근거가 바뀌었습니다. 필요한 항목을 검토해주세요.':'저장된 기록으로 입력을 준비했습니다. 실행 버튼을 누르기 전에는 AI를 호출하지 않습니다.');});
$('recordOwner').onchange=()=>safe(()=>changeInput(async()=>{try{records.assertIdle();}catch(e){$('recordOwner').value=records.owner;throw e;}records.conceal();await records.flush();records.owner=$('recordOwner').value;await records.open(records.view.record_id,module,false);await records.prepare(module,false);}));
window.addEventListener('beforeunload',()=>{if(records.ready&&records.dirty){const input=structuredClone(current());delete input._speaker;const body={module,input,pending:pending[module]||'',expected_revision:records.view.revision,metadata:{selected_module:module}};navigator.sendBeacon('/api/records/'+records.view.record_id+'/input',new Blob([JSON.stringify(body)],{type:'application/json'}));}});
records.init();
$('mode').onchange=()=>records.schedule();
