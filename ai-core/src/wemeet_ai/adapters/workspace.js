/* Local-only persistence adapter; all AI functions still take explicit inputs. */
let workspaceReady=false,workspaceTimer=null,workspaceQueue=Promise.resolve(),lastWorkspaceJSON='';
function snapshotWorkspace(){const copy=structuredClone(state);delete copy.workspaceId;delete copy.workspaceRevision;copy.runs=copy.runs.map(r=>({...r,trace_archived:Boolean(r.trace_archived||r.traces?.length),traces:[]}));copy._ui={speaker:owner(),target:$('target').value,scope:$('scope').value,reference:$('reference').value,timezone:$('tz').value,message:$('message').value,personalMessage:$('personalMessage').value};return copy;}
function saveStatus(text,failed=false){$('saveStatus').textContent=text;$('saveStatus').className=failed?'field-error':'muted';$('retrySave').hidden=!failed;}
function scheduleWorkspaceSave(){if(!workspaceReady)return;clearTimeout(workspaceTimer);saveStatus('저장 대기…');workspaceTimer=setTimeout(()=>flushWorkspace().catch(()=>{}),350);}
function flushWorkspace(){clearTimeout(workspaceTimer);if(!workspaceReady||!state.workspaceId){const error=Error('로컬 저장 연결을 먼저 확인해주세요. 저장되지 않은 상태로 AI를 호출하지 않습니다.');error.storage=true;return Promise.reject(error);}const snapshot=snapshotWorkspace(),encoded=JSON.stringify(snapshot),id=state.workspaceId;workspaceQueue=workspaceQueue.catch(()=>{}).then(async()=>{if(encoded===lastWorkspaceJSON)return;if(id!==state.workspaceId)throw Error('작업이 바뀌어 이전 저장을 중단했습니다.');saveStatus('로컬 DB에 저장 중…');try{const response=await fetch('/ai/v1/workspaces/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot,expected_revision:state.workspaceRevision})});if(!response.ok)throw Error(response.status===409?'다른 탭에서 작업이 변경됐습니다. 저장된 작업을 다시 불러오세요.':'로컬 DB 저장 실패');const saved=await response.json();state.workspaceRevision=saved.revision;lastWorkspaceJSON=encoded;saveStatus('자동 저장됨 · '+new Date(saved.updated_at).toLocaleTimeString());}catch(error){error.storage=true;saveStatus(error.message,true);throw error;}});return workspaceQueue;}
async function refreshWorkspaceList(){const response=await fetch('/ai/v1/workspaces');if(!response.ok)throw Error('저장 목록을 불러오지 못했습니다.');const list=await response.json();$('workspaceList').replaceChildren(...list.map(w=>Object.assign(el('option',w.title||'진행 중인 목표 대화'),{value:w.id})));$('workspaceList').value=state.workspaceId||'';return list;}
async function createCurrentWorkspace(){clearTimeout(workspaceTimer);const snapshot=snapshotWorkspace();const response=await fetch('/ai/v1/workspaces',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot})});if(!response.ok)throw Error('새 목표 작업 저장 실패');const saved=await response.json();state.workspaceId=saved.id;state.workspaceRevision=saved.revision;lastWorkspaceJSON='';await refreshWorkspaceList();await flushWorkspace();}
function restoreWorkspace(saved){workspaceReady=false;const snapshot=saved.snapshot;reset();Object.assign(state,snapshot);delete state._ui;state.workspaceId=saved.id;state.workspaceRevision=saved.revision;const ui=snapshot._ui||{};setMembers(state.members);if(ui.speaker)$('speaker').value=ui.speaker;if(ui.target)$('target').value=ui.target;$('scope').value=ui.scope||'GROUP';if(ui.reference)$('reference').value=ui.reference;if(ui.timezone)$('tz').value=ui.timezone;$('message').value=ui.message||'';$('personalMessage').value=ui.personalMessage||'';showStep(state.step==='E'&&!state.group?'A':state.step||'A');const last=visibleRuns().at(-1);notice(last?'저장된 '+state.step+' 단계 복원 · 마지막 '+last.operation+' · '+last.status:'저장된 '+state.step+' 단계 복원 · 아직 AI 실행 기록 없음');workspaceReady=true;lastWorkspaceJSON=JSON.stringify(snapshotWorkspace());saveStatus('저장된 작업 복원됨');}
async function loadWorkspace(id){await flushWorkspace();const response=await fetch('/ai/v1/workspaces/'+id);if(!response.ok)throw Error('저장된 작업을 불러오지 못했습니다.');restoreWorkspace(await response.json());await refreshWorkspaceList();}
async function refreshProviderStatus(){const response=await fetch('/ai/v1/provider/status');if(!response.ok)throw Error('사용 내역을 읽지 못했습니다.');const data=await response.json();$('providerDiagnostics').hidden=false;$('providerDiagnostics').textContent=pretty(data);$('config').textContent=(data.configured?'DeepSeek 설정됨':'DeepSeek 키 없음')+' · '+data.model+(data.live_calls_paused?' · LIVE 호출 보류':' · LIVE 사용 가능');return data;}

function renderBaselineState(){const list=state.baselines||[],group=list.find(b=>b.scope==='GROUP'&&b.group_plan_version_id===state.group?.plan_id),mid=$('target').value,personal=list.find(b=>b.scope==='PERSONAL'&&b.owner_member_id===mid&&b.plan_version_id===state.memberPlans[mid]?.public_plan.plan_id);$('baselineStatus').textContent=group?'현재 공동 계획의 평가 기준 확인됨 · '+group.plan_version_id:'현재 공동 계획은 평가 기준 확인 전입니다. 과거 기준 '+list.filter(b=>b.scope==='GROUP').length+'개 보존 중';$('personalBaselineStatus').textContent=personal?'현재 개인 계획의 평가 기준 확인됨 · '+personal.plan_version_id:'현재 개인 계획은 평가 기준 확인 전입니다.';}
async function confirmBaseline(scope){requireSpec();if(!state.group)return;const mid=owner();const plans=scope==='PERSONAL'?[state.memberPlans[mid]].filter(Boolean):[];if(scope==='PERSONAL'&&!plans.length){notice('개인 계획을 먼저 생성·검증해주세요.');return;}await flushWorkspace();const result=await call('baselines',{scope,confirmed_by:mid,owner_member_id:scope==='PERSONAL'?mid:null,goal_spec:state.spec,group_plan:state.group,member_plans:plans,member_profiles:Object.values(state.profiles),require_all_members:false},scope==='PERSONAL'?mid:null);if(result.status!=='OK')return;state.baselines??=[];if(!state.baselines.some(b=>b.baseline_id===result.data.baseline_id))state.baselines.push(result.data);const target=scope==='PERSONAL'?'personalBaselineStatus':'baselineStatus';$(target).textContent='평가 기준 저장됨 · 버전 '+result.data.plan_version_id+' · 서비스 활성화는 SW 승인 필요';scheduleWorkspaceSave();}
$('workspaceList').onchange=safe(()=>loadWorkspace($('workspaceList').value));$('newWorkspace').onclick=safe(async()=>{await flushWorkspace();reset();$('message').value='';$('personalMessage').value='';delete state.workspaceId;delete state.workspaceRevision;showStep('A');await createCurrentWorkspace();});$('retrySave').onclick=safe(()=>state.workspaceId?flushWorkspace():initializeWorkspaceStore());$('providerStatusBtn').onclick=safe(refreshProviderStatus);$('probeProvider').onclick=safe(async()=>{const result=await call('provider/probe',{});await refreshProviderStatus();if(result.status==='OK')notice('실제 DeepSeek 응답 확인 · '+result.meta.usage.total_tokens+' tokens · 요청 ID '+result.meta.provider_request_ids.join(', '));});$('confirmPersonalBaseline').onclick=safe(()=>confirmBaseline('PERSONAL'));$('confirmGroupBaseline').onclick=safe(()=>confirmBaseline('GROUP'));
document.addEventListener('input',scheduleWorkspaceSave);document.addEventListener('change',scheduleWorkspaceSave);
$('loadExample').onclick=safe(async()=>{
 const selected=$('example').value;if(!selected)return;
 const data=await selectedExample(selected); // Validate/download before replacing current memory.
 await flushWorkspace();applyTestExample(data);await createCurrentWorkspace();
});
const importHandler=$('file').onchange;$('file').onchange=safe(async()=>{
 const file=$('file').files[0];if(!file)return;
 const data=JSON.parse(await file.text());
 if(data.group_input||data.scenario?.group_input){const scenario=data.scenario||data;validateScenarioImport(scenario);await flushWorkspace();applyTestExample(scenario);await createCurrentWorkspace();}
 else{await flushWorkspace();await importHandler();await createCurrentWorkspace();}
 $('file').value='';
});
const showStepOriginal=showStep;showStep=function(step){showStepOriginal(step);scheduleWorkspaceSave();};
async function initializeWorkspaceStore(){document.querySelectorAll('button,input,textarea,select').forEach(b=>b.disabled=true);try{const list=await refreshWorkspaceList();if(list.length){const response=await fetch('/ai/v1/workspaces/'+(new URLSearchParams(location.search).get('workspace')||list[0].id));restoreWorkspace(await response.json());}else{workspaceReady=true;await createCurrentWorkspace();}workspaceReady=true;await refreshWorkspaceList();await refreshProviderStatus();$('providerDiagnostics').hidden=true;}catch(error){saveStatus(error.message,true);fail(error);}finally{document.querySelectorAll('button,input,textarea,select').forEach(b=>b.disabled=false);render();}}
initializeWorkspaceStore();

// Explicit authored-stage input: preview only; clicking Execute is the first AI call.
async function loadScenarioEntry(){
 if(!state.scenarioEntry)return;
 const entry=state.scenarioEntry, operation={A:'coach',B:'analyze',C:'goal-spec/draft',E:'member-goal/coach',PLAN:'group-plan'}[entry.module];
 $('operation').value=operation;$('request').value=JSON.stringify(entry.input,null,2);
 notice('지정 단계 '+entry.module+' 입력을 불러왔습니다 · '+entry.prerequisite_source+' · AI 미실행. 아래 SW 연동 요청의 실행 버튼으로 호출합니다.');
 const banner=el('div',undefined,'card');banner.id='scenarioEntryBanner';
 banner.append(el('strong','시나리오 '+state.scenarioId+' · '+entry.module+' 단계'),el('p','선행 입력 출처: '+entry.prerequisite_source+' · 작성 기준안을 불러왔으며 실제 AI 결과·사람 승인이 아닙니다.'));
 const execute=el('button','이 단계 실행 · 실제 DeepSeek 호출');execute.onclick=safe(async()=>{
  if(entry.module==='A')await runCoach();else if(entry.module==='B')await analyze();else if(entry.module==='C')await draftOnly();else if(entry.module==='E')await requestPersonalCoach();else $('group').click();
 });banner.append(execute);$('steps').after(banner);
}
setTimeout(()=>{if(new URLSearchParams(location.search).has('scenario-entry'))loadScenarioEntry();},1500);

// The existing Test examples / JSON entry point uses the same catalog as /scenarios.
let exampleCatalog=[],exampleDomains={};
const previousExamples=Array.from($('example').options).filter(o=>o.value).map(o=>({value:o.value,title:o.textContent}));
function validateScenarioImport(value){
 const input=value.group_input;
 if(!value.case_id||!input||!Array.isArray(input.members)||!input.members.length||!Array.isArray(input.conversation)||!input.reference_date)throw Error('시나리오 JSON의 case_id·구성원·대화·기준 날짜를 확인해주세요.');
 const ids=new Set(input.members.map(m=>m.member_id));
 if(ids.size!==input.members.length||input.members.some(m=>!m.member_id||!m.nickname)||input.conversation.some(m=>!m.message_id||typeof m.text!=='string'||m.role==='user'&&!ids.has(m.speaker_id)))throw Error('시나리오의 구성원과 대화 연결이 올바르지 않습니다. 기존 작업을 유지했습니다.');
}
function applyTestExample(value){
 if(value.group_input){
  validateScenarioImport(value);applyExample(value.group_input);state.scenarioId=value.case_id;state.scenarioFixture=structuredClone(value);
  $('tz').value=value.group_input.timezone||'Asia/Seoul';
  notice((value.kind==='VARIANT'?'조건 변형':'기본 사례')+' · '+value.title+' · 대화부터 시작 · 기준안·AI 미실행. 개인 조건과 기대 결과는 선택 예제 JSON에 보존했습니다.');
 }else applyExample(value);
}
async function selectedExample(selected){
 const scenario=selected.startsWith('scenario:');
 const response=await fetch(scenario?'/ai/v1/scenarios/'+encodeURIComponent(selected.slice(9)):'/ai/v1/examples/'+encodeURIComponent(selected));
 if(!response.ok)throw Error('선택한 테스트 예제를 불러오지 못했습니다. 기존 작업을 유지합니다.');
 const data=await response.json(),value=scenario?data.scenario:data;
 if(scenario)validateScenarioImport(value);return value;
}
function renderExampleCatalog(){
 const selected=$('example').value,q=$('exampleSearch').value.trim().toLowerCase(),domain=$('exampleDomain').value;
 const matches=exampleCatalog.filter(c=>(!domain||c.domain===domain)&&(!q||[c.title,c.summary,c.case_id,...c.tags].join(' ').toLowerCase().includes(q)));
 $('example').replaceChildren(Object.assign(el('option','예제를 선택해주세요'),{value:''}));
 for(const [key,label]of Object.entries(exampleDomains)){
  const cases=matches.filter(c=>c.domain===key);if(!cases.length)continue;
  const group=document.createElement('optgroup');group.label=label+' · '+cases.length+'개';
  for(const c of cases)group.append(Object.assign(el('option',(c.kind==='BASE'?'[기본] ':'[변형] ')+c.title+' · '+c.entry_module),{value:'scenario:'+c.case_id}));
  $('example').append(group);
 }
 if(!domain){const legacy=document.createElement('optgroup');legacy.label='기존 회귀 예제 · 호환 유지';for(const old of previousExamples)if(!q||old.title.toLowerCase().includes(q))legacy.append(Object.assign(el('option',old.title),{value:old.value}));if(legacy.children.length)$('example').append(legacy);}
 if(Array.from($('example').options).some(o=>o.value===selected))$('example').value=selected;
 $('exampleCount').textContent='시나리오 '+matches.length+' / '+exampleCatalog.length+'개 · 기본/변형 구분 · 불러오기만으로 AI를 호출하지 않습니다.';
}
async function initializeExampleCatalog(){
 try{
  const first=await fetch('/ai/v1/scenarios').then(r=>{if(!r.ok)throw Error('목록 연결 실패');return r.json()});
  const rest=await Promise.all(Array.from({length:Math.ceil(first.total_filtered/first.page_size)-1},(_,i)=>fetch('/ai/v1/scenarios?page='+(i+2)).then(r=>{if(!r.ok)throw Error('목록 일부를 불러오지 못했습니다.');return r.json()})));
  exampleCatalog=[...first.items,...rest.flatMap(p=>p.items)];exampleDomains=first.domains;
  $('exampleDomain').replaceChildren(Object.assign(el('option','모든 분야'),{value:''}),...Object.entries(exampleDomains).map(([value,label])=>Object.assign(el('option',label),{value})));
  renderExampleCatalog();$('retryExamples').hidden=true;
 }catch(error){$('retryExamples').hidden=false;$('exampleCount').textContent='200개 목록 연결 실패 · 기존 예제는 사용할 수 있습니다. '+error.message;}
}
$('exampleSearch').oninput=renderExampleCatalog;$('exampleDomain').onchange=renderExampleCatalog;
$('saveExampleJson').onclick=safe(async()=>{const selected=$('example').value;if(!selected)throw Error('저장할 테스트 예제를 선택해주세요.');const data=await selectedExample(selected);download(data,(data.case_id||selected)+'.json');});
initializeExampleCatalog();

$('retryExamples').onclick=safe(initializeExampleCatalog);
