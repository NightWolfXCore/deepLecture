const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_API_URL||'http://127.0.0.1:18091';
 const id='11111111-1111-4111-8111-111111111111';
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try {
  const page=await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let summaryCalls=0, fullCalls=0;
  await page.route('**/api/v1/lectures/'+id+'**',async route=>{
   const path=new URL(route.request().url()).pathname;
   if(path.endsWith('/summary/regenerate')){summaryCalls++;return route.fulfill({status:202,json:{data:{id,status:'summarizing',job_id:id}}});}
   if(path.endsWith('/retry'))fullCalls++;
   if(path.endsWith('/transcript'))return route.fulfill({json:{data:{lecture_id:id,segments:[]}}});
   return route.fulfill({json:{data:{id,title:'Test fixture',category:'other',status:'failed',duration_ms:1000,media_url:null,
    created_at:'2026-10-28T12:00:00Z',availability:{transcript:true,search:true,summary:false,terms:false},
    processing:{failed_stage:'summarizing',error_message:'Invalid JSON fixture',can_retry:true,can_regenerate_summary:true,summary_regenerate_available_at:null}}}});
  });
  await page.goto(base+'/?lecture='+id);
  await page.getByRole('button',{name:'Повторить создание конспекта',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Повторить обработку',exact:true}).count(),0);
  await page.getByRole('button',{name:'Повторить создание конспекта',exact:true}).click();
  await page.waitForTimeout(100);
  assert.equal(summaryCalls,1);assert.equal(fullCalls,0);assert.deepEqual(errors,[]);
  console.log('Browser OK: failed summary retries only LLM; no full processing request. Card and POST are explicit fixtures.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
