import assert from 'node:assert/strict';
import path from 'node:path';
export async function verifyCollapsibleNavigation({browser,base,out,key}){
 const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,reducedMotion:'reduce'});
 await context.addInitScript(k=>{sessionStorage.setItem('project_byte_access',k);localStorage.setItem('prfkt.updates.popups','off');},key);
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>session.ok);
 const nav=page.locator('.home-nav'),toggle=page.locator('#prfktNavToggle'),main=page.locator('body > main.wrap'),dock=page.locator('#prfktNavDock');
 for(const [width,height] of [[320,740],[390,844],[844,390],[1440,1000]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(150);
  assert.equal(await nav.isVisible(),false,'navigation starts tucked away');
  const closed=await main.boundingBox();await toggle.tap();await nav.waitFor();
  const opened=await main.boundingBox(),bar=await nav.boundingBox(),bottom=await dock.boundingBox();
  assert.ok(closed.height-opened.height>=bar.height,'workspace shrinks by the revealed navigation height');
  assert.ok(opened.y+opened.height<=bottom.y+1,'content viewport ends before navigation');assert.ok(bottom.y+bottom.height<=height+1,'dock fits visible screen');
  assert.ok(bar.x>=0&&bar.x+bar.width<=width+1,'all five destinations fit');
  await main.evaluate(e=>e.scrollTop=e.scrollHeight);await page.screenshot({path:path.join(out,'navigation-open-'+width+'.png')});
  assert.ok(await main.evaluate(e=>e.scrollTop+e.clientHeight>=e.scrollHeight-2),'last content remains reachable above navigation');
  await toggle.tap();assert.equal(await nav.isVisible(),false);await main.evaluate(e=>e.scrollTop=0);
  await page.screenshot({path:path.join(out,'navigation-closed-'+width+'.png')});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 }
 await toggle.focus();await page.keyboard.press('Enter');await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.closest('.home-nav')!==null),true);
 await page.keyboard.press('Escape');assert.equal(await nav.isVisible(),false);assert.equal(await toggle.evaluate(e=>e===document.activeElement),true);
 for(const view of ['portfolio','agents','ai','settings','home']){await toggle.click();await nav.locator('[data-home-go="'+view+'"]').click();await page.locator('#'+view+'.active').waitFor();assert.equal(await nav.isVisible(),false,'choosing destination tucks dock away');}
 await page.evaluate(()=>{openTask();document.querySelector('#taskTitle').value='Untouched navigation draft';});await page.evaluate(()=>closeModal('taskModal'));
 await toggle.click();await toggle.click();assert.equal(await page.locator('#taskTitle').inputValue(),'Untouched navigation draft');
 await page.reload({waitUntil:'domcontentloaded'});await page.waitForSelector('#prfktNavToggle');assert.equal(await nav.isVisible(),false,'reload starts discreet');
 assert.deepEqual(errors,[]);await context.close();console.log('COLLAPSIBLE_NAV_HIDDEN_RESIZE_NO_OVERLAP_FOUR_WIDTHS_TOUCH_KEYBOARD_DESTINATIONS=PASS');
}
