// Explicit LIVE E-stage regression. No login/DB, anonymous fixtures only.
const assert=require('node:assert/strict');const fs=require('node:fs');const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1550,height:1100},acceptDownloads:true});
 const errors=[],runs=[];let savedSession;const report={live:true,passed:false,assertions:[]};
 page.on('pageerror',e=>errors.push(e.message));
 async function run(selector){const responsePromise=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/ai/v1/')&&(selector!=='#prepare'||r.url().endsWith('/group-goal-spec')),{timeout:180000});await page.locator(selector).click();const response=await responsePromise;const result=await response.json();await page.waitForFunction(()=>!document.querySelector('#coach').disabled,null,{timeout:180000});runs.push({endpoint:response.url().split('/ai/v1/')[1],result});assert.equal(result.status,'OK',JSON.stringify(result));return result;}
 try{
  await page.goto('http://127.0.0.1:8010');await page.locator('.toolbar summary').click();await page.locator('#example').selectOption('project');await page.locator('#loadExample').click();await page.locator('#acceptAll').click();await page.waitForFunction(()=>!document.querySelector('#coach').disabled);await run('#prepare');await run('#group');
  let coached=await run('#enterPersonal');assert.ok(await page.locator('[data-panel="E"]').isVisible());assert.ok(!(await page.locator('[data-panel="A"]').isVisible()));report.assertions.push('D group plan → E private coaching without returning to A');
  for(let turn=0;turn<5&&coached.data.state.current_question;turn++){
   const q=coached.data.state.current_question;
   let option=q.target_field==='weekly_available_minutes'?q.options.find(o=>o.action==='VALUE'&&o.value>=120):q.target_field==='constraints'?q.options.find(o=>o.action==='SKIP'):q.options.find(o=>o.action==='VALUE');
   option??=q.options[0];const selector='[data-question="'+q.question_id+'"] [data-option="'+option.option_id+'"]';
   if(q.multiple&&option.action==='VALUE'){await page.locator(selector).click();coached=await run('[data-question="'+q.question_id+'"] button:last-child');}else coached=await run(selector);
   assert.equal(await page.locator('[data-question="'+q.question_id+'"] button:enabled').count(),0);
  }
  report.assertions.push('AI-generated buttons auto-submit, normalize answers and lock answered questions');
  let draft=await run('#personalDraft');
  if(draft.data.missing_fields.length){
   // Recover in E through free text when the conversation has not yet supplied essentials.
   await page.waitForFunction(()=>!document.querySelector('#coach').disabled,null,{timeout:180000});
   await page.locator('#personalMessage').fill('처음 해보는 초보이고 일주일에 120분 가능해요. 이 조건으로 기여 초안을 정리해주세요.');await run('#sendPersonal');draft=await run('#personalDraft');
  }
  assert.equal(draft.data.missing_fields.length,0,JSON.stringify(draft));
  const member=await run('#member');assert.ok(member.data.contribution_goal);assert.equal(member.data.contribution_goal.review_status,'ACCEPTED');assert.ok(member.data.public_plan.first_week_todos.length>0);
  report.assertions.push('Private conversation → contribution review → confirmed personal plan in E, without a separate intake form');
  await page.locator('#personalToValidation').click();const validation=await run('#validateGroup');assert.equal(validation.data.valid,true);const handoff=await run('#handoff');assert.equal(handoff.data.group_file.integration_ready,false);assert.equal(handoff.data.group_file.group_payload.contribution_goals.length,1);assert.ok(!JSON.stringify(handoff.data.group_file).includes('personal_conversations'));
  const download=page.waitForEvent('download');await page.locator('#downloadGroup').click();await(await download).saveAs('runs/personal-group-export.json');
  report.assertions.push('Public contribution exported separately; missing other members keeps integration_ready=false');
  await page.locator('[data-step="E"]').click();const members=await page.locator('#personalTarget option').evaluateAll(nodes=>nodes.map(n=>n.value));
  const newResponse=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/member-goal/coach'),{timeout:180000});await page.locator('#personalTarget').selectOption(members[1]);const other=await newResponse;const otherInput=other.request().postDataJSON();assert.equal(otherInput.conversation.length,0);assert.equal(otherInput.owner_member_id,members[1]);await page.waitForFunction(()=>!document.querySelector('#coach').disabled,null,{timeout:180000});assert.equal(await page.locator('#personalChat .bubble').count(),1);
  report.assertions.push('Switching members restores isolated conversation, state and traces');
  const sessionDownload=page.waitForEvent('download');await page.locator('#saveSession').click();await(await sessionDownload).saveAs('runs/personal-live-session.json');savedSession=true;
  assert.deepEqual(errors,[]);report.passed=true;await page.screenshot({path:'runs/personal-flow.png',fullPage:true});
 }catch(error){report.error=error.stack;await page.screenshot({path:'runs/personal-flow-failed.png',fullPage:true});throw error;}
 finally{report.runs=runs.map(r=>({endpoint:r.endpoint,status:r.result.status,meta:r.result.meta}));fs.writeFileSync('runs/browser-personal.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify({passed:report.passed,assertions:report.assertions}));
})();
