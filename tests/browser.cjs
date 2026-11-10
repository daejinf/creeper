// Optional browser QA. Does not install Node into or import it from the AI core.
const {chromium}=require('playwright');
const fs=require('fs');const path=require('path');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const output=path.resolve(process.env.EVALUATION_FOLDER||'runtime/evaluation-012/round-3/browser');fs.mkdirSync(output,{recursive:true});
 const findings=[];
 for(const width of [390,768,1024,1440]){
  const page=await browser.newPage({viewport:{width,height:1000}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>typeof records!=='undefined'&&records.ready&&!busy&&!inputLoading&&!records.dirty&&document.querySelector('#recordSave').textContent.includes('완료'));
  await page.goto(process.env.WEMEET_BENCH_URL||'http://127.0.0.1:8011/');await page.locator('#connection').filter({hasText:'화면 조회는 AI 호출 없음'}).waitFor();
  await page.waitForFunction(()=>typeof records!=='undefined'&&records.ready);await page.selectOption('#mode','FIXTURE');await page.evaluate(()=>records.schedule());await ready();
  for(const module of ['A','B','C','D','E.coach','E.draft','E.plan','F']){
   await page.locator(`[data-module="${module.startsWith('E.')?'E.coach':module}"]`).click();await ready();
   if(module==='E.draft')await page.getByRole('button',{name:'기여 목표 정리',exact:true}).click();
   if(module==='E.plan')await page.getByRole('button',{name:'조건으로 바로 계획 생성',exact:true}).click();
   await page.waitForFunction(m=>module===m&&!inputLoading,module);await ready();console.log('checking',width,module);
   await page.locator('#loadExample').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('작성된 예제'));await ready();
   await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('작성된 예제'));
   await page.locator('#run').click();
   await page.waitForFunction(()=>!busy&&document.querySelector('#status').textContent.startsWith('OK'));await ready();
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
   findings.push({width,module,overflow,errors:[...errors]});
   if(overflow||errors.length)throw Error(JSON.stringify(findings.at(-1)));
   if(module==='A'){
    let posts=0;const count=req=>{if(req.url().includes('/api/run/'))posts++;};page.on('request',count);
    await page.locator('#run').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('AI 호출 없음'));
    page.off('request',count);if(posts)throw Error('Duplicate coaching click caused HTTP');
   }
   if(module==='E.coach'){
    for(let turn=0;turn<3;turn++){
     const q=page.locator('#result article').last();await q.locator('button').first().click();
     await ready();
    }
    const response=JSON.parse(await page.locator('#response').textContent());
    if(!response.data.state.fields.current_progress)throw Error('Third answer not recorded');
   }
   await page.screenshot({path:path.join(output,`${width}-${module.replace('.','-')}.png`),fullPage:true});
  }
  await page.locator('[data-module="C"]').click();await ready();await page.locator('#loadExample').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('작성된 예제'));await ready();await page.locator('#run').click();
  await page.waitForFunction(()=>!busy&&document.querySelector('#status').textContent.startsWith('OK'));await ready();
  const start=page.locator('#result article').filter({has:page.getByRole('heading',{name:'시작일',exact:true})});
  await start.locator('input').fill('2026-12-02');await start.getByRole('button',{name:'수정·확인',exact:true}).click();
  await page.waitForFunction(()=>JSON.parse(document.querySelector('#response').textContent).data.fields.start_date.value==='2026-12-02');
  const before=JSON.parse(await page.locator('#response').textContent());if(before.data.fields.start_date.review_status!=='EDITED')throw Error('Edit not saved');
  await ready();await page.locator('[data-module="D"]').click();await ready();await page.locator('[data-module="C"]').click();await ready();
  const after=JSON.parse(await page.locator('#response').textContent());if(after.data.fields.start_date.value!=='2026-12-02')throw Error('Edit lost after module switch');
  await page.close();
 }
 await browser.close();fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({findings,passed:true},null,2));console.log(JSON.stringify({checks:findings.length,widths:[390,768,1024,1440],passed:true}));
})().catch(e=>{console.error(e);process.exit(1)});
