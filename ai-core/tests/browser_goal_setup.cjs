// Uses saved LIVE state, actual CODE-only edit/accept/confirm routes, and saved plan replay.
const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert');
(async()=>{
 const live=JSON.parse(fs.readFileSync('ai-core/runs/v10-live/design.json','utf8'));
 const reviewed=live.steps.findLast(s=>s.request.action==='REVIEW'&&s.response.status==='OK').response.data.state;
 const plan=live.steps.findLast(s=>s.operation==='group-plan'&&s.response.status==='OK').response;
 const browser=await chromium.launch({channel:'msedge',headless:true}),p=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],metas=[];
 p.on('pageerror',e=>errors.push(e.message));
 p.on('response',async r=>{if(r.url().includes('/ai/v1/goal-setup')){const d=await r.json();metas.push(d.meta);assert.equal(d.meta.transport_attempts,0);}});
 await p.route('**/ai/v1/group-plan',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...plan,meta:{...plan.meta,execution_source:'REPLAY',usage:{total_tokens:0},transport_attempts:0}})}));
 await p.goto('http://127.0.0.1:8010/');await p.waitForFunction(()=>workspaceReady&&exampleCatalog.length===200);
 await p.locator('#newWorkspace').click();await p.waitForFunction(()=>!state.groupChat.length);
 await p.evaluate(s=>{state.members=s.members;state.groupChat=s.conversation;state.analysis=s.analysis;state.draft=s.draft;state.goalSetup=s;state.analysisVersion=state.groupVersion;state.draftSourceVersion=state.groupVersion;$('reference').value=s.reference_date;$('tz').value=s.timezone;state.draft.fields.start_date={value:null,origin:'UNRESOLVED',review_status:'PENDING',reason:'시작할 날짜를 알려주세요.',evidence_refs:[],validation_errors:[]};state.goalSetup.draft=structuredClone(state.draft);render();showStep('C');},reviewed);
 assert.equal(await p.getByText('A에서 대화로 보완',{exact:true}).count(),0);
 assert.equal(await p.locator('[data-field="start_date"] button:text-is("수락")').count(),0);
 async function click(selector,endpoint){const waiting=p.waitForResponse(r=>r.url().endsWith('/ai/v1/'+endpoint)&&r.request().method()==='POST');await p.locator(selector).click();await waiting;await p.waitForFunction(()=>!busy);}
 await p.getByLabel('시작일 답변',{exact:true}).fill('2026-10-28');
 await click('[data-field="start_date"] button:text-is("답변 반영")','goal-setup');
 assert.equal(await p.evaluate(()=>state.step),'C');assert.equal(await p.evaluate(()=>state.draft.fields.start_date.value),'2026-10-28');
 await p.locator('[data-field="checkins"] button:text-is("수정")').click();await p.waitForFunction(()=>state.draftEditors.checkins&&!state.draftEditors.checkins.loading);
 if(!await p.getByLabel('확인할 일정 1',{exact:true}).count())await p.locator('[data-field="checkins"] button:text-is("항목 추가")').click();
 await p.getByLabel('확인할 일정 1',{exact:true}).fill('매주 화요일 · 사진 인증');
 await click('[data-field="checkins"] button:text-is("수정·확인")','goal-setup');
 assert.equal(await p.evaluate(()=>state.draft.fields.checkins.value[0]),'매주 화요일 · 사진 인증');
 const widths=[];
 for(const width of [390,768,1024,1440]){await p.setViewportSize({width,height:1000});await p.waitForTimeout(100);const sizes=await p.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));assert(sizes.scroll<=sizes.client+1,JSON.stringify({width,...sizes}));widths.push({width,...sizes});}
 await click('#acceptAll','goal-setup');await click('#prepare','goal-setup');assert.equal(await p.evaluate(()=>state.step),'D');
 assert.equal(await p.evaluate(()=>state.spec.checkins[0]),'매주 화요일 · 사진 인증');
 await click('#group','group-plan');assert(await p.locator('#groupCards').textContent());
 await p.screenshot({path:'ai-core/runs/v10-review-to-plan.png',fullPage:false});
 await p.waitForFunction(()=>$('saveStatus').textContent.includes('자동 저장됨'));await p.reload();await p.waitForFunction(()=>workspaceReady&&exampleCatalog.length===200);
 assert.equal(await p.evaluate(()=>state.step),'D');assert.equal(await p.evaluate(()=>state.draft.fields.checkins.value[0]),'매주 화요일 · 사진 인증');assert.deepEqual(errors,[]);
 fs.writeFileSync('ai-core/runs/browser-goal-setup-v10.json',JSON.stringify({passed:true,source:'saved LIVE state + CODE routes + plan REPLAY',new_paid_calls:0,widths,checks:['C inline answer without A navigation','no accept button for missing value','edit saved in next confirmed spec','all accept and confirm code only','D saved plan display','refresh preserves draft and stage'],errors},null,2));
 await browser.close();console.log('C inline -> confirm -> D; four widths and restore passed');
})().catch(e=>{console.error(e);process.exit(1)});
