import {openDock} from './test_navigation_helpers.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';

export async function verifyWorkspaceProfile({page,api,root}){
  const file=path.join(root,'workspace.json');
  const before={session:await api('/api/session'),settings:await api('/api/settings'),tasks:await api('/api/tasks')};
  try{
    await fs.writeFile(file,JSON.stringify({schema_version:1,display_name:'Acme Operations',assistant_name:'Atlas',owner_shortcuts:['Avery','Taylor']}));
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.title==='Acme Operations');
    assert.equal(await page.locator('.home-hero-top h2').textContent(),'Atlas');
    assert.equal(await page.locator('#homeCommandInput').count(),0,'Home chat entry remains removed for custom profiles');
    assert.equal(await page.locator('.agent-node.center').getAttribute('aria-label'),'Open Atlas');
    assert.equal(await page.locator('[data-home-scope="mike"]').count(),0);
    await page.locator('[data-home-scope="person-0"]').click();
    assert.equal(await page.locator('#ownerFilter').inputValue(),'Avery');
    await page.evaluate(()=>refreshWorkspace());
    assert.equal(await page.locator('#ownerFilter').inputValue(),'Avery','configured owner scope survives refresh without matching records');
    await page.locator('#homeScopes [data-home-scope="all"]').click();
    await page.locator('#pbWorkspaceButton').click();
    assert.match(await page.locator('#pbWorkspaces [data-home-go="ai"]').textContent(),/Atlas chat/);
    await page.locator('#pbWorkspaces [data-home-go="ai"]').click();
    assert.equal(await page.locator('#pbWorkspaceTitle').textContent(),'Atlas chat');
    assert.equal(await page.locator('#ai .chatbox h2').first().textContent(),'Atlas');
    await fs.writeFile(file,JSON.stringify({schema_version:1,display_name:'W'.repeat(48),assistant_name:'A'.repeat(48),owner_shortcuts:Array.from({length:6},(_,i)=>String(i).repeat(48))}));
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.title==='W'.repeat(48));
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:900});
      for(const view of ['home','ai','board']){
        if(view==='board'){await page.locator('#pbWorkspaceButton').click();await page.locator('#pbWorkspaces [data-home-go="board"]').click();}
        else {await openDock(page);await page.locator(`.home-nav [data-home-go="${view}"]`).click();}
        if(view==='home'){
          await page.locator('#homeScopes [data-home-scope="all"]').click();
          await page.locator('#homeScopes [data-home-scope="person-0"]').click();
          assert.equal(await page.locator('#ownerFilter').inputValue(),'0'.repeat(48),'long owner is selected at every viewport');
        }
        const layout=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,wide:[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.right>document.documentElement.clientWidth+1;}).slice(0,35).map(el=>({tag:el.tagName,id:el.id,cls:el.className,width:el.getBoundingClientRect().width}))}));
        assert.ok(layout.scroll<=layout.width+1,`custom profile fits ${view} at ${width}px: ${JSON.stringify(layout)}`);
        if(view==='home'){
          assert.ok(await page.locator('.agent-node.center').evaluate(node=>{const n=node.getBoundingClientRect(),b=node.querySelector('b').getBoundingClientRect();return b.left>=n.left&&b.right<=n.right&&b.top>=n.top&&b.bottom<=n.bottom;}),'assistant label stays inside the compact agent node');
          assert.ok(await page.locator('#homeScopes .home-chip').evaluateAll(chips=>chips.every(chip=>chip.getBoundingClientRect().width>=44)),'scrollable owner chips remain usable');
        }
      }
    }
    await openDock(page);await page.locator('.home-nav [data-home-go="home"]').click();
    await page.locator('#homeScopes [data-home-scope="all"]').click();
    await page.setViewportSize({width:320,height:900});
    await page.locator('#login').click();
    await page.waitForFunction(()=>!session.ok);
    assert.equal(await page.locator('#homeCommandInput,#homeCommandSend').count(),0);
    // Normal sign-in remains available after removing the Home composer.
    await page.locator('#login').click();await page.waitForFunction(()=>session.ok);
    await fs.writeFile(file,JSON.stringify({schema_version:1,display_name:'Acme Operations',assistant_name:'Atlas',owner_shortcuts:['Avery','Taylor']}));
    await verifyFirstNotification({page});
    await verifyFirstNotification({page,logoutPending:true});
    await fs.writeFile(file,JSON.stringify({schema_version:1,display_name:'<img src=x onerror=alert(1)>',assistant_name:'<b>Atlas</b>',owner_shortcuts:['<svg onload=alert(1)>']}));
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.title.startsWith('<img'));
    assert.equal(await page.locator('.brand h1 img,.home-hero-top h2 b,.home-chip svg:not(.pb-icon)').count(),0,'profile labels are text, not HTML');
    await fs.writeFile(file,'{"schema_version":99,"api_key":"DO_NOT_EXPOSE"}');
    await page.reload({waitUntil:'networkidle'});
    await page.waitForSelector('#workspaceProfileWarning',{state:'attached'});
    assert.ok(!(await page.locator('body').textContent()).includes('DO_NOT_EXPOSE'));
    assert.equal(await page.locator('.home-hero-top h2').textContent(),'AI_BYTE');
    assert.deepEqual(await api('/api/session'),before.session);
    assert.deepEqual(await api('/api/settings'),before.settings);
    assert.deepEqual(await api('/api/tasks'),before.tasks);
  }finally{
    await fs.rm(file,{force:true});
    await page.reload({waitUntil:'networkidle'});
  }
  assert.equal(await page.title(),'PRFKT_PROJECT');
  console.log('WORKSPACE_PROFILE_DEFAULTS_CUSTOM_LABELS_AUTH_DATA_PRESERVED=PASS');
}

