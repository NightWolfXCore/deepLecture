// Manual browser regression: requires Playwright, Chromium and a fixture lecture.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_API_URL || 'http://127.0.0.1:18093';
 const b=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try {
  const p=await b.newPage();
  await p.goto(base);
  await p.locator('.list-group-item').first().waitFor();
  const nulls=await p.evaluate(()=>{const w=document.createTreeWalker(document.querySelector('#app'),NodeFilter.SHOW_TEXT); let n;while(n=w.nextNode())if(n.textContent.trim()==='null')return true;return false;});
  assert.equal(nulls,false);
  await p.route('**/api/v1/lectures/*',async route=>{
   const response=await route.fetch(); const body=await response.json();
   if(body.data?.id){body.data.status='extracting';body.data.media_url=null;}
   await route.fulfill({response,json:body});
  });
  await p.locator('.list-group-item').first().click();
  await p.getByText('Скачивание видео…',{exact:true}).waitFor();
  assert.match(await p.locator('#app').innerText(),/Этап 2 из 5/);
  assert.doesNotMatch(await p.locator('#app').innerText(),/33%/);
  console.log('Browser checks passed: no null pager; download stage; no invented percentage.');
 } finally {await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
