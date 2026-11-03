// Real DeepSeek + browser regression for the developer bench. Anonymous fixtures only.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1100},acceptDownloads:true});
 const errors=[],responses=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async response=>{if(response.url().includes('/ai/v1/')&&response.request().method()==='POST'){try{responses.push({url:response.url(),body:await response.json()});}catch{}}});
 const report={live:true,assertions:[]};
 async function clickWait(selector){const before=responses.length;await page.locator(selector).click();await page.waitForFunction(()=>!document.querySelector('#coach').disabled,null,{timeout:180000});await page.waitForFunction(()=>!document.querySelector('#notice').textContent.includes('실行中'),null,{timeout:180000});assert.ok(responses.length>before,'No API response for '+selector);return responses.at(-1).body;}
 async function expectOK(selector){const result=await clickWait(selector);assert.equal(result.status,'OK',JSON.stringify(result));return result;}
 async function editField(key,value){const c=page.locator('[data-field="'+key+'"]');await c.getByRole('button',{name:'수정',exact:true}).click();const text=Array.isArray(value)&&['completion_criteria','checkins','custom_privacy_fields'].includes(key)?value.join('\n'):typeof value==='object'?JSON.stringify(value):String(value);await c.locator('textarea').fill(text);await c.getByRole('button',{name:'수정·확인',exact:true}).click();}
 try{
  await page.goto(process.env.WEMEET_TEST_URL||'http://127.0.0.1:8010');
  assert.equal(await page.locator('#chat .bubble').count(),0);
  assert.ok(await page.locator('#traceInput').isVisible());
  report.assertions.push('empty chat; developer input/response/validation panels visible');
  await page.locator('.toolbar summary').click();await page.locator('#example').selectOption('design');await page.locator('#loadExample').click();
  await page.waitForFunction(()=>document.querySelectorAll('#chat .bubble').length===7);
  await page.locator('#speaker').selectOption('j');await page.locator('#scope').selectOption('PRIVATE');
  await page.locator('#message').fill('화면 UI와 PPT 싹 다야. 백지 디자인이고 메인부터야. 직접 실행 계획을 만들어줘. 현재는 초보이고 일주일에 60분 가능해. 개인 제약은 없어.');
  await clickWait('#coach');
  const coached=responses.find(r=>r.url.endsWith('/coach')).body;
  assert.equal(coached.status,'OK',JSON.stringify(coached));assert.equal(coached.data.kind,'PLAN_PROPOSAL');assert.equal(coached.data.question,null);
  await page.locator('[data-panel="C"]').waitFor({state:'visible'});
  assert.ok(await page.locator('#draftCards .card').count()>5);
  assert.ok(responses.some(r=>r.url.endsWith('/workflows/goal-draft')));
  report.assertions.push('private plan request uses shared context and automatically runs B → AI C without another question');
  await page.locator('#intakeBtn').click();const intake=await expectOK('#analyzeIntake');assert.equal(intake.data.owner_member_id,'j');
  await page.locator('#level').fill('BEGINNER');await page.locator('#minutes').fill('60');await page.locator('#constraints').fill('테스트 개인 사유 J는 비공개');await page.locator('#saveIntake').click();
  for(const [mid,minutes,level]of[['s',180,'INTERMEDIATE'],['h',240,'INTERMEDIATE'],['y',300,'ADVANCED']]){await page.locator('#speaker').selectOption(mid);await page.locator('#intakeBtn').click();await page.locator('#level').fill(level);await page.locator('#minutes').fill(String(minutes));await page.locator('#preferences').fill('평일 저녁');await page.locator('#constraints').fill('');await page.locator('#saveIntake').click();}
  report.assertions.push('private analysis fills personal fields without group-mode error');
  await page.locator('[data-step="C"]').click();
  const confirmed={goal:'WE·MEET 목표 설정 데모 완성',expected_outcome:'목표 입력부터 개인 계획 확인까지 실행 가능한 시연',completion_criteria:['목표 입력·개인 계획 조회를 시연','핵심 흐름 테스트 결과 제출'],start_date:'2026-10-28',end_date:'2026-11-15',execution_method:'역할별 개발과 디자인 진행',verification_method:'매주 화요일 작업 결과 사진',privacy_scope:'OBJECTIVE_ONLY',frequency_per_week:1,metric:null,milestones:[{title:'중간 목표',due_date:'2026-11-03'},{title:'개발 완료',due_date:'2026-11-10'},{title:'최종 제출',due_date:'2026-11-15'}],roles:{j:'UI와 PPT 디자인',s:'DB',h:'프론트',y:'백엔드'}};
  for(const [key,value]of Object.entries(confirmed))await editField(key,value);
  await page.locator('#acceptAll').click();
  const prepared=await expectOK('#prepare');assert.equal(prepared.data.milestones.length,3);
  const shared=await expectOK('#group');assert.ok(shared.data.milestones.some(m=>m.due_date==='2026-11-10'));assert.ok(shared.data.milestones.some(m=>m.due_date==='2026-11-15'));
  for(const mid of ['j','s','h','y']){await page.locator('#target').selectOption(mid);await expectOK('#member');}
  await page.locator('[data-step="F"]').click();const validation=await expectOK('#validate');assert.equal(validation.data.valid,true,JSON.stringify(validation.data));
  const handoff=await expectOK('#handoff');assert.equal(handoff.data.group_file.integration_ready,true,JSON.stringify(handoff.data.group_file.validation));
  assert.ok(!JSON.stringify(handoff.data.group_file).includes('테스트 개인 사유 J는 비공개'));
  report.assertions.push('AI review cards → confirmed specification → group/4 members → validated handoff');
  const dir=path.resolve('runs');fs.mkdirSync(dir,{recursive:true});
  const [groupDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#downloadGroup').click()]);await groupDownload.saveAs(path.join(dir,'web-v2-group.json'));
  await page.locator('#speaker').selectOption('j');await page.locator('#scope').selectOption('PRIVATE');
  const [privateDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#downloadPrivate').click()]);await privateDownload.saveAs(path.join(dir,'web-v2-private-j.json'));
  assert.equal(JSON.parse(fs.readFileSync(path.join(dir,'web-v2-private-j.json'),'utf8')).owner_member_id,'j');
  report.assertions.push('separate group/private SW JSON downloads');
  await page.locator('#scope').selectOption('GROUP');await page.locator('#runList .run').filter({hasText:'goal-spec/draft'}).first().click();
  assert.ok((await page.locator('#traceRequest').textContent()).includes('/chat/completions'));
  assert.ok(!(await page.locator('.developer').textContent()).includes('테스트 개인 사유 J는 비공개'));
  assert.ok(!(await page.locator('.developer').textContent()).includes('Authorization'));
  report.assertions.push('actual request/response traces visible with private data and credentials scoped out');
  await page.screenshot({path:path.join(dir,'web-v2.png'),fullPage:true});
  await page.locator('[data-step="A"]').click();await page.locator('#message').fill('마감일은 11/16으로 다시 논의하자.');await page.locator('#append').click();await page.locator('[data-step="D"]').click();const count=responses.length;await page.locator('#group').click();await page.locator('#error').waitFor({state:'visible'});assert.equal(responses.length,count);assert.ok((await page.locator('#planStatus').textContent()).includes('이전 입력'));
  await page.locator('[data-step="F"]').click();assert.ok((await page.locator('#validationCards').textContent()).includes('이전 입력'));
  report.assertions.push('changed input blocks stale generation and marks old validation');
  assert.deepEqual(errors,[]);report.passed=true;fs.writeFileSync(path.join(dir,'browser-v2.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(error){fs.mkdirSync('runs',{recursive:true});await page.screenshot({path:'runs/web-v2-failed.png',fullPage:true});fs.writeFileSync('runs/browser-v2-failed.json',JSON.stringify({error:String(error),last:responses.at(-1),pageErrors:errors},null,2));throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
