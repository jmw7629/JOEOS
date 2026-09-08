// Executable browser acceptance for the actual reconstructed PROJECT_BYTE app.
// Isolated database and credentials; GitHub execution is deliberately unavailable.
import assert from 'node:assert/strict';
import {verifyFocusedWorkspaces} from './test_focused_workspaces.mjs';
import {prepareObservationFixtures,verifyObservatory} from './test_observatory_browser.mjs';
import {verifyWorkspaceProfile} from './test_workspace_profile_browser.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
const {chromium}=await import(process.env.PB_PLAYWRIGHT_MODULE||'playwright');
const source=path.dirname(fileURLToPath(import.meta.url));
const root=await fs.mkdtemp(path.join(os.tmpdir(),'pb-orbital-browser-'));
const key='TEST_ONLY_ORBITAL_UI_ACCESS_2026';
async function concatenate(folder){const files=(await fs.readdir(path.join(source,folder))).filter(f=>f.endsWith('.part')).sort();return (await Promise.all(files.map(f=>fs.readFile(path.join(source,folder,f),'utf8')))).join('');}
await fs.writeFile(path.join(root,'backend.py'),await concatenate('server'));
await fs.copyFile(path.join(source,'runtime_server.py'),path.join(root,'server.py'));
let html=await concatenate('index');
for(const name of ['home.js','home-inspector.js','health-runtime.js']){await fs.copyFile(path.join(source,name),path.join(root,name));const tag=`<script src="/${name}"></script>`;if(!html.includes(tag))html=html.replace('</body>',tag+'</body>');}
await fs.writeFile(path.join(root,'index.html'),html);
await fs.writeFile(path.join(root,'admin.secret'),key,{mode:0o600});
await fs.mkdir(path.join(root,'bin'));
await fs.writeFile(path.join(root,'bin','gh'),'#!/bin/sh\necho "GitHub disabled in browser acceptance runtime" >&2\nexit 2\n',{mode:0o700});
const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
const base=`http://127.0.0.1:${port}`;
await prepareObservationFixtures(root);
const server=spawn('python3',[path.join(root,'server.py')],{cwd:root,env:{...process.env,HOME:root,PATH:path.join(root,'bin')+path.delimiter+process.env.PATH,KANBAN_HOST:'127.0.0.1',KANBAN_PORT:String(port),PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
let serverLog='',browser;
for(const stream of [server.stdout,server.stderr])stream.on('data',b=>{serverLog=(serverLog+b.toString()).slice(-6000);});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function api(route,method='GET',body){const r=await fetch(base+route,{method,headers:{'X-Access-Key':key,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw new Error(`${route}: ${r.status} ${await r.text()}`);return r.json();}
async function waitServer(){for(let n=0;n<80;n++){try{if((await fetch(base+'/healthz')).ok)return;}catch{}if(server.exitCode!==null)throw new Error(serverLog);await sleep(100);}throw new Error('UI acceptance server did not start');}
try{
  await waitServer();
  const today=new Date().toISOString().slice(0,10);
  const joe=await api('/api/tasks','POST',{title:'Joe launch review — browser fixture',project:'Orbital QA',owner:'Joe',status:'Review',priority:'High',note:'TEST DATA ONLY',due_date:today});
  const mike=await api('/api/tasks','POST',{title:'Mike integration — browser fixture',project:'Orbital QA',owner:'Mike',status:'Blocked',priority:'Critical',note:'TEST DATA ONLY'});
  browser=await chromium.launch({headless:true,...(process.env.PB_BROWSER_PATH?{executablePath:process.env.PB_BROWSER_PATH}:{})});
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!r.url().startsWith(base)&&!r.url().startsWith('data:'))external.push(r.url());});
  page.on('dialog',d=>d.accept(d.type()==='prompt'?key:undefined));
  await page.goto(base,{waitUntil:'networkidle'});
  await page.waitForSelector('#home.active');
  assert.equal(await page.locator('.home-nav button').count(),5,'five primary navigation controls');
  await page.locator('#login').click();
  await page.waitForFunction(()=>document.querySelector('#login').textContent.includes('owner'));
  await page.waitForSelector('#homeAgentMap [data-home-agent="builder"]');
  assert.equal(await page.locator('[data-home-action="create"] .pb-icon').count(),1,'inspector must not erase the New work icon');
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('project-byte-health',{detail:{components:{bridges:{vitros:{state:'healthy',progress_state:'running',execution_state:'running',execution_reason:'active external executor observed; completion pending',active_run_ref:'issue-337',execution_source:'external-bridge',external_elapsed_seconds:60,external_running:true,external_evidence_complete:false}},models:{state:'tested_ok',enabled:1,tested_ok:1}}}})));
  const activeFabric=page.locator('#homeExecutionFabric .fabric-node').filter({hasText:'VITROS builder'});
  assert.equal(await activeFabric.getAttribute('data-state'),'healthy','active execution must not render as unknown');
  assert.match(await activeFabric.textContent(),/Execution: running/);
  assert.match(await activeFabric.textContent(),/completion pending/);
  await page.locator('[data-home-scope="mike"]').click();
  await page.locator('[data-home-scope="critical"]').click();
  assert.equal(await page.locator('#ownerFilter').inputValue(),'Mike','priority must preserve owner');
  assert.equal(await page.locator('#priorityFilter').inputValue(),'Critical');
  assert.match(await page.locator('#homeWorkTitle').textContent(),/Mike/);
  assert.match(await page.locator('#homeWork').textContent(),/Mike integration/);
  assert.ok(!(await page.locator('#homeWork').textContent()).includes('Joe launch'));
  await page.locator('[data-home-kpi="blocked"]').click();
  assert.equal(await page.locator('#ownerFilter').inputValue(),'Mike');
  assert.equal(await page.locator('#statusFilter').inputValue(),'Blocked');
  await page.locator('[data-home-scope="all"]').click();
  assert.equal(await page.locator('#homeKpis [data-kind="due"] b').textContent(),'1','today is inside the due-soon range');
  await page.locator('#homeAgentMap [data-home-agent="builder"]').click();
  await page.waitForSelector('#homeAgentInspector.open');
  await page.evaluate(()=>renderAll());
  assert.ok(await page.locator('#homeAgentMap [data-home-agent="builder"]').evaluate(e=>e.classList.contains('inspecting')),'inspector selection survives render');
  await page.locator('[data-inspector-close]').click();
  await page.locator('[data-home-action="create"]').click();
  await page.waitForSelector('#taskModal.open');
  await page.locator('#taskTitle').fill('New mobile task — browser fixture');
  await page.locator('#taskProject').fill('Orbital QA');
  await page.locator('#taskForm .row button.primary').click();
  await page.waitForSelector('#taskModal.open',{state:'hidden'});
  assert.ok((await api('/api/tasks')).tasks.some(t=>t.title==='New mobile task — browser fixture'),'task really persists');
  await page.locator('[data-home-action="create"]').click();
  await page.keyboard.press('Escape');
  await page.waitForSelector('#taskModal.open',{state:'hidden'});
  for(const view of ['portfolio','board','agents','ai','settings','models','team','terminalView','activity','intelligence','help','home']){
    await page.locator('#pbWorkspaceButton').click();
    await page.locator(`#pbWorkspaces [data-home-go="${view}"]`).click();
    await page.waitForSelector(`#${view}.active`);
    assert.equal(await page.locator('#pbWorkspaces').evaluate(e=>e.open),false,'workspace drawer closes');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`${view} has no page-level horizontal overflow`);
  }
  const out=process.env.PB_SCREENSHOTS||path.join(root,'screenshots');await fs.mkdir(out,{recursive:true});
  await verifyFocusedWorkspaces({page,api,base,out});
  await verifyObservatory({page,api,base,out});
  for(const [width,height] of [[320,740],[390,844],[768,1024],[1440,1000]]){
    await page.setViewportSize({width,height});await page.evaluate(()=>renderAll());await sleep(200);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`Home fits ${width}px`);
    assert.equal(await page.locator('.home-nav button').count(),5);
    const b=await page.locator('.home-nav').boundingBox();assert.ok(b.x>=0&&b.x+b.width<=width+1,'dock fits viewport');
    await page.screenshot({path:path.join(out,`orbital-${width}.png`),fullPage:true});
  }
  await verifyWorkspaceProfile({page,api,root});
  await api('/api/settings','POST',{general:{default_view:'board'}});
  await page.goto(base,{waitUntil:'networkidle'});await page.waitForSelector('#board.active');
  assert.equal(errors.length,0,'no browser exceptions: '+errors.join('; '));
  assert.equal(external.length,0,'no external assets or trackers: '+external.join('; '));
  console.log('BROWSER_HOME_LAYOUT_320_390_768_1440=PASS');console.log('MOBILE_NAV_ALL_12_WORKSPACES=PASS');
  console.log('COMPOSABLE_OWNER_PRIORITY_STATUS_FILTERS=PASS');console.log('LIVE_INSPECTOR_REFRESH=PASS');console.log('TASK_CREATE_PERSISTENCE_AND_MODAL_ESCAPE=PASS');console.log('DEFAULT_VIEW_PREFERENCE=PASS');console.log('NO_EXTERNAL_ASSET_REQUESTS=PASS');console.log('SCREENSHOTS='+out);
} catch(error){console.error('BROWSER_ACCEPTANCE_FAILED',error);console.error(serverLog);process.exitCode=1;
} finally {if(browser)await browser.close();server.kill('SIGTERM');await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);});}
