import assert from 'node:assert/strict';

export async function verifyExternalReviewLogout({page}){
  const title='LATE_EXTERNAL_REVIEW_TEST_FIXTURE';
  let release,arrived,handled,finished,pendingRequest;
  const requested=new Promise(resolve=>arrived=resolve);
  const routeHandled=new Promise(resolve=>handled=resolve);
  const requestFinished=new Promise(resolve=>finished=resolve);
  const onFinished=request=>{if(request===pendingRequest)finished();};
  page.on('requestfinished',onFinished);page.on('requestfailed',onFinished);
  async function bounded(promise){
    let timer;
    try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('review request did not settle')),10000))]);}
    finally{clearTimeout(timer);}
  }
  await page.route('**/api/external-reviews',async route=>{
    assert.ok(route.request().headers()['x-access-key'],'request starts authenticated');
    pendingRequest=route.request();
    arrived();await new Promise(resolve=>release=resolve);
    try{await route.fulfill({contentType:'application/json',body:JSON.stringify({state:'fresh',repositories:{},items:[{
      project:'DASH_BYTE / VITROS',repo:'jmw7629/vitros-web-dashboard',number:999,title,
      url:'https://github.com/jmw7629/vitros-web-dashboard/pull/999',checks:{},evidence_state:'fresh'
    }]})});}catch(error){
      // An aborted old request may already have been discarded by the browser.
      if(!/closed|cancel|abort|invalid interception/i.test(error.message))throw error;
    }finally{handled();}
  });
  try{
    await page.reload({waitUntil:'domcontentloaded'});
    await bounded(requested);
    await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Live'));
    await page.locator('#login').click();
    await page.waitForFunction(()=>session.level===0&&!accessKey);
    release();
    await bounded(Promise.all([routeHandled,requestFinished]));
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('.external-review').count(),0,'logout must discard late external cards');
    assert.ok(!(await page.locator('#pbLiveCrawl').textContent()).includes(title),'logout must discard external crawl evidence');
  }finally{
    release?.();await page.unroute('**/api/external-reviews');
    page.off('requestfinished',onFinished);page.off('requestfailed',onFinished);
  }
  await page.locator('#login').click();
  await page.waitForFunction(()=>session.level>=2);
  console.log('EXTERNAL_REVIEW_LATE_RESPONSE_AFTER_REAL_LOGOUT=PASS');
}
