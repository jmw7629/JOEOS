import assert from 'node:assert/strict';
import path from 'node:path';

// UI-only protocol fixtures. Actual runner execution is verified by the Python proof.
export async function verifyExecutionPermissions({page,api,base,out}){
  assert.equal((await fetch(base+'/api/execution-permissions')).status,403);
  assert.equal((await fetch(base+'/api/execution-permissions/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,403);
  assert.equal((await api('/api/execution-permissions')).state,'not_connected');
  let state='pending',mode='ok',release=null,arrived=null,settled=null,pendingRequest=null,finished=null;
  const onFinished=request=>{if(request===pendingRequest)finished?.();};
  page.on('requestfinished',onFinished);page.on('requestfailed',onFinished);
  async function bounded(promise){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('Permission fixture request did not settle')),10000))]);}finally{clearTimeout(timer);}}
  const decisions=[];
  const handle='a'.repeat(64),token='b'.repeat(64);
  const fixture=()=>({state:'connected',repository:'jmw7629/JOEOS',session_id:'ses_BROWSER_TEST_ONLY',server_time:Date.now()/1000,
    requests:[{handle,review_token:token,state,expires_at:Date.now()/1000+(state==='expired'?-1:120),affected_count:2,
      request:{permission:'bash',patterns:['printf harmless-test'],metadata:{command:'<img src=x onerror="window.PERMISSION_XSS=true">'},tool:{callID:'call_browser_test',messageID:'msg_browser_test'}}}],history:[]});
  await page.route('**/api/execution-permissions',async route=>{
    if(mode==='offline'){await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({state:'unavailable',error:'Runner unreachable',requests:[],history:[{ts:Date.now()/1000,decision:'deny_session',result:'unknown',note:'Audit survives outage'}]})});return;}
    if(mode==='delay'){pendingRequest=route.request();arrived?.();await new Promise(resolve=>release=resolve);}
    try{await route.fulfill({contentType:'application/json',body:JSON.stringify(fixture())});}
    catch(error){if(!/closed|cancel|abort|invalid interception/i.test(error.message))throw error;}
    finally{if(route.request()===pendingRequest)settled?.();}
  });
  await page.route('**/api/execution-permissions/decision',async route=>{
    decisions.push(route.request().postDataJSON());
    assert.ok(route.request().headers()['x-access-key']);
    state=mode==='unknown'?'unknown':decisions.at(-1).decision==='deny_session'?'rejected':'accepted';
    await route.fulfill({contentType:'application/json',body:JSON.stringify({state,message:state==='unknown'?'Runner reply is uncertain. Do not retry; inspect the runner.':'Runner accepted the permission decision. Tool completion is separate.'})});
  });
  const panel=page.locator('#pbObservatory .obs-approval-note');
  try{
    await page.goto(base+'/#agents',{waitUntil:'domcontentloaded'});
    await page.waitForSelector('#agents.active');
    await panel.getByRole('button',{name:'Refresh',exact:true}).click();
    await panel.getByRole('button',{name:'Approve once',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>!!window.PERMISSION_XSS),false,'request metadata is escaped');
    assert.equal(await panel.locator('img').count(),0);
    for(const [width,height] of [[320,740],[390,844],[768,1024],[1440,1000]]){
      await page.setViewportSize({width,height});
      // Live polling can replace the controls during a viewport change. Wait for
      // both visible actions and capture one layout snapshot without stale locators.
      const layout=await page.waitForFunction(()=>{
        const buttons=[...document.querySelectorAll('#pbObservatory .obs-approval-note [data-permission-decision]')];
        if(buttons.length!==2)return false;
        const bounds=buttons.map(button=>button.getBoundingClientRect());
        if(bounds.some(box=>box.width<=0||box.height<=0))return false;
        return bounds.map(box=>({x:box.x,width:box.width}));
      });
      const bounds=await layout.jsonValue();await layout.dispose();
      for(const box of bounds)assert.ok(box.x>=0&&box.x+box.width<=width+1,'permission action fits '+width);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
      await panel.screenshot({path:path.join(out,`permissions-fixture-${width}.png`)});
    }
    await panel.locator('textarea').fill('Private test note');
    const noteRefresh=page.waitForResponse(response=>response.url()===base+'/api/execution-permissions');
    await panel.getByRole('button',{name:'Refresh',exact:true}).click();
    await (await noteRefresh).finished();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.waitForFunction(()=>document.querySelector('[data-permission-note]')?.value==='Private test note');
    await panel.getByRole('button',{name:'Approve once',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.permission-card b')?.textContent.includes('accepted'));
    assert.equal(decisions.length,1);assert.deepEqual(decisions[0],{handle,review_token:token,decision:'approve_once',message:'Private test note'});
    assert.equal(await panel.locator('[data-permission-decision]').count(),0);
    state='pending';mode='unknown';await panel.getByRole('button',{name:'Refresh',exact:true}).click();
    await panel.getByRole('button',{name:'Deny session’s pending tools',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.permission-card b')?.textContent.includes('unknown'));
    assert.equal(await panel.locator('[data-permission-decision]').count(),0);
    assert.equal(decisions[1].decision,'deny_session');
    mode='offline';await panel.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.permission-feedback')?.textContent.includes('Runner unreachable'));
    assert.ok((await panel.textContent()).includes('Do not retry'));
    assert.ok((await panel.textContent()).includes('Audit survives outage'));
    assert.equal(await panel.locator('[data-permission-decision]').count(),0);
    mode='ok';state='expired';await panel.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.permission-card b')?.textContent.includes('expired'));
    assert.equal(await panel.locator('[data-permission-decision]').count(),0);
    // A delayed queue response must not restore tool details or controls after actual logout.
    mode='delay';state='pending';const requested=new Promise(resolve=>arrived=resolve);
    const routeSettled=new Promise(resolve=>settled=resolve),requestSettled=new Promise(resolve=>finished=resolve);
    await panel.getByRole('button',{name:'Refresh',exact:true}).click();await bounded(requested);
    await page.locator('#login').click();await page.waitForFunction(()=>session.level===0&&!accessKey);
    release();mode='ok';
    await bounded(Promise.all([routeSettled,requestSettled]));
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.waitForFunction(()=>!document.querySelector('.permission-card'));
    assert.ok(!(await panel.textContent()).includes('call_browser_test'));
    assert.equal(decisions.length,2);
  }finally{
    release?.();page.off('requestfinished',onFinished);page.off('requestfailed',onFinished);await page.unroute('**/api/execution-permissions');await page.unroute('**/api/execution-permissions/decision');
  }
  await page.locator('#login').click();await page.waitForFunction(()=>session.level===4);
  console.log('PERMISSION_OWNER_AUTH_ACTIONS_EXPIRY_AMBIGUITY_LOGOUT_AND_4_WIDTHS=PASS');
}
