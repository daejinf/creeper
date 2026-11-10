// Dedicated synthetic-record database only. Every model request explicitly uses FIXTURE.
const {chromium}=require('playwright');const fs=require('fs');
const url=process.env.WEMEET_BENCH_URL||'http://127.0.0.1:8011/';
const folder=process.env.EVALUATION_FOLDER||'runtime/evaluation-012/round-2';
(async()=>{
 fs.mkdirSync(folder,{recursive:true});const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050}});const errors=[];let modelRequests=0;
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/run/')){modelRequests++;const b=r.postDataJSON();if(b.mode!=='FIXTURE')throw Error('Paid test request forbidden')}});
 const ready=()=>page.waitForFunction(()=>typeof records!=='undefined'&&records.ready&&!busy&&!inputLoading&&!records.dirty&&document.querySelector('#recordSave').textContent.includes('완료'),{},{timeout:15000});
 async function choose(m){await page.locator(`[data-module="${m.startsWith('E.')?'E.coach':m}"]`).click();await page.waitForFunction(m=>document.querySelector('#moduleCode').textContent==='M1-'+m,m);await ready();}
 async function run(){if(await page.locator('#mode').inputValue()!=='FIXTURE')throw Error('Mode lost');await page.click('#run');await page.waitForFunction(()=>!busy&&document.querySelector('#status').textContent.startsWith('OK'),{},{timeout:15000});await ready();}
 await page.goto(url);await ready();await page.selectOption('#mode','FIXTURE');await ready();await choose('A');await page.click('#loadExample');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('작성된 예제'));await ready();
 const id=await page.locator('#recordList').inputValue();await run();
 await page.locator('#form textarea').first().fill('자동 보존 개인화 연결 회귀 012');await page.getByRole('button',{name:'대화만 추가',exact:true}).click();await ready();
 await page.reload();await ready();if(await page.locator('#recordList').inputValue()!==id)throw Error('Wrong record restored');if(await page.locator('#mode').inputValue()!=='FIXTURE')throw Error('Mode not restored');if(!(await page.locator('#form').textContent()).includes('자동 보존 개인화 연결 회귀 012'))throw Error('Message lost');
 const beforePrepare=modelRequests;await choose('B');if(modelRequests!==beforePrepare)throw Error('Preparing called AI');const b=JSON.parse(await page.locator('#raw').inputValue());if(!b.conversation.some(m=>m.text.includes('자동 보존')))throw Error('B missing dialogue');await run();
 await choose('C');const c=JSON.parse(await page.locator('#raw').inputValue());if(!c.facts.goal)throw Error('C facts missing');await run();
 for(let i=0;i<12;i++){const key=await page.evaluate(()=>Object.entries(outputs.C.data.fields).find(([k,v])=>v.review_status==='PENDING'&&v.value!==null)?.[0]);if(!key)break;const title=await page.evaluate(k=>labels[k]||k,key);const card=page.locator('#result article').filter({has:page.getByRole('heading',{name:title,exact:true})});await card.getByRole('button',{name:'이 값 수락',exact:true}).click();await page.waitForFunction(k=>outputs.C.data.fields[k].review_status!=='PENDING',key);await ready();}
 await page.getByRole('button',{name:'검토한 공동 조건 확인 · AI 호출 없음',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#moduleCode').textContent==='M1-D');await ready();await run();
 await choose('E.coach');
 await page.locator('#run').click();await page.waitForFunction(()=>!busy&&document.querySelector('#status').textContent.startsWith('OK'));await ready();
 for(let i=0;i<3;i++){await page.locator('#result article').filter({has:page.locator('button')}).last().locator('button').first().click();await page.waitForFunction(()=>!busy);await ready();}
 let response=JSON.parse(await page.locator('#response').textContent());if(!response.data.state.fields.current_progress)throw Error('Third condition missing');
 await page.getByRole('button',{name:'현재 정보로 기여 초안 보기',exact:true}).click();await page.waitForFunction(()=>!busy&&document.querySelector('#moduleCode').textContent==='M1-E.draft');await ready();
 await page.getByRole('button',{name:'개인 계획 입력으로 가져와 수정·확인',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#moduleCode').textContent==='M1-E.plan');await ready();
 for(const checkbox of await page.locator('#form input[type="checkbox"]').all())await checkbox.check();await ready();await run();
 response=JSON.parse(await page.locator('#response').textContent());const planId=response.data.plan_version_id;
 await choose('F');await run();response=JSON.parse(await page.locator('#response').textContent());if(!response.data.valid)throw Error('F validation failed');
 await page.reload();await ready();if(!JSON.parse(await page.locator('#response').textContent()).data.valid)throw Error('F result not restored');
 await page.screenshot({path:folder+'/record-flow.png',fullPage:true});
 const result={record_id:id,shared_dialogue_restored:true,mode_restored:true,C_to_D:true,personal_third_answer:true,plan_id:planId,F_restored:true,model_requests:modelRequests,new_paid_http:0,js_errors:errors};
 fs.writeFileSync(folder+'/browser-records.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
