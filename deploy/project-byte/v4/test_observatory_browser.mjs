import {openDock} from './test_navigation_helpers.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
export async function prepareObservationFixtures(root){
  const start=Date.now()-90000;
  for(const slug of ['jmw7629__stickdeath-byte','jmw7629__vitros-web-dashboard']){
    const dir=path.join(root,'.local/state/joeos-opencode-bridge',slug,'logs');await fs.mkdir(dir,{recursive:true});
    const events=[];
    for(let i=0;i<9;i++)events.push({type:'tool_use',timestamp:start+i*3500,sessionID:i<5?'fixture-main':'fixture-child',part:{id:'fixture-tool-'+i,tool:['read','bash','edit'][i%3],state:{status:i===7?'error':'completed',input:{command:'PRIVATE_BROWSER_SENTINEL',api_key:'ACTUAL_CREDENTIAL_SECRET_123456',filePath:'/private/project/file.ts'},output:'SENSITIVE_RESPONSE_BODY',time:{start:start+i*3500,end:start+i*3500+500+i*20}}}});
    events.push({type:'tool_use',timestamp:start+5000,sessionID:'fixture-main',part:{id:'fixture-task',tool:'task',state:{status:'completed',input:{subagent_type:'explore',prompt:'PRIVATE_PROMPT'},metadata:{sessionId:'fixture-child'},time:{start:start+2000,end:start+30000}}}});
    events.push({type:'step_finish',timestamp:start+40000,sessionID:'fixture-main',part:{id:'fixture-step',tokens:{total:500,input:420,output:80}}});
    await fs.writeFile(path.join(dir,'issue-901.log'),events.map(e=>JSON.stringify(e)).join('\n')+'\n');
  }
}
export async function verifyObservatory({page,api,base,out}){
  const denied=await fetch(base+'/api/observatory');assert.equal(denied.status,403,'public cannot read execution metadata');
  const data=await api('/api/observatory');assert.equal(data.version,1);assert.equal(data.traces.length,2);
  assert.equal(data.capabilities.execution_permissions,false,'no fake executable permissions');
  const raw=JSON.stringify(data);assert.equal(data.capabilities.owner_evidence,true);for(const content of ['PRIVATE_BROWSER_SENTINEL','SENSITIVE_RESPONSE_BODY','PRIVATE_PROMPT'])assert.ok(raw.includes(content));assert.ok(!raw.includes('ACTUAL_CREDENTIAL_SECRET_123456'),'credentials remain redacted');
  await page.locator('#pbWorkspaceButton').click();await page.locator('#pbWorkspaces [data-home-go="agents"]').click();await page.waitForSelector('#agents.active');
  if(await page.locator('#cwRemoteHistory').isVisible() && await page.locator('#cwRemoteHistory').getAttribute('aria-pressed')==='false')await page.locator('#cwRemoteHistory').click();
  await page.locator('#obsRefresh').click();await page.waitForSelector('.obs-node');
  await page.locator('#obsTrace').selectOption(data.traces[0].id);await page.waitForSelector('.obs-node[data-kind="tool"]');
  const id=await page.locator('.obs-node[data-kind="tool"]').first().getAttribute('data-obs-node');
  await page.locator('.obs-node[data-kind="tool"]').first().click();assert.ok(await page.locator('#obsInspector').isVisible());
  await page.locator('[data-obs-mode="treemap"]').click();assert.ok(await page.locator('.obs-block').count()>0);assert.equal(await page.locator(`.obs-block[data-obs-node="${id}"]`).getAttribute('class').then(s=>s.includes('selected')),true,'cross-selection persists into lens');
  await page.locator('#obsMetric').selectOption('tokens');assert.ok(await page.locator('.obs-block').count()>0,'recorded model tokens render');await page.locator('#obsMetric').selectOption('count');
  await page.locator('[data-obs-mode="flow"]').click();assert.ok(await page.locator('.obs-flow path').count()>0,'Sankey uses real tool count edges');
  await page.locator('[data-obs-mode="timeline"]').click();assert.ok(await page.locator('#obsCanvas .obs-table tr').count()>1);assert.match(await page.locator('#obsLegend').textContent(),/0 explicit compaction/);
  await page.locator('[data-obs-tools="timeline"]').click();assert.ok(await page.locator('#obsTools .obs-bar').count()>0);
  await page.locator('[data-obs-tools="matrix"]').click();assert.ok(await page.locator('#obsTools .obs-matrix').count()>0);
  await page.locator('[data-obs-tools="pills"]').click();await page.locator('#obsTools [data-obs-event]').first().click();assert.match(await page.locator('#obsInspector').textContent(),/argument_fields/);
  assert.ok((await page.locator('#obsInspector').textContent()).includes('PRIVATE_BROWSER_SENTINEL'));assert.ok(!(await page.locator('#obsInspector').textContent()).includes('ACTUAL_CREDENTIAL_SECRET_123456'));
  await page.locator('[data-obs-mode="tree"]').click();await page.locator('#obsFit').click();
  await page.locator('#obsCanvas').focus();await page.keyboard.press('ArrowRight');assert.ok(await page.locator('#obsInspector').isVisible(),'keyboard inspection');
  await page.locator('#obsPause').click();assert.equal(await page.locator('#obsPause').textContent(),'Resume live');await page.locator('#obsPause').click();
  await page.locator('#obsFocus').click();assert.ok(await page.locator('#pbObservatory').evaluate(e=>e.classList.contains('obs-focused')));await page.keyboard.press('Escape');assert.ok(!(await page.locator('#pbObservatory').evaluate(e=>e.classList.contains('obs-focused'))));
  for(const [width,height] of [[390,844],[1440,1000]]){
    await page.setViewportSize({width,height});await page.locator('#obsFit').click();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Observatory never causes page overflow');
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(out,`observatory-tree-${width}.png`),fullPage:false});
    await page.locator('[data-obs-mode="treemap"]').click();await page.screenshot({path:path.join(out,`observatory-map-${width}.png`),fullPage:false});await page.locator('[data-obs-mode="tree"]').click();
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('#obsSearch').fill('NOT_A_REAL_TOOL');assert.equal(await page.locator('.obs-node').count(),0,'search filters graph');await page.locator('#obsSearch').fill('');
  assert.equal(await page.locator('main.wrap>.metrics').isVisible(),false,'legacy wall remains removed');
  console.log('OBSERVATORY_AUTH_PAYLOAD_REDACTION_REAL_LOG_ADAPTER=PASS');console.log('OBSERVATORY_TREE_TREEMAP_SANKEY_TIMELINE=PASS');console.log('OBSERVATORY_CROSS_SELECTION_KEYBOARD_ZOOM=PASS');console.log('OBSERVATORY_TOOL_PILLS_SWIMLANES_FREQUENCY_MATRIX=PASS');console.log('OBSERVATORY_MOBILE_DESKTOP_NO_OVERFLOW=PASS');
  await openDock(page);await page.locator('.home-nav [data-home-go="home"]').click();
}