async function verifyFirstNotification({page,logoutPending=false}){
  let release,releaseLogout;
  const delayed=new Promise(resolve=>release=resolve),logoutResponse=new Promise(resolve=>releaseLogout=resolve);
  const originalKey=await page.evaluate(()=>accessKey);
  await page.addInitScript(()=>{
    localStorage.setItem('pb_last_notice','0');window.previewNotifications=[];
    window.Notification=class {static permission='granted';constructor(title){window.previewNotifications.push(title);}};
  });
  await page.route('**/api/notifications',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({notifications:[{id:'profile-notice-fixture',ts:Math.floor(Date.now()/1000),unread:true,title:'Sample notification',message:'Fixture only',severity:'info',link:''}]})}));
  await page.route('**/api/workspace-profile',async route=>{const response=await route.fetch();await delayed;await route.fulfill({response});});
  try{
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Live'));
    assert.deepEqual(await page.evaluate(()=>window.previewNotifications),[],'notification waits for presentation profile');
    if(logoutPending){
      await page.route('**/api/session',async route=>{const response=await route.fetch();await logoutResponse;await route.fulfill({response});});
      await page.locator('#login').click();
      await page.waitForFunction(()=>!accessKey);
    }
    release();
    await page.waitForFunction(()=>window.PROJECT_BYTE_WORKSPACE_READY);
    if(logoutPending){
      assert.deepEqual(await page.evaluate(()=>window.previewNotifications),[],'delayed profile cannot deliver private notices during logout');
      releaseLogout();
      await page.waitForFunction(()=>!session.ok);
    }else{
      await page.waitForFunction(()=>window.previewNotifications.length===1);
      assert.deepEqual(await page.evaluate(()=>window.previewNotifications),['Acme Operations · Sample notification']);
    }
  }finally{
    release();releaseLogout();await page.unroute('**/api/workspace-profile');await page.unroute('**/api/notifications');
    if(logoutPending){await page.unroute('**/api/session');await page.evaluate(key=>sessionStorage.setItem('project_byte_access',key),originalKey);await page.reload({waitUntil:'networkidle'});}
  }
}
