// Browser regressions for the user's repeated-header screenshot and live crawl request.
import assert from 'node:assert/strict';
import path from 'node:path';
export async function verifyFocusedWorkspaces({page,api,base,out}){
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  async function go(view){await page.locator('#pbWorkspaceButton').click();await page.locator(`#pbWorkspaces [data-home-go="${view}"]`).click();await page.waitForSelector(`#${view}.active`);}
  await page.setViewportSize({width:390,height:844});
  for(const view of ['ai','agents','settings','models','team','terminalView','help','portfolio','board','activity','intelligence']){
    await go(view);
    assert.equal(await page.locator('main.wrap > .metrics').isVisible(),false,`${view}: legacy metric wall must never precede content`);
    assert.equal(await page.locator('#search').isVisible(),false,`${view}: full filters must be collapsed`);
    assert.ok(await page.locator(view==='ai'?'#cwWindowsMenu':'#pbWorkspaceTitle').isVisible(),`${view}: visible workspace navigation`);
    const box=await page.locator(`#${view}`).boundingBox();assert.ok(box.y<220,`${view}: actual content starts on first screen, y=${box.y}`);
    if(view==='ai'){const composer=await page.locator('#chatInput').boundingBox();const dock=await page.locator('#prfktNavDock').boundingBox();assert.ok(composer.y+composer.height<=dock.y+1,`Chat composer visible above reserved dock: ${JSON.stringify({composer,dock})}`);assert.equal(await page.locator('#pbChatContext').evaluate(e=>e.open),false);}
    if(view==='ai')await page.screenshot({path:path.join(out,'chat-focused-390.png'),fullPage:false});
  }
  await go('ai');
  const catalog=await api('/api/codex-workspace');
  assert.equal(catalog.configured,false,'this real runtime fixture deliberately has no native executor');
  assert.equal(catalog.connected,false);
  await page.waitForFunction(()=>document.querySelector('#codexConnection')?.textContent.includes('has not been connected'));
  assert.equal(await page.locator('#pbChatContext').evaluate(element=>element.hidden),true,'owner routing controls are automatic');
  assert.equal(await page.locator('#sendChat').isDisabled(),true,'unconfigured execution cannot submit');
  await page.locator('#chatInput').fill('Keep this unsent owner task draft');
  await page.evaluate(async()=>{document.activeElement?.blur();await refreshWorkspace();});
  assert.equal(await page.locator('#chatInput').inputValue(),'Keep this unsent owner task draft');
  await page.locator('#chatInput').fill('');
  // The legacy context controls belong to collaborators. Exercise them with a
  // real editor session, without inventing a connected native execution API.
  const editor=await api('/api/team','POST',{name:'Focused chat editor fixture',role:'editor'});
  assert.equal(typeof editor.access_key,'string');
  const editorContext=await page.context().browser().newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
  try{
    await editorContext.addInitScript(key=>{sessionStorage.setItem('project_byte_access',key);sessionStorage.setItem('pb_login_at',String(Date.now()));},editor.access_key);
    const editorPage=await editorContext.newPage(),editorErrors=[];
    editorPage.on('pageerror',error=>editorErrors.push(error.message));
    await editorPage.goto(base,{waitUntil:'networkidle'});
    await editorPage.waitForFunction(()=>typeof session!=='undefined'&&session.role==='editor');
    await editorPage.locator('#pbWorkspaceButton').click();
    await editorPage.locator('#pbWorkspaces [data-home-go="ai"]').click();
    await editorPage.waitForSelector('#ai.active');
    assert.equal(await editorPage.locator('#codexWorkspacePanel').isVisible(),false);
    await editorPage.locator('#pbChatContext summary').click();
    await editorPage.locator('#aiProject').selectOption('Orbital QA');
    await editorPage.locator('#aiAgent').selectOption('builder');
    await editorPage.evaluate(async()=>{document.activeElement?.blur();await refreshWorkspace();});
    assert.equal(await editorPage.locator('#aiProject').inputValue(),'Orbital QA','refresh preserves collaborator chat project');
    assert.equal(await editorPage.locator('#aiAgent').inputValue(),'builder','refresh preserves collaborator agent');
    assert.deepEqual(editorErrors,[]);
  }finally{await editorContext.close();}
  await go('board');await page.locator('#pbScopedFilters').click();
  await page.waitForSelector('#pbFilterDialog[open]');assert.equal(await page.locator('#search').isVisible(),true);
  await page.locator('#ownerFilter').selectOption('Mike');await page.locator('#priorityFilter').selectOption('Critical');
  await page.locator('#pbFilterDialog .pb-filter-close').click();assert.match(await page.locator('#pbWorkspaceScope').textContent(),/Mike.*Critical/);
  assert.equal(await page.locator('#search').isVisible(),false);
  await page.locator('#pbScopedFilters').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#pbFilterDialog').evaluate(e=>e.open),false);
  await go('home');await page.locator('[data-home-scope="all"]').click();
  assert.equal(await page.locator('.home-hero').textContent().then(s=>s.includes('Your executive AI command layer')),false,'static mission text removed');
  assert.equal(await page.locator('#pbLiveCrawl').count(),1,'one real live crawl');
  await page.waitForFunction(()=>document.querySelector('#pbLiveCrawl').dataset.state==='fresh');
  await page.emulateMedia({reducedMotion:'no-preference'});
  const track=page.locator('.pb-crawl-track');
  const transform=()=>track.evaluate(e=>getComputedStyle(e).transform);
  await sleep(250);const before=await transform();await sleep(550);assert.notEqual(await transform(),before,'crawl continuously moves');
  await page.locator('#pbCrawlToggle').click();await sleep(100);const stopped=await transform();await sleep(300);assert.equal(await transform(),stopped,'pause control stops crawl');
  await page.locator('#pbCrawlToggle').click();await sleep(300);assert.notEqual(await transform(),stopped,'resume restarts motion');
  assert.equal(await page.locator('#homeCommandInput').count(),0,'Home chat entry removed');
  await go('ai');await page.locator('#chatInput').fill('Do not erase this unsent draft');await go('home');
  const title='Crawl update <img src=x onerror=alert(1)>';
  await api('/api/tasks','POST',{title,project:'Orbital QA',owner:'Mike',status:'Blocked',priority:'High'});
  await page.evaluate(()=>refreshWorkspace());
  await page.waitForFunction(text=>document.querySelector('.pb-crawl-group').textContent.includes(text),title,{timeout:22000});
  assert.equal(await page.locator('#chatInput').inputValue(),'Do not erase this unsent draft','independent live polling cannot reset a focused draft');
  assert.equal(await page.locator('#pbLiveCrawl img').count(),0,'event text must be escaped, never HTML');
  await page.route('**/api/activity',r=>r.abort());
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('#pbLiveCrawl').dataset.state==='stale');
  assert.match(await page.locator('.pb-crawl-group').first().textContent(),/connection interrupted/,'failed reads must not look live');
  await page.unroute('**/api/activity');await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('#pbLiveCrawl').dataset.state==='fresh');
  await page.emulateMedia({reducedMotion:'reduce'});await sleep(100);
  assert.equal(await track.evaluate(e=>getComputedStyle(e).animationName),'none','reduced motion uses readable nonmoving strip');
  await go('ai');await page.locator('#chatInput').fill('');await go('home');
  const jump=page.locator('.pb-crawl-group:not([aria-hidden]) [data-home-go="agents"]').first();
  if(await jump.count()){await jump.evaluate(e=>e.click());await page.waitForSelector('#agents.active');await go('home');}
  console.log('OWNER_UNCONFIGURED_CODEX_AND_DRAFT_REFRESH=PASS');
  console.log('CHAT_COMPOSER_FIRST_COLLABORATOR_CONTEXT_REFRESH_PRESERVED=PASS');
  console.log('NO_REPEATED_HEADER_ALL_11_WORKSPACES=PASS');console.log('ON_DEMAND_FILTERS_AND_WORKSPACE_TITLES=PASS');
  console.log('CONTINUOUS_CRAWL_PAUSE_RESUME_REDUCED_MOTION=PASS');console.log('CRAWL_LIVE_POLL_WHILE_TYPING_DRAFT_PRESERVED=PASS');console.log('CRAWL_FAILURE_TRUTHFUL_AND_EVENT_HTML_ESCAPED=PASS');
}
