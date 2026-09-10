import {openDock} from './test_navigation_helpers.mjs';
import assert from 'node:assert/strict';
import path from 'node:path';

// Called only by the isolated browser fixture. Never point at a live workspace.
export async function verifyDirectInteraction({browser,api,base,out,key}) {
  const created = await api('/api/tasks','POST',{title:'Grab card — acceptance',project:'Direct interaction QA',status:'Backlog',priority:'Medium',note:'Drag fixture',executor:'ai'});
  const id = created.id, context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',hasTouch:true});
  const page = await context.newPage(), writes = [], errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push({url:r.url(),method:r.method(),body:r.postData()});});
  const task = () => page.locator('[data-task-id="'+id+'"]');
  async function scrollToTask(){
    // Live snapshots may replace an idle card between locator resolution and scrolling.
    for(let attempt=0;attempt<3;attempt++){try{await task().scrollIntoViewIfNeeded();return;}catch(error){if(attempt===2||!error.message.includes('not attached'))throw error;await page.waitForTimeout(100);}}
  }
  const column = status => page.locator('[data-drop-status="'+status+'"]');
  const current = async() => (await api('/api/tasks')).tasks.find(t=>t.id===id).status;
  async function ready() {
    await page.evaluate(async()=>{await load();applyCriteria({projectFilter:'Direct interaction QA'});document.querySelector('[data-view="board"]').click();});
    await task().waitFor();
  }
  async function start() {
    await page.waitForFunction(()=>document.getElementById('boardGrid').getAttribute('aria-busy')!=='true');
    await scrollToTask();const b=await task().locator('.task-grip').boundingBox();
    await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();
    await page.mouse.move(b.x+b.width/2+12,b.y+b.height/2+12,{steps:3});
    await page.locator('.prfkt-drag-ghost').waitFor();
  }
  async function drop(status) {
    const b=await column(status).boundingBox();await page.mouse.move(b.x+b.width/2,Math.max(b.y+100,180),{steps:12});await page.mouse.up();
  }
  async function waitStatus(status) {
    await page.waitForFunction(({id,status})=>tasks.find(t=>t.id===id)?.status===status,{id,status});
    assert.equal(await current(),status);
    await page.waitForFunction(()=>document.getElementById('boardGrid').getAttribute('aria-busy')==='false');
  }
  try {
    await page.goto(base,{waitUntil:'networkidle'});
    // Read-only users can open cards but cannot pick them up.
    await ready(); assert.equal(await task().locator('.task-grip').isDisabled(),true);
    await task().locator('.task-title').click();await page.locator('#taskModal.open').waitFor();await page.keyboard.press('Escape');
    page.once('dialog',d=>d.accept(key));await page.locator('#login').click();
    await page.waitForFunction(()=>session.level===4);await ready();
    await start();await drop('Active');await waitStatus('Active');
    assert.deepEqual(JSON.parse(writes.find(w=>w.method==='PATCH').body),{status:'Active'},'move only changes status');
    await page.locator('.prfkt-move-toast button',{hasText:'Undo'}).click();await waitStatus('Backlog');
    await page.reload({waitUntil:'networkidle'});await ready();assert.equal(await current(),'Backlog');
    // Escape and a drop outside the board must not save.
    let before=writes.length;await start();await page.keyboard.press('Escape');await page.mouse.up();
    assert.equal(writes.length,before);assert.equal(await page.locator('.prfkt-drag-ghost').count(),0);
    await start();await page.mouse.move(10,10);await page.mouse.up();assert.equal(await current(),'Backlog');assert.equal(writes.length,before);
    // Keyboard movement and Undo both persist.
    await task().locator('.task-grip').focus();await page.keyboard.press('Space');await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');await page.keyboard.press('Enter');await waitStatus('Blocked');
    await page.locator('.prfkt-move-toast button',{hasText:'Undo'}).click();await waitStatus('Backlog');
    // A rejected write leaves the real task in its original column.
    await page.route('**/api/tasks/'+id,route=>route.request().method()==='PATCH'?route.fulfill({status:403,contentType:'application/json',body:'{"error":"Fixture access revoked"}'}):route.continue());
    await start();await drop('Active');await page.getByRole('status').filter({hasText:'Move not saved.'}).waitFor();
    assert.equal(await current(),'Backlog');assert.equal(await column('Backlog').locator('[data-task-id="'+id+'"]').count(),1);
    await page.unroute('**/api/tasks/'+id);
    // Refresh waits during a grab; an explicit re-render cancels safely.
    await start();assert.equal(await page.evaluate(()=>userIsEditing()),true);await page.evaluate(()=>renderBoard());
    await page.mouse.up();assert.equal(await page.locator('.prfkt-drag-ghost').count(),0);assert.equal(await current(),'Backlog');
    // If another user changed the status, neither drop nor Undo overwrites it.
    await start();await api('/api/tasks/'+id,'PATCH',{status:'Review'});await drop('Active');
    await page.getByRole('status').filter({hasText:'changed since you picked it up'}).waitFor();assert.equal(await current(),'Review');
    await api('/api/tasks/'+id,'PATCH',{status:'Backlog'});await ready();
    await start();await drop('Active');await waitStatus('Active');await api('/api/tasks/'+id,'PATCH',{status:'Review'});
    await page.locator('.prfkt-move-toast button',{hasText:'Undo'}).click();
    await page.getByRole('status').filter({hasText:'changed since you picked it up'}).waitFor();assert.equal(await current(),'Review');
    await api('/api/tasks/'+id,'PATCH',{status:'Backlog'});await ready();
    // Narrow screens: drag empty board space; swipes on card bodies remain native scroll.
    await page.setViewportSize({width:390,height:844});await ready();
    const scroll=page.locator('.boardscroll');await scroll.evaluate(e=>e.scrollLeft=0);
    await column('Backlog').locator('.colhead').scrollIntoViewIfNeeded();
    const head=await column('Backlog').locator('.colhead').boundingBox();
    await page.mouse.move(head.x+260,head.y+15);await page.mouse.down();await page.mouse.move(head.x+20,head.y+15,{steps:8});await page.mouse.up();
    assert.ok(await scroll.evaluate(e=>e.scrollLeft)>100,'empty board grabs pan horizontally');
    await scroll.evaluate(e=>e.scrollLeft=0);await scrollToTask();
    const cdp=await context.newCDPSession(page);
    const note=await task().locator('.tasknote').boundingBox();
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:note.x+30,y:note.y+10}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:note.x+30,y:note.y-30}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    assert.equal(await page.locator('.prfkt-drag-ghost').count(),0,'card body touch scroll does not start a move');
    await scrollToTask();const grip=await task().locator('.task-grip').boundingBox();
    const x=grip.x+20,y=grip.y+20;
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+10,y:y+15}]});
    await page.locator('.prfkt-drag-ghost').waitFor();
    // Hold near the right edge until Active has scrolled into view.
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:375,y:y+15}]});
    await page.waitForFunction(()=>document.querySelector('.boardscroll').scrollLeft>160);
    const active=await column('Active').boundingBox();const destinationX=Math.min(330,Math.max(80,active.x+active.width/2));
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:destinationX,y:y+15}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await waitStatus('Active');
    assert.equal(await page.locator('.prfkt-drag-ghost').count(),0);
    // Phone users can move by tapping, without holding a card across columns.
    const tabs=page.getByRole('navigation',{name:'Kanban columns'});
    await tabs.locator('[data-column="Done"]').click();
    await page.waitForFunction(()=>document.querySelector('[data-column="Done"]').getAttribute('aria-pressed')==='true');
    await tabs.locator('[data-column="Active"]').click();await scrollToTask();
    const mobileGrip=task().locator('.task-grip');const size=await mobileGrip.boundingBox();assert.ok(size.width>=44&&size.height>=44);
    await mobileGrip.tap();const sheet=page.locator('.prfkt-move-sheet[open]');await sheet.waitFor();
    assert.equal(await sheet.locator('[data-move-status="Active"]').isDisabled(),true);
    assert.equal(await page.evaluate(()=>userIsEditing()),true,'open sheet defers refresh');
    await page.keyboard.press('Escape');await page.locator('.prfkt-move-sheet[open]').waitFor({state:'hidden'});assert.equal(await current(),'Active');
    await mobileGrip.tap();await page.locator('[data-move-status="Review"]').tap();await waitStatus('Review');
    assert.equal(await tabs.locator('[data-column="Review"]').getAttribute('aria-pressed'),'true','saved move follows destination column');
    await page.locator('.prfkt-move-toast button',{hasText:'Undo'}).tap();await waitStatus('Active');
    for(const width of [320,390,768,1440]) {
      await page.setViewportSize({width,height:900});await page.screenshot({path:path.join(out,'kanban-grab-'+width+'.png')});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no page overflow at '+width);
      if(width<=650){
        await scrollToTask();await task().locator('.task-grip').tap();await page.locator('.prfkt-move-sheet[open]').waitFor();
        const bounds=await page.locator('.prfkt-move-sheet').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1&&bounds.y>=0&&bounds.y+bounds.height<=901);
        await page.screenshot({path:path.join(out,'kanban-move-sheet-'+width+'.png')});await page.getByRole('button',{name:'Close move menu'}).tap();
      }
    }
    await page.setViewportSize({width:844,height:390});await page.waitForSelector('.task-grip[aria-haspopup=dialog]');await task().locator('.task-grip').tap();
    await page.locator('.prfkt-move-sheet[open]').waitFor();const landscape=await page.locator('.prfkt-move-sheet').boundingBox();
    assert.ok(landscape.y>=0&&landscape.y+landscape.height<=391,'landscape sheet stays within viewport and scrolls');
    await page.screenshot({path:path.join(out,'kanban-move-sheet-landscape.png')});await page.getByRole('button',{name:'Close move menu'}).tap();
    await page.setViewportSize({width:390,height:844});await openDock(page);await page.locator('.home-nav [data-home-go="home"]').click();
    const scopes=page.locator('#homeScopes');await scopes.scrollIntoViewIfNeeded();await scopes.evaluate(e=>e.scrollLeft=0);
    const scopeBefore=await page.evaluate(()=>filterCriteria());const strip=await scopes.boundingBox();
    await page.mouse.move(strip.x+strip.width-35,strip.y+20);await page.mouse.down();await page.mouse.move(strip.x+35,strip.y+20,{steps:10});await page.mouse.up();
    assert.ok(await scopes.evaluate(e=>e.scrollLeft)>80,'scope chips support mouse grab-to-pan');
    assert.deepEqual(await page.evaluate(()=>filterCriteria()),scopeBefore,'drag does not activate a filter');
    assert.ok(!writes.some(w=>/\/queue|\/runs|\/codex-workspace/.test(w.url)),'no execution launched by direct manipulation');
    assert.deepEqual(errors,[]);console.log('DIRECT_INTERACTION_POINTER_TOUCH_KEYBOARD_UNDO_FAILURE_PERMISSIONS=PASS');
  } catch(error) {await page.screenshot({path:path.join(out,'kanban-failure.png')});console.error('INTERACTION_FAILURE_STATE',await page.locator('.prfkt-move-toast').textContent(),await current());throw error;} finally {await context.close();await api('/api/tasks/'+id,'DELETE');}
}
