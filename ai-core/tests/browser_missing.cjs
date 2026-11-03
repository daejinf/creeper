// Explicit LIVE regression: incomplete discussion must still reach a group plan.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1550,height:1050}});
 const errors=[],results=[];const report={live:true,assertions:[],passed:false};
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async r=>{if(r.request().method()==='POST'&&r.url().includes('/ai/v1/')){try{results.push({url:r.url(),body:await r.json()});}catch{}}});
 async function run(selector){const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/ai/v1/'));await page.locator(selector).click();const response=await waiting;const result=await response.json();await page.waitForFunction(()=>!document.querySelector('#coach').disabled,null,{timeout:180000});assert.equal(result.status,'OK',JSON.stringify(result));return result;}
 try{
  await page.goto('http://127.0.0.1:8010');
  await page.locator('#message').fill('우리 팀은 함께 쓸 할 일 관리 웹 데모를 만들고 싶어. 할 일 추가와 완료 체크를 직접 시연하는 결과물이 필요해.');
  await page.locator('#append').click();
  const analysis=await run('#analyze');
  assert.ok(Object.values(analysis.data.fields).some(f=>f.status==='MISSING'));
  report.assertions.push('Incomplete discussion retains MISSING facts and still opens C');
  const draft=await run('#draftOnly');
  for(const key of ['goal','expected_outcome','completion_criteria','start_date','end_date','execution_method','verification_method','privacy_scope']){
   assert.ok(draft.data.fields[key]?.value,'Missing required proposal '+key);
  }
  assert.equal(draft.data.fields.start_date.origin,'AI_SUGGESTED');
  assert.equal(draft.data.fields.privacy_scope.origin,'AI_SUGGESTED');
  const before=draft.data.fields.goal;
  const supplemented=await run('[data-field="verification_method"] button:last-child');
  assert.deepEqual(supplemented.data.fields.goal,before);
  report.assertions.push('AI proposes missing dates/verification/privacy; targeted supplementation preserves other cards');
  await page.locator('#acceptAll').click();
  const spec=await run('#prepare');
  assert.ok(spec.data.members.length>0);assert.ok(!('member_profiles' in spec.data));
  const group=await run('#group');
  await page.locator('[data-step="F"]').click();
  const groupValidation=await run('#validateGroup');assert.equal(groupValidation.data.valid,true,JSON.stringify(groupValidation.data));
  const diagnostic=await run('#handoff');assert.equal(diagnostic.data.group_file.integration_ready,false);
  assert.ok(diagnostic.data.group_file.validation.issues.some(i=>i.code==='PROFILE_COVERAGE'));
  report.assertions.push('Zero profiles: confirmed group specification, group plan and group validation succeed; handoff remains diagnostic');
  await page.locator('[data-step="D"]').click();
  await page.locator('#member').click();assert.ok((await page.locator('#error').textContent()).includes('개인 조건 필요'));
  await page.locator('#intakeBtn').click();await page.locator('#level').fill('초보');await page.locator('#minutes').fill('180');await page.locator('#saveIntake').click();
  const member=await run('#member');assert.ok(member.data.public_plan.first_week_todos.length>0);
  await page.locator('[data-step="F"]').click();
  const full=await run('#validate');assert.equal(full.data.valid,false);
  report.assertions.push('Only the selected person needs private conditions; adding that profile preserves the group plan and permits personal generation');
  assert.deepEqual(errors,[]);
  report.passed=true;report.runs=results.map(r=>({endpoint:r.url.split('/ai/v1/')[1],status:r.body.status,meta:r.body.meta}));
  await page.screenshot({path:'runs/missing-flow.png',fullPage:true});
 }catch(error){report.error=error.stack;await page.screenshot({path:'runs/missing-flow-failed.png',fullPage:true});throw error;}
 finally{fs.writeFileSync('runs/browser-missing.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify({passed:report.passed,assertions:report.assertions}));
})();
